import base64
import hashlib
import hmac
import json
import time

from django.conf import settings


class ShopifyEmbedMiddleware:
    """Set headers required for Shopify embedded app iframe."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Decode Shopify session token if present
        self._decode_session_token(request)

        response = self.get_response(request)

        # Allow Shopify to embed our app in an iframe
        shop = getattr(request, "shopify_shop_domain", None) or request.GET.get("shop", "")
        if shop:
            response["Content-Security-Policy"] = (
                f"frame-ancestors https://{shop} https://admin.shopify.com https://*.myshopify.com;"
            )
        else:
            response["Content-Security-Policy"] = (
                "frame-ancestors https://admin.shopify.com https://*.myshopify.com;"
            )

        # Remove X-Frame-Options (conflicts with CSP frame-ancestors)
        if "X-Frame-Options" in response:
            del response["X-Frame-Options"]

        return response

    def _decode_session_token(self, request):
        """Decode and verify Shopify session token (JWT) from Authorization header."""
        request.shopify_shop_domain = None
        request.shopify_session_token = None

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return

        token = auth_header[7:]
        try:
            payload = self._verify_shopify_jwt(token)
            if payload:
                # Extract shop domain from "iss" (e.g. "https://store.myshopify.com/admin")
                iss = payload.get("iss", "")
                shop_domain = iss.replace("https://", "").replace("/admin", "")
                request.shopify_shop_domain = shop_domain
                request.shopify_session_token = payload
        except Exception:
            pass

    def _verify_shopify_jwt(self, token):
        """Verify a Shopify session token JWT using HMAC-SHA256."""
        parts = token.split(".")
        if len(parts) != 3:
            return None

        header_b64, payload_b64, signature_b64 = parts

        # Verify signature
        secret = settings.SHOPIFY_API_SECRET.encode("utf-8")
        message = f"{header_b64}.{payload_b64}".encode("utf-8")
        expected_sig = hmac.new(secret, message, hashlib.sha256).digest()
        actual_sig = self._base64url_decode(signature_b64)

        if not hmac.compare_digest(expected_sig, actual_sig):
            return None

        # Decode payload
        payload = json.loads(self._base64url_decode(payload_b64))

        # Check expiration (with 10s leeway)
        exp = payload.get("exp", 0)
        if time.time() > exp + 10:
            return None

        # Check audience matches our API key
        aud = payload.get("aud", "")
        if aud != settings.SHOPIFY_API_KEY:
            return None

        return payload

    @staticmethod
    def _base64url_decode(s):
        s += "=" * (4 - len(s) % 4)
        return base64.urlsafe_b64decode(s)
