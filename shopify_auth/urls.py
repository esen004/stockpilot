from django.urls import path
from . import views

urlpatterns = [
    path("install", views.install, name="shopify_install"),
    path("callback", views.callback, name="shopify_callback"),
    path("setup", views.manual_setup, name="manual_setup"),
]
