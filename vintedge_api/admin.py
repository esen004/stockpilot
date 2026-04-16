from django.contrib import admin
from .models import VintEdgeSubscriber


@admin.register(VintEdgeSubscriber)
class VintEdgeSubscriberAdmin(admin.ModelAdmin):
    list_display = ("email", "active", "plan", "stripe_subscription_id", "created_at", "expires_at")
    list_filter = ("active", "plan")
    search_fields = ("email", "stripe_customer_id", "stripe_subscription_id")
    readonly_fields = ("created_at", "updated_at")
