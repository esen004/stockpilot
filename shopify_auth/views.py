import hashlib
import hmac
import json
import secrets
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.models import Shop


def _verify_hmac(query_params: dict, secret: str) -> bool:
    """Verify Shopify HMAC signature on OAuth callback."""
    params = dict(query_params)
    received_hmac = params.pop("hmac", None)
    if not received_hmac:
        return False
    sorted_params = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    computed = hmac.new(
        secret.encode("utf-8"),
        sorted_params.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(computed, received_hmac)


def install(request):
    """Step 1: Merchant clicks Install -> redirect to Shopify OAuth consent screen."""
    shop = request.GET.get("shop", "").strip()
    if not shop or not shop.endswith(".myshopify.com"):
        return HttpResponseBadRequest("Missing or invalid shop parameter")

    nonce = secrets.token_urlsafe(32)
    request.session["shopify_nonce"] = nonce
    request.session["shopify_shop"] = shop

    params = {
        "client_id": settings.SHOPIFY_API_KEY,
        "scope": ",".join(settings.SHOPIFY_API_SCOPES),
        "redirect_uri": f"{settings.SHOPIFY_APP_URL}/auth/callback",
        "state": nonce,
    }
    auth_url = f"https://{shop}/admin/oauth/authorize?{urlencode(params)}"
    return redirect(auth_url)


def callback(request):
    """Step 2: Shopify redirects back with auth code -> exchange for access token."""
    shop = request.GET.get("shop", "")
    code = request.GET.get("code", "")
    state = request.GET.get("state", "")

    if not shop or not code:
        return HttpResponseBadRequest("Missing shop or code")

    # Verify HMAC (most important security check)
    if not _verify_hmac(dict(request.GET.items()), settings.SHOPIFY_API_SECRET):
        return HttpResponseBadRequest("Invalid HMAC")

    # Nonce check — skip if session was lost (Render free tier cold starts)
    stored_nonce = request.session.get("shopify_nonce")
    if stored_nonce and state != stored_nonce:
        return HttpResponseBadRequest("Invalid state/nonce")

    # Exchange code for access token
    token_url = f"https://{shop}/admin/oauth/access_token"
    resp = requests.post(
        token_url,
        json={
            "client_id": settings.SHOPIFY_API_KEY,
            "client_secret": settings.SHOPIFY_API_SECRET,
            "code": code,
        },
        timeout=15,
    )
    if resp.status_code != 200:
        return HttpResponseBadRequest(f"Token exchange failed: {resp.text}")

    token_data = resp.json()
    access_token = token_data.get("access_token", "")

    # Save or update shop
    shop_obj, created = Shop.objects.update_or_create(
        shopify_domain=shop,
        defaults={
            "access_token": access_token,
            "is_active": True,
            "uninstalled_at": None,
        },
    )

    # Fetch basic store info
    try:
        _fetch_store_info(shop_obj)
    except Exception:
        pass

    # Register mandatory webhooks
    try:
        _register_webhooks(shop_obj)
    except Exception:
        pass

    # Clear session nonce
    request.session.pop("shopify_nonce", None)
    request.session.pop("shopify_shop", None)

    # Store shop ID in session for embedded app
    request.session["shop_id"] = shop_obj.id

    # Redirect to app inside Shopify admin
    return redirect(f"https://{shop}/admin/apps/{settings.SHOPIFY_API_KEY}")


def manual_setup(request):
    """Emergency setup — re-register shop after DB wipe.
    Uninstall and reinstall the app from Shopify admin to trigger fresh OAuth.
    OR use this with ?shop=xxx&token=xxx to manually insert.
    """
    import traceback
    shop_domain = request.GET.get("shop", "")
    token = request.GET.get("token", "")

    if not shop_domain:
        # Show a form
        return HttpResponse("""
            <h2>Manual Shop Setup</h2>
            <p>The database was reset. To fix this:</p>
            <ol>
                <li>Go to your Shopify admin → Settings → Apps</li>
                <li>Uninstall StockPilot</li>
                <li>Then reinstall from: <a href="/auth/install?shop=stockpdev.myshopify.com">/auth/install?shop=stockpdev.myshopify.com</a></li>
            </ol>
            <hr>
            <p>Or if you have the access token, enter it below:</p>
            <form method="get">
                <label>Shop domain: <input name="shop" value="stockpdev.myshopify.com"></label><br><br>
                <label>Access token: <input name="token" size="60"></label><br><br>
                <button type="submit">Save</button>
            </form>
        """)

    if token:
        try:
            shop_obj, created = Shop.objects.update_or_create(
                shopify_domain=shop_domain,
                defaults={
                    "access_token": token,
                    "is_active": True,
                    "uninstalled_at": None,
                },
            )
            try:
                _fetch_store_info(shop_obj)
            except Exception:
                pass

            request.session["shop_id"] = shop_obj.id
            return HttpResponse(
                f"<h2>Shop saved!</h2>"
                f"<p>{shop_obj.shopify_domain} — {shop_obj.store_name}</p>"
                f"<p><a href='/?shop={shop_domain}'>Go to Dashboard</a></p>"
                f"<p><a href='/sync?shop={shop_domain}'>Sync Products</a></p>"
            )
        except Exception as e:
            return HttpResponse(f"<pre>Error: {e}\n{traceback.format_exc()}</pre>")

    # No token — try to get it via OAuth
    return redirect(f"/auth/install?shop={shop_domain}")


def _fetch_store_info(shop_obj):
    query = """{ shop { name email currencyCode } }"""
    data = _graphql(shop_obj, query)
    if data and "shop" in data:
        s = data["shop"]
        shop_obj.store_name = s.get("name", "")
        shop_obj.store_email = s.get("email", "")
        shop_obj.currency = s.get("currencyCode", "USD")
        shop_obj.save(update_fields=["store_name", "store_email", "currency"])


def _register_webhooks(shop_obj):
    webhooks = [
        ("APP_UNINSTALLED", f"{settings.SHOPIFY_APP_URL}/webhooks/app-uninstalled"),
        ("CUSTOMERS_DATA_REQUEST", f"{settings.SHOPIFY_APP_URL}/webhooks/customers-data-request"),
        ("CUSTOMERS_REDACT", f"{settings.SHOPIFY_APP_URL}/webhooks/customers-redact"),
        ("SHOP_REDACT", f"{settings.SHOPIFY_APP_URL}/webhooks/shop-redact"),
    ]
    for topic, address in webhooks:
        mutation = """
        mutation webhookCreate($topic: WebhookSubscriptionTopic!, $webhookSubscription: WebhookSubscriptionInput!) {
            webhookSubscriptionCreate(topic: $topic, webhookSubscription: $webhookSubscription) {
                webhookSubscription { id }
                userErrors { field message }
            }
        }
        """
        _graphql(shop_obj, mutation, {
            "topic": topic,
            "webhookSubscription": {
                "callbackUrl": address,
                "format": "JSON",
            },
        })


def _graphql(shop_obj, query, variables=None):
    url = f"https://{shop_obj.shopify_domain}/admin/api/{settings.SHOPIFY_API_VERSION}/graphql.json"
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    resp = requests.post(
        url,
        json=payload,
        headers={
            "X-Shopify-Access-Token": shop_obj.access_token,
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    if resp.status_code == 200:
        result = resp.json()
        return result.get("data")
    return None


# --- Webhook Handlers ---

def _verify_webhook_hmac(request) -> bool:
    secret = settings.SHOPIFY_API_SECRET.encode("utf-8")
    digest = hmac.new(secret, request.body, hashlib.sha256).digest()
    import base64
    computed = base64.b64encode(digest).decode("utf-8")
    received = request.headers.get("X-Shopify-Hmac-Sha256", "")
    return hmac.compare_digest(computed, received)


@csrf_exempt
@require_POST
def webhook_app_uninstalled(request):
    if not _verify_webhook_hmac(request):
        return HttpResponse(status=401)
    try:
        domain = request.headers.get("X-Shopify-Shop-Domain", "")
        if domain:
            from django.utils import timezone
            Shop.objects.filter(shopify_domain=domain).update(
                is_active=False, uninstalled_at=timezone.now(),
            )
    except Exception:
        pass
    return HttpResponse(status=200)


@csrf_exempt
@require_POST
def webhook_customers_data_request(request):
    if not _verify_webhook_hmac(request):
        return HttpResponse(status=401)
    return HttpResponse(status=200)


@csrf_exempt
@require_POST
def webhook_customers_redact(request):
    if not _verify_webhook_hmac(request):
        return HttpResponse(status=401)
    return HttpResponse(status=200)


@csrf_exempt
@require_POST
def webhook_shop_redact(request):
    if not _verify_webhook_hmac(request):
        return HttpResponse(status=401)
    try:
        domain = request.headers.get("X-Shopify-Shop-Domain", "")
        if domain:
            Shop.objects.filter(shopify_domain=domain).delete()
    except Exception:
        pass
    return HttpResponse(status=200)
