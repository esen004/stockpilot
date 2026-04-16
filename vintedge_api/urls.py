from django.urls import path
from . import views

app_name = "vintedge_api"

urlpatterns = [
    path("webhook", views.stripe_webhook, name="webhook"),
    path("verify", views.verify_subscription, name="verify"),
    path("success", views.payment_success, name="success"),
]
