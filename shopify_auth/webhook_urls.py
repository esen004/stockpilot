from django.urls import path
from . import views

urlpatterns = [
    path("app-uninstalled", views.webhook_app_uninstalled),
    path("customers-data-request", views.webhook_customers_data_request),
    path("customers-redact", views.webhook_customers_redact),
    path("shop-redact", views.webhook_shop_redact),
]
