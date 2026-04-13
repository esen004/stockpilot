class ShopifyEmbedMiddleware:
    """Set headers required for Shopify embedded app iframe."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Allow Shopify to embed our app in an iframe
        shop = request.GET.get("shop", "")
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
