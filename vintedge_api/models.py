from django.db import models


class VintEdgeSubscriber(models.Model):
    email = models.EmailField(unique=True, db_index=True)
    stripe_customer_id = models.CharField(max_length=255, blank=True, default="")
    stripe_subscription_id = models.CharField(max_length=255, blank=True, default="")
    active = models.BooleanField(default=True)
    plan = models.CharField(max_length=50, default="pro")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "VintEdge Subscriber"
        verbose_name_plural = "VintEdge Subscribers"

    def __str__(self):
        status = "active" if self.active else "inactive"
        return f"{self.email} ({status})"
