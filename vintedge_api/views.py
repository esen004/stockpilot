import logging
import os
from datetime import timedelta

import stripe
from django.core.cache import cache
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import VintEdgeSubscriber

logger = logging.getLogger(__name__)

stripe.api_key = os.environ.get("STRIPE_API_KEY", "")
WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")


def _cors_json(data, status=200):
    """Return a JsonResponse with CORS headers for Chrome extension access."""
    response = JsonResponse(data, status=status)
    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response["Access-Control-Allow-Headers"] = "Content-Type"
    return response


# ---------------------------------------------------------------------------
# POST /vintedge/webhook — Stripe webhook receiver
# ---------------------------------------------------------------------------

@csrf_exempt
@require_POST
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")

    if not WEBHOOK_SECRET:
        logger.error("STRIPE_WEBHOOK_SECRET is not configured")
        return HttpResponse("Webhook secret not configured", status=500)

    # Verify signature
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
    except ValueError:
        logger.warning("Invalid webhook payload")
        return HttpResponse("Invalid payload", status=400)
    except stripe.error.SignatureVerificationError:
        logger.warning("Invalid webhook signature")
        return HttpResponse("Invalid signature", status=400)

    event_type = event["type"]
    data_obj = event["data"]["object"]

    logger.info("Stripe webhook received: %s", event_type)

    try:
        if event_type == "checkout.session.completed":
            _handle_checkout_completed(data_obj)

        elif event_type == "customer.subscription.created":
            _handle_subscription_update(data_obj, active=True)

        elif event_type == "customer.subscription.updated":
            _handle_subscription_update(
                data_obj,
                active=data_obj.get("status") in ("active", "trialing"),
            )

        elif event_type == "customer.subscription.deleted":
            _handle_subscription_update(data_obj, active=False)

        elif event_type == "invoice.payment_succeeded":
            _handle_invoice_paid(data_obj)

        elif event_type == "invoice.payment_failed":
            _handle_invoice_failed(data_obj)

        else:
            logger.info("Unhandled event type: %s", event_type)

    except Exception:
        logger.exception("Error processing webhook event %s", event_type)
        # Return 200 anyway so Stripe doesn't retry endlessly
        return HttpResponse("Error logged", status=200)

    return HttpResponse("OK", status=200)


def _resolve_email(obj):
    """Extract customer email from a Stripe object."""
    email = obj.get("customer_email") or obj.get("customer_details", {}).get("email")
    if not email:
        # Try to fetch from Stripe Customer object
        customer_id = obj.get("customer")
        if customer_id:
            try:
                customer = stripe.Customer.retrieve(customer_id)
                email = customer.get("email")
            except Exception:
                logger.warning("Could not retrieve customer %s", customer_id)
    return email.lower().strip() if email else None


def _handle_checkout_completed(session):
    email = _resolve_email(session)
    if not email:
        logger.warning("checkout.session.completed with no email: %s", session.get("id"))
        return

    customer_id = session.get("customer", "")
    subscription_id = session.get("subscription", "")

    # Calculate expiry: 1 month from now (safe default)
    expires = timezone.now() + timedelta(days=32)

    # If there's a subscription, try to get the real period end
    if subscription_id:
        try:
            sub = stripe.Subscription.retrieve(subscription_id)
            expires = timezone.datetime.fromtimestamp(
                sub["current_period_end"], tz=timezone.utc
            )
        except Exception:
            pass

    subscriber, created = VintEdgeSubscriber.objects.update_or_create(
        email=email,
        defaults={
            "stripe_customer_id": customer_id or "",
            "stripe_subscription_id": subscription_id or "",
            "active": True,
            "plan": "pro",
            "expires_at": expires,
        },
    )
    logger.info(
        "Checkout completed: %s (customer=%s, sub=%s, created=%s)",
        email, customer_id, subscription_id, created,
    )


def _handle_subscription_update(subscription, active):
    customer_id = subscription.get("customer", "")
    subscription_id = subscription.get("id", "")

    # Get period end
    period_end = subscription.get("current_period_end")
    expires = None
    if period_end:
        expires = timezone.datetime.fromtimestamp(period_end, tz=timezone.utc)

    # Find subscriber by subscription ID or customer ID
    subscriber = None
    if subscription_id:
        subscriber = VintEdgeSubscriber.objects.filter(
            stripe_subscription_id=subscription_id
        ).first()
    if not subscriber and customer_id:
        subscriber = VintEdgeSubscriber.objects.filter(
            stripe_customer_id=customer_id
        ).first()

    if subscriber:
        subscriber.active = active
        if expires:
            subscriber.expires_at = expires
        subscriber.stripe_subscription_id = subscription_id
        subscriber.save()
        logger.info("Subscription %s: %s -> active=%s", subscription_id, subscriber.email, active)
    else:
        logger.warning(
            "Subscription event for unknown subscriber: customer=%s sub=%s",
            customer_id, subscription_id,
        )


def _handle_invoice_paid(invoice):
    customer_id = invoice.get("customer", "")
    subscription_id = invoice.get("subscription", "")
    email = invoice.get("customer_email")

    if not email and customer_id:
        try:
            customer = stripe.Customer.retrieve(customer_id)
            email = customer.get("email")
        except Exception:
            pass

    if not email:
        logger.warning("invoice.payment_succeeded with no email")
        return

    email = email.lower().strip()

    # Determine expiry from subscription
    expires = timezone.now() + timedelta(days=32)
    if subscription_id:
        try:
            sub = stripe.Subscription.retrieve(subscription_id)
            expires = timezone.datetime.fromtimestamp(
                sub["current_period_end"], tz=timezone.utc
            )
        except Exception:
            pass

    subscriber, created = VintEdgeSubscriber.objects.update_or_create(
        email=email,
        defaults={
            "stripe_customer_id": customer_id or "",
            "stripe_subscription_id": subscription_id or "",
            "active": True,
            "plan": "pro",
            "expires_at": expires,
        },
    )
    logger.info("Invoice paid: %s (created=%s)", email, created)


def _handle_invoice_failed(invoice):
    customer_id = invoice.get("customer", "")
    subscription_id = invoice.get("subscription", "")

    subscriber = None
    if subscription_id:
        subscriber = VintEdgeSubscriber.objects.filter(
            stripe_subscription_id=subscription_id
        ).first()
    if not subscriber and customer_id:
        subscriber = VintEdgeSubscriber.objects.filter(
            stripe_customer_id=customer_id
        ).first()

    if subscriber:
        subscriber.active = False
        subscriber.save()
        logger.info("Invoice payment failed: %s -> deactivated", subscriber.email)
    else:
        logger.warning(
            "Invoice failed for unknown subscriber: customer=%s sub=%s",
            customer_id, subscription_id,
        )


# ---------------------------------------------------------------------------
# GET /vintedge/verify?email=... — Check subscription status
# ---------------------------------------------------------------------------

@csrf_exempt
def verify_subscription(request):
    # Handle CORS preflight
    if request.method == "OPTIONS":
        response = HttpResponse(status=204)
        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response["Access-Control-Allow-Headers"] = "Content-Type"
        response["Access-Control-Max-Age"] = "86400"
        return response

    if request.method != "GET":
        return _cors_json({"error": "Method not allowed"}, status=405)

    email = request.GET.get("email", "").lower().strip()
    if not email or "@" not in email:
        return _cors_json({"error": "Valid email parameter required"}, status=400)

    # Rate limiting: max 20 requests per email per hour
    cache_key = f"vintedge_verify_{email}"
    hits = cache.get(cache_key, 0)
    if hits >= 20:
        return _cors_json({"error": "Rate limit exceeded. Try again later."}, status=429)
    cache.set(cache_key, hits + 1, timeout=3600)

    # Also rate limit by IP: max 60 requests per hour
    ip = _get_client_ip(request)
    ip_cache_key = f"vintedge_verify_ip_{ip}"
    ip_hits = cache.get(ip_cache_key, 0)
    if ip_hits >= 60:
        return _cors_json({"error": "Rate limit exceeded. Try again later."}, status=429)
    cache.set(ip_cache_key, ip_hits + 1, timeout=3600)

    try:
        subscriber = VintEdgeSubscriber.objects.get(email=email)
        # Check if subscription has expired
        is_active = subscriber.active
        if is_active and subscriber.expires_at and subscriber.expires_at < timezone.now():
            is_active = False

        return _cors_json({
            "active": is_active,
            "plan": subscriber.plan if is_active else None,
            "expires": subscriber.expires_at.isoformat() if subscriber.expires_at else None,
        })
    except VintEdgeSubscriber.DoesNotExist:
        return _cors_json({
            "active": False,
            "plan": None,
            "expires": None,
        })


def _get_client_ip(request):
    x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded:
        return x_forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


# ---------------------------------------------------------------------------
# GET /vintedge/success — Post-payment success page
# ---------------------------------------------------------------------------

def payment_success(request):
    session_id = request.GET.get("session_id", "")
    email = ""

    if session_id and stripe.api_key:
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            email = session.get("customer_email") or session.get(
                "customer_details", {}
            ).get("email", "")
        except Exception:
            logger.warning("Could not retrieve checkout session %s", session_id)

    return render(request, "vintedge/success.html", {"email": email})
