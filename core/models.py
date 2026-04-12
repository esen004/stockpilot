from django.db import models
from django.utils import timezone


class Shop(models.Model):
    """Installed Shopify store — our top-level tenant."""

    shopify_domain = models.CharField(max_length=255, unique=True)
    access_token = models.CharField(max_length=512)
    plan = models.CharField(max_length=20, default="starter")
    installed_at = models.DateTimeField(auto_now_add=True)
    uninstalled_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    store_name = models.CharField(max_length=255, blank=True)
    store_email = models.CharField(max_length=255, blank=True)
    currency = models.CharField(max_length=10, default="USD")

    shopify_charge_id = models.CharField(max_length=100, blank=True)
    billing_status = models.CharField(
        max_length=20,
        choices=[
            ("trial", "Trial"),
            ("active", "Active"),
            ("frozen", "Frozen"),
            ("cancelled", "Cancelled"),
        ],
        default="trial",
    )
    trial_ends_at = models.DateTimeField(null=True, blank=True)

    last_product_sync = models.DateTimeField(null=True, blank=True)
    last_order_sync = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.shopify_domain

    class Meta:
        ordering = ["-installed_at"]


class Location(models.Model):
    """Shopify location (warehouse, retail store, etc.)."""

    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="locations")
    shopify_location_id = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("shop", "shopify_location_id")

    def __str__(self):
        return self.name


class Supplier(models.Model):
    """Supplier/vendor — entirely in our DB (Shopify has no Supplier API)."""

    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="suppliers")
    name = models.CharField(max_length=255)
    contact_name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    address = models.TextField(blank=True)
    currency = models.CharField(max_length=10, default="USD")
    payment_terms = models.CharField(
        max_length=50,
        choices=[
            ("prepaid", "Prepaid"),
            ("net_15", "Net 15"),
            ("net_30", "Net 30"),
            ("net_60", "Net 60"),
            ("on_receipt", "On Receipt"),
            ("other", "Other"),
        ],
        default="net_30",
    )
    lead_time_days = models.PositiveIntegerField(default=7)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        unique_together = ("shop", "name")

    def __str__(self):
        return self.name


class Product(models.Model):
    """Cached Shopify product — synced from Shopify, enriched with our data."""

    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="products")
    shopify_product_id = models.CharField(max_length=50)
    title = models.CharField(max_length=500)
    vendor = models.CharField(max_length=255, blank=True)
    product_type = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, default="active")
    image_url = models.URLField(max_length=1000, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    default_supplier = models.ForeignKey(
        Supplier, on_delete=models.SET_NULL, null=True, blank=True, related_name="products"
    )

    class Meta:
        unique_together = ("shop", "shopify_product_id")

    def __str__(self):
        return self.title


class Variant(models.Model):
    """Cached Shopify variant with inventory + supplier data."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="variants")
    shopify_variant_id = models.CharField(max_length=50)
    shopify_inventory_item_id = models.CharField(max_length=50)
    title = models.CharField(max_length=500)
    sku = models.CharField(max_length=255, blank=True)
    barcode = models.CharField(max_length=255, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Inventory settings (our data — Shopify doesn't have min/max)
    reorder_point = models.PositiveIntegerField(default=0)
    reorder_qty = models.PositiveIntegerField(default=0)

    # Supplier link
    supplier = models.ForeignKey(
        Supplier, on_delete=models.SET_NULL, null=True, blank=True, related_name="variants"
    )
    supplier_sku = models.CharField(max_length=255, blank=True)
    supplier_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    class Meta:
        unique_together = ("shop", "shopify_variant_id")

    def __str__(self):
        if self.title != "Default Title":
            return f"{self.product.title} - {self.title}"
        return self.product.title


class InventoryLevel(models.Model):
    """Cached inventory quantity per variant per location."""

    variant = models.ForeignKey(Variant, on_delete=models.CASCADE, related_name="inventory_levels")
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name="inventory_levels")
    available = models.IntegerField(default=0)
    on_hand = models.IntegerField(default=0)
    incoming = models.IntegerField(default=0)
    committed = models.IntegerField(default=0)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("variant", "location")

    def __str__(self):
        return f"{self.variant} @ {self.location}: {self.available}"


class PurchaseOrder(models.Model):
    """Purchase order — entirely in our DB."""

    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("ordered", "Ordered"),
        ("partial", "Partially Received"),
        ("received", "Received"),
        ("closed", "Closed"),
        ("cancelled", "Cancelled"),
    ]

    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="purchase_orders")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="purchase_orders")
    location = models.ForeignKey(
        Location, on_delete=models.SET_NULL, null=True, blank=True, related_name="purchase_orders"
    )

    po_number = models.CharField(max_length=50)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")

    order_date = models.DateField(null=True, blank=True)
    expected_date = models.DateField(null=True, blank=True)
    received_date = models.DateField(null=True, blank=True)

    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    shipping_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    currency = models.CharField(max_length=10, default="USD")

    payment_terms = models.CharField(max_length=50, blank=True)
    notes = models.TextField(blank=True)

    sent_to_supplier_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = ("shop", "po_number")

    def __str__(self):
        return f"PO-{self.po_number} ({self.supplier.name})"

    def recalculate_totals(self):
        from django.db.models import F, Sum

        agg = self.line_items.aggregate(total=Sum(F("quantity") * F("unit_cost")))
        self.subtotal = agg["total"] or 0
        self.total = self.subtotal + self.tax + self.shipping_cost
        self.save(update_fields=["subtotal", "total", "updated_at"])


class POLineItem(models.Model):
    """Line item on a purchase order."""

    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="line_items"
    )
    variant = models.ForeignKey(Variant, on_delete=models.CASCADE, related_name="po_line_items")
    sku = models.CharField(max_length=255, blank=True)
    description = models.CharField(max_length=500, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    received_qty = models.PositiveIntegerField(default=0)

    @property
    def line_total(self):
        return self.quantity * self.unit_cost

    @property
    def remaining_qty(self):
        return max(0, self.quantity - self.received_qty)

    @property
    def is_fully_received(self):
        return self.received_qty >= self.quantity

    def __str__(self):
        return f"{self.description or self.variant} x{self.quantity}"


class ReceivingRecord(models.Model):
    """Record of receiving inventory against a PO line item."""

    line_item = models.ForeignKey(POLineItem, on_delete=models.CASCADE, related_name="receivings")
    location = models.ForeignKey(Location, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    received_at = models.DateTimeField(default=timezone.now)
    received_by = models.CharField(max_length=255, blank=True)
    notes = models.CharField(max_length=500, blank=True)
    synced_to_shopify = models.BooleanField(default=False)

    def __str__(self):
        return f"Received {self.quantity}x {self.line_item.variant} at {self.location}"


class SalesVelocity(models.Model):
    """Pre-calculated sales velocity per variant. Updated daily from order history."""

    variant = models.ForeignKey(Variant, on_delete=models.CASCADE, related_name="sales_velocity")
    location = models.ForeignKey(
        Location, on_delete=models.CASCADE, null=True, blank=True, related_name="sales_velocity"
    )

    avg_daily_sales_7d = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    avg_daily_sales_30d = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    avg_daily_sales_90d = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    total_sold_30d = models.PositiveIntegerField(default=0)
    total_sold_90d = models.PositiveIntegerField(default=0)

    days_of_stock = models.DecimalField(max_digits=10, decimal_places=1, null=True, blank=True)
    abc_class = models.CharField(
        max_length=1,
        choices=[("A", "A - Top 80%"), ("B", "B - Next 15%"), ("C", "C - Bottom 5%")],
        default="C",
    )
    is_dead_stock = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("variant", "location")

    def __str__(self):
        return f"{self.variant} velocity: {self.avg_daily_sales_30d}/day"


class Stocktake(models.Model):
    """Inventory count session."""

    STATUS_CHOICES = [
        ("in_progress", "In Progress"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="stocktakes")
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name="stocktakes")
    name = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="in_progress")
    notes = models.TextField(blank=True)

    total_items = models.PositiveIntegerField(default=0)
    total_counted = models.PositiveIntegerField(default=0)
    total_variance = models.IntegerField(default=0)
    variance_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.name} @ {self.location}"


class StocktakeItem(models.Model):
    """Individual item in a stocktake."""

    stocktake = models.ForeignKey(Stocktake, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(Variant, on_delete=models.CASCADE, related_name="stocktake_items")
    expected_qty = models.IntegerField(default=0)
    counted_qty = models.IntegerField(null=True, blank=True)

    @property
    def variance(self):
        if self.counted_qty is None:
            return 0
        return self.counted_qty - self.expected_qty

    @property
    def is_counted(self):
        return self.counted_qty is not None

    def __str__(self):
        return f"{self.variant}: expected={self.expected_qty}, counted={self.counted_qty}"


class Transfer(models.Model):
    """Inventory transfer between locations."""

    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("in_transit", "In Transit"),
        ("received", "Received"),
        ("cancelled", "Cancelled"),
    ]

    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="transfers")
    from_location = models.ForeignKey(
        Location, on_delete=models.CASCADE, related_name="transfers_out"
    )
    to_location = models.ForeignKey(
        Location, on_delete=models.CASCADE, related_name="transfers_in"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Transfer {self.from_location} → {self.to_location}"


class TransferItem(models.Model):
    """Item in a transfer."""

    transfer = models.ForeignKey(Transfer, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(Variant, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    received_qty = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.variant} x{self.quantity}"
