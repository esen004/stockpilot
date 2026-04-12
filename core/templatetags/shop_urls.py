from django import template
from django.urls import reverse

register = template.Library()


@register.simple_tag(takes_context=True)
def shop_url(context, url_name, *args):
    """Generate URL with ?shop= parameter appended."""
    url = reverse(url_name, args=args)
    shop = context.get("shop")
    if shop:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}shop={shop.shopify_domain}"
    return url
