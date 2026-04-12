from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("auth/", include("shopify_auth.urls")),
    path("webhooks/", include("shopify_auth.webhook_urls")),
    path("", include("core.urls")),
]
