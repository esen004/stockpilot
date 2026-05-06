from django.conf import settings


def shopify_app(request):
    return {
        "SHOPIFY_API_KEY": settings.SHOPIFY_API_KEY,
    }
