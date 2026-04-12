from django.contrib import admin
from .models import (
    Shop, Location, Supplier, Product, Variant,
    InventoryLevel, PurchaseOrder, POLineItem,
    ReceivingRecord, SalesVelocity,
)


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = ["shopify_domain", "store_name", "plan", "billing_status", "is_active"]
    list_filter = ["plan", "billing_status", "is_active"]


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ["name", "shop", "is_active"]


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ["name", "shop", "email", "lead_time_days", "payment_terms"]
    list_filter = ["shop"]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["title", "shop", "vendor", "default_supplier", "status"]
    list_filter = ["shop", "status"]
    search_fields = ["title", "vendor"]


@admin.register(Variant)
class VariantAdmin(admin.ModelAdmin):
    list_display = ["__str__", "sku", "price", "cost", "supplier"]
    search_fields = ["sku", "barcode", "product__title"]


class POLineItemInline(admin.TabularInline):
    model = POLineItem
    extra = 0


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ["po_number", "supplier", "status", "total", "created_at"]
    list_filter = ["status", "shop"]
    inlines = [POLineItemInline]


@admin.register(SalesVelocity)
class SalesVelocityAdmin(admin.ModelAdmin):
    list_display = ["variant", "avg_daily_sales_30d", "days_of_stock", "abc_class", "is_dead_stock"]
    list_filter = ["abc_class", "is_dead_stock"]
