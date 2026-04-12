import json
from decimal import Decimal
from io import BytesIO

from django.conf import settings
from django.db.models import F, Q, Sum, Count, Value, CharField
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (
    Shop, Location, Supplier, Product, Variant,
    InventoryLevel, PurchaseOrder, POLineItem,
    ReceivingRecord, SalesVelocity,
)


# --- Helpers ---

def _get_shop(request):
    # First check session
    shop_id = request.session.get("shop_id")
    if shop_id:
        try:
            return Shop.objects.get(id=shop_id, is_active=True)
        except Shop.DoesNotExist:
            pass

    # Fallback: check shop query param (Shopify passes this when embedding)
    shop_domain = request.GET.get("shop", "").strip()
    if shop_domain:
        try:
            shop_obj = Shop.objects.get(shopify_domain=shop_domain, is_active=True)
            request.session["shop_id"] = shop_obj.id
            return shop_obj
        except Shop.DoesNotExist:
            pass

    # Fallback: if only one shop exists, use it (dev convenience)
    shops = Shop.objects.filter(is_active=True)
    if shops.count() == 1:
        shop_obj = shops.first()
        request.session["shop_id"] = shop_obj.id
        return shop_obj

    return None


def _require_shop(view_func):
    def wrapper(request, *args, **kwargs):
        shop = _get_shop(request)
        if not shop:
            # If shop param provided but not found, redirect to install
            shop_domain = request.GET.get("shop", "")
            if shop_domain:
                return redirect(f"/auth/install?shop={shop_domain}")
            return HttpResponse("Please install the app from your Shopify admin.", status=400)
        request.shop = shop
        return view_func(request, *args, **kwargs)
    return wrapper


# --- Dashboard ---

@_require_shop
def dashboard(request):
    shop = request.shop
    total_variants = Variant.objects.filter(shop=shop).count()
    total_suppliers = shop.suppliers.count()
    open_pos = PurchaseOrder.objects.filter(
        shop=shop, status__in=["draft", "ordered", "partial"]
    ).count()
    low_stock_count = SalesVelocity.objects.filter(
        variant__shop=shop, days_of_stock__isnull=False, days_of_stock__lte=14,
    ).count()
    dead_stock_count = SalesVelocity.objects.filter(
        variant__shop=shop, is_dead_stock=True,
    ).count()
    recent_pos = PurchaseOrder.objects.filter(shop=shop).select_related("supplier")[:5]

    return render(request, "core/dashboard.html", {
        "shop": shop,
        "total_variants": total_variants,
        "total_suppliers": total_suppliers,
        "open_pos": open_pos,
        "low_stock_count": low_stock_count,
        "dead_stock_count": dead_stock_count,
        "recent_pos": recent_pos,
        "active_tab": "dashboard",
    })


# --- Sync ---

def sync_from_shopify(request):
    """Sync — no decorator, handle shop manually to avoid 500 on redirect."""
    import traceback
    try:
        shop = _get_shop(request)
        if not shop:
            return HttpResponse("No shop found. Install the app first.", status=400)

        from .shopify_client import ShopifyClient
        client = ShopifyClient(shop)
        client.sync_all()
        return HttpResponse(
            f"<h2>Sync complete!</h2>"
            f"<p>Store: {shop.shopify_domain}</p>"
            f"<p>Products synced at: {shop.last_product_sync}</p>"
            f"<p><a href='/?shop={shop.shopify_domain}'>Go to Dashboard</a></p>"
        )
    except Exception as e:
        tb = traceback.format_exc()
        return HttpResponse(f"<h2>Sync Error</h2><pre>{e}\n\n{tb}</pre>")


# =============================================================
# SUPPLIERS
# =============================================================

@_require_shop
def supplier_list(request):
    suppliers = Supplier.objects.filter(shop=request.shop).annotate(
        product_count=Count("products"),
    )
    return render(request, "core/supplier_list.html", {
        "shop": request.shop,
        "suppliers": suppliers,
        "active_tab": "suppliers",
    })


@_require_shop
def supplier_create(request):
    if request.method == "POST":
        supplier = Supplier(shop=request.shop)
        _save_supplier_from_post(supplier, request.POST)
        return redirect("supplier_detail", supplier_id=supplier.id)
    return render(request, "core/supplier_form.html", {
        "shop": request.shop,
        "active_tab": "suppliers",
        "editing": False,
    })


@_require_shop
def supplier_detail(request, supplier_id):
    supplier = get_object_or_404(Supplier, id=supplier_id, shop=request.shop)
    products = Product.objects.filter(default_supplier=supplier)
    variants = Variant.objects.filter(supplier=supplier)
    pos = PurchaseOrder.objects.filter(supplier=supplier).order_by("-created_at")[:10]

    return render(request, "core/supplier_detail.html", {
        "shop": request.shop,
        "supplier": supplier,
        "products": products,
        "variants": variants,
        "recent_pos": pos,
        "active_tab": "suppliers",
    })


@_require_shop
def supplier_edit(request, supplier_id):
    supplier = get_object_or_404(Supplier, id=supplier_id, shop=request.shop)
    if request.method == "POST":
        _save_supplier_from_post(supplier, request.POST)
        return redirect("supplier_detail", supplier_id=supplier.id)
    return render(request, "core/supplier_form.html", {
        "shop": request.shop,
        "supplier": supplier,
        "active_tab": "suppliers",
        "editing": True,
    })


@_require_shop
@require_POST
def supplier_delete(request, supplier_id):
    supplier = get_object_or_404(Supplier, id=supplier_id, shop=request.shop)
    if not supplier.purchase_orders.exists():
        supplier.delete()
    return redirect("supplier_list")


def _save_supplier_from_post(supplier, post):
    supplier.name = post.get("name", "").strip()
    supplier.contact_name = post.get("contact_name", "").strip()
    supplier.email = post.get("email", "").strip()
    supplier.phone = post.get("phone", "").strip()
    supplier.address = post.get("address", "").strip()
    supplier.currency = post.get("currency", "USD").strip()
    supplier.payment_terms = post.get("payment_terms", "net_30")
    supplier.lead_time_days = int(post.get("lead_time_days", 7) or 7)
    supplier.notes = post.get("notes", "").strip()
    supplier.save()


# =============================================================
# PURCHASE ORDERS
# =============================================================

@_require_shop
def po_list(request):
    status_filter = request.GET.get("status", "")
    pos = PurchaseOrder.objects.filter(shop=request.shop).select_related("supplier", "location")
    if status_filter:
        pos = pos.filter(status=status_filter)

    return render(request, "core/po_list.html", {
        "shop": request.shop,
        "purchase_orders": pos,
        "status_filter": status_filter,
        "active_tab": "purchase_orders",
    })


@_require_shop
def po_create(request):
    if request.method == "POST":
        po = _create_po_from_post(request.shop, request.POST)
        return redirect("po_detail", po_id=po.id)

    suppliers = Supplier.objects.filter(shop=request.shop)
    locations = Location.objects.filter(shop=request.shop, is_active=True)

    # Auto-generate PO number
    last_po = PurchaseOrder.objects.filter(shop=request.shop).order_by("-id").first()
    next_num = 1
    if last_po:
        try:
            next_num = int(last_po.po_number) + 1
        except ValueError:
            next_num = last_po.id + 1

    return render(request, "core/po_form.html", {
        "shop": request.shop,
        "suppliers": suppliers,
        "locations": locations,
        "next_po_number": str(next_num).zfill(4),
        "active_tab": "purchase_orders",
        "editing": False,
    })


@_require_shop
def po_detail(request, po_id):
    po = get_object_or_404(
        PurchaseOrder.objects.select_related("supplier", "location"),
        id=po_id, shop=request.shop,
    )
    line_items = po.line_items.select_related("variant__product").all()

    return render(request, "core/po_detail.html", {
        "shop": request.shop,
        "po": po,
        "line_items": line_items,
        "active_tab": "purchase_orders",
    })


@_require_shop
def po_add_items(request, po_id):
    """Add line items to a PO — shows supplier's linked variants."""
    po = get_object_or_404(PurchaseOrder, id=po_id, shop=request.shop)

    if request.method == "POST":
        _add_line_items_from_post(po, request.POST)
        po.recalculate_totals()
        return redirect("po_detail", po_id=po.id)

    # Show variants linked to this supplier
    variants = Variant.objects.filter(
        shop=request.shop, supplier=po.supplier,
    ).select_related("product")

    # Also show all variants if supplier has none linked
    if not variants.exists():
        variants = Variant.objects.filter(shop=request.shop).select_related("product")

    # Exclude already-added variants
    existing_variant_ids = po.line_items.values_list("variant_id", flat=True)
    variants = variants.exclude(id__in=existing_variant_ids)

    return render(request, "core/po_add_items.html", {
        "shop": request.shop,
        "po": po,
        "variants": variants[:200],
        "active_tab": "purchase_orders",
    })


@_require_shop
def po_fill_shelves(request, po_id):
    """Auto-calculate reorder quantities based on sales velocity."""
    po = get_object_or_404(PurchaseOrder, id=po_id, shop=request.shop)
    days_to_cover = int(request.GET.get("days", 30))

    # Get supplier's variants with velocity data
    variants = Variant.objects.filter(
        shop=request.shop, supplier=po.supplier,
    ).select_related("product")

    suggestions = []
    for variant in variants:
        velocity = SalesVelocity.objects.filter(variant=variant).first()
        inv = InventoryLevel.objects.filter(variant=variant).aggregate(
            total_available=Sum("available")
        )
        current_stock = inv["total_available"] or 0
        daily_sales = float(velocity.avg_daily_sales_30d) if velocity else 0

        if daily_sales > 0:
            needed = int(daily_sales * days_to_cover) - current_stock
            if needed > 0:
                suggestions.append({
                    "variant": variant,
                    "current_stock": current_stock,
                    "daily_sales": round(daily_sales, 2),
                    "suggested_qty": needed,
                    "days_of_stock": round(current_stock / daily_sales, 1) if daily_sales else 999,
                })

    if request.method == "POST":
        # Apply suggestions as line items
        for variant in variants:
            qty_key = f"qty_{variant.id}"
            qty = int(request.POST.get(qty_key, 0) or 0)
            if qty > 0:
                POLineItem.objects.update_or_create(
                    purchase_order=po,
                    variant=variant,
                    defaults={
                        "sku": variant.sku,
                        "description": str(variant),
                        "quantity": qty,
                        "unit_cost": variant.supplier_cost or variant.cost,
                    },
                )
        po.recalculate_totals()
        return redirect("po_detail", po_id=po.id)

    return render(request, "core/po_fill_shelves.html", {
        "shop": request.shop,
        "po": po,
        "suggestions": suggestions,
        "days_to_cover": days_to_cover,
        "active_tab": "purchase_orders",
    })


@_require_shop
@require_POST
def po_mark_ordered(request, po_id):
    po = get_object_or_404(PurchaseOrder, id=po_id, shop=request.shop)
    po.status = "ordered"
    po.order_date = timezone.now().date()
    if po.supplier.lead_time_days:
        from datetime import timedelta
        po.expected_date = po.order_date + timedelta(days=po.supplier.lead_time_days)
    po.save()
    return redirect("po_detail", po_id=po.id)


@_require_shop
@require_POST
def po_send_email(request, po_id):
    """Send PO to supplier via email."""
    po = get_object_or_404(PurchaseOrder, id=po_id, shop=request.shop)
    if not po.supplier.email:
        return redirect("po_detail", po_id=po.id)

    from django.core.mail import send_mail
    line_items = po.line_items.select_related("variant__product").all()

    body = f"Purchase Order: PO-{po.po_number}\n"
    body += f"From: {request.shop.store_name}\n"
    body += f"Date: {po.order_date or timezone.now().date()}\n\n"
    body += "Items:\n"
    body += "-" * 60 + "\n"
    for item in line_items:
        body += f"  {item.description or item.variant} | SKU: {item.sku} | Qty: {item.quantity} | ${item.unit_cost} ea\n"
    body += "-" * 60 + "\n"
    body += f"Subtotal: ${po.subtotal}\n"
    body += f"Total: ${po.total}\n\n"
    body += f"Notes: {po.notes}\n"

    try:
        send_mail(
            subject=f"Purchase Order PO-{po.po_number} from {request.shop.store_name}",
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL if hasattr(settings, "DEFAULT_FROM_EMAIL") else request.shop.store_email,
            recipient_list=[po.supplier.email],
            fail_silently=True,
        )
        po.sent_to_supplier_at = timezone.now()
        po.save(update_fields=["sent_to_supplier_at"])
    except Exception:
        pass

    return redirect("po_detail", po_id=po.id)


@_require_shop
def po_pdf(request, po_id):
    """Generate PDF of purchase order."""
    po = get_object_or_404(PurchaseOrder, id=po_id, shop=request.shop)
    line_items = po.line_items.select_related("variant__product").all()

    html = render(request, "core/po_pdf.html", {
        "po": po,
        "line_items": line_items,
        "shop": request.shop,
    }).content.decode()

    try:
        from weasyprint import HTML
        pdf = HTML(string=html).write_pdf()
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="PO-{po.po_number}.pdf"'
        return response
    except Exception as e:
        return HttpResponse(f"PDF generation error: {e}", status=500)


# =============================================================
# RECEIVING
# =============================================================

@_require_shop
def po_receive(request, po_id):
    """Receive inventory against a PO."""
    po = get_object_or_404(PurchaseOrder, id=po_id, shop=request.shop)
    line_items = po.line_items.select_related("variant__product").filter(
        received_qty__lt=F("quantity")
    )
    locations = Location.objects.filter(shop=request.shop, is_active=True)

    if request.method == "POST":
        location_id = request.POST.get("location")
        location = get_object_or_404(Location, id=location_id, shop=request.shop)

        from .shopify_client import ShopifyClient
        client = ShopifyClient(request.shop)

        any_received = False
        all_received = True

        for item in po.line_items.all():
            qty_key = f"receive_{item.id}"
            qty = int(request.POST.get(qty_key, 0) or 0)
            if qty <= 0:
                if item.remaining_qty > 0:
                    all_received = False
                continue

            any_received = True

            # Record the receiving
            ReceivingRecord.objects.create(
                line_item=item,
                location=location,
                quantity=qty,
            )
            item.received_qty += qty
            item.save(update_fields=["received_qty"])

            # Sync to Shopify — adjust inventory
            try:
                client.adjust_inventory(
                    item.variant.shopify_inventory_item_id,
                    location.shopify_location_id,
                    qty,
                    reason="received",
                )
                # Update cost in Shopify if supplier cost differs
                if item.unit_cost and item.unit_cost != item.variant.cost:
                    client.update_inventory_cost(
                        item.variant.shopify_inventory_item_id,
                        float(item.unit_cost),
                    )
                    item.variant.cost = item.unit_cost
                    item.variant.save(update_fields=["cost"])
            except Exception:
                pass  # Log but don't block receiving

            if item.remaining_qty > 0:
                all_received = False

        # Update PO status
        if any_received:
            if all_received:
                po.status = "received"
                po.received_date = timezone.now().date()
            else:
                po.status = "partial"
            po.save()

        return redirect("po_detail", po_id=po.id)

    return render(request, "core/po_receive.html", {
        "shop": request.shop,
        "po": po,
        "line_items": line_items,
        "locations": locations,
        "default_location": po.location,
        "active_tab": "purchase_orders",
    })


# =============================================================
# INVENTORY
# =============================================================

@_require_shop
def inventory_list(request):
    search = request.GET.get("q", "").strip()
    stock_filter = request.GET.get("stock", "")

    variants = Variant.objects.filter(shop=request.shop).select_related(
        "product", "supplier"
    ).prefetch_related("inventory_levels", "sales_velocity")

    if search:
        variants = variants.filter(
            Q(sku__icontains=search) |
            Q(product__title__icontains=search) |
            Q(barcode__icontains=search)
        )

    # Annotate with total available stock
    from django.db.models import Subquery, OuterRef
    variants = variants.annotate(
        total_stock=Sum("inventory_levels__available"),
    )

    if stock_filter == "low":
        variants = variants.filter(total_stock__gt=0, total_stock__lte=F("reorder_point"))
    elif stock_filter == "out":
        variants = variants.filter(total_stock__lte=0)
    elif stock_filter == "dead":
        variants = variants.filter(sales_velocity__is_dead_stock=True)

    return render(request, "core/inventory_list.html", {
        "shop": request.shop,
        "variants": variants[:500],
        "search": search,
        "stock_filter": stock_filter,
        "active_tab": "inventory",
    })


@_require_shop
def fill_shelves_global(request):
    """Global fill-shelves — auto-reorder calculation across all suppliers."""
    days_to_cover = int(request.GET.get("days", 30))
    suggestions = []

    variants = Variant.objects.filter(
        shop=request.shop, supplier__isnull=False,
    ).select_related("product", "supplier")

    for variant in variants:
        velocity = SalesVelocity.objects.filter(variant=variant).first()
        inv = InventoryLevel.objects.filter(variant=variant).aggregate(
            total=Sum("available")
        )
        stock = inv["total"] or 0
        daily = float(velocity.avg_daily_sales_30d) if velocity else 0

        if daily > 0:
            needed = int(daily * days_to_cover) - stock
            if needed > 0:
                suggestions.append({
                    "variant": variant,
                    "supplier": variant.supplier,
                    "current_stock": stock,
                    "daily_sales": round(daily, 2),
                    "suggested_qty": needed,
                    "days_of_stock": round(stock / daily, 1),
                })

    # Group by supplier
    by_supplier = {}
    for s in suggestions:
        sup = s["supplier"]
        if sup.id not in by_supplier:
            by_supplier[sup.id] = {"supplier": sup, "items": [], "total_cost": 0}
        cost = s["suggested_qty"] * float(s["variant"].supplier_cost or s["variant"].cost)
        s["line_cost"] = round(cost, 2)
        by_supplier[sup.id]["items"].append(s)
        by_supplier[sup.id]["total_cost"] += cost

    return render(request, "core/fill_shelves.html", {
        "shop": request.shop,
        "by_supplier": by_supplier.values(),
        "days_to_cover": days_to_cover,
        "active_tab": "inventory",
    })


# =============================================================
# REPORTS
# =============================================================

@_require_shop
def reports_overview(request):
    return render(request, "core/reports.html", {
        "shop": request.shop,
        "active_tab": "reports",
    })


@_require_shop
def report_abc(request):
    """ABC analysis — classify variants by revenue contribution."""
    velocities = SalesVelocity.objects.filter(
        variant__shop=request.shop,
    ).select_related("variant__product", "variant__supplier").order_by("-total_sold_90d")

    a_items = velocities.filter(abc_class="A")
    b_items = velocities.filter(abc_class="B")
    c_items = velocities.filter(abc_class="C")

    return render(request, "core/report_abc.html", {
        "shop": request.shop,
        "a_items": a_items,
        "b_items": b_items,
        "c_items": c_items,
        "active_tab": "reports",
    })


@_require_shop
def report_low_stock(request):
    low_stock = SalesVelocity.objects.filter(
        variant__shop=request.shop,
        days_of_stock__isnull=False,
        days_of_stock__lte=14,
    ).select_related("variant__product", "variant__supplier").order_by("days_of_stock")

    return render(request, "core/report_low_stock.html", {
        "shop": request.shop,
        "items": low_stock,
        "active_tab": "reports",
    })


@_require_shop
def report_dead_stock(request):
    dead = SalesVelocity.objects.filter(
        variant__shop=request.shop,
        is_dead_stock=True,
    ).select_related("variant__product", "variant__supplier")

    # Calculate total dead stock value
    total_value = 0
    for item in dead:
        inv = InventoryLevel.objects.filter(variant=item.variant).aggregate(s=Sum("available"))
        stock = inv["s"] or 0
        total_value += stock * float(item.variant.cost)

    return render(request, "core/report_dead_stock.html", {
        "shop": request.shop,
        "items": dead,
        "total_value": round(total_value, 2),
        "active_tab": "reports",
    })


# =============================================================
# SALES VELOCITY CALCULATION (run daily)
# =============================================================

def calculate_sales_velocity(shop: Shop):
    """Calculate sales velocity for all variants. Call daily via cron."""
    from .shopify_client import ShopifyClient
    from datetime import timedelta

    client = ShopifyClient(shop)

    # Fetch orders from last 90 days
    since = (timezone.now() - timedelta(days=90)).strftime("%Y-%m-%dT00:00:00Z")
    orders = _fetch_orders_since(client, since)

    # Count sales per variant
    variant_sales = {}  # variant_id -> {7d: count, 30d: count, 90d: count}
    now = timezone.now()

    for order in orders:
        order_date = order.get("created_at", "")
        if not order_date:
            continue
        from django.utils.dateparse import parse_datetime
        dt = parse_datetime(order_date)
        if not dt:
            continue
        days_ago = (now - dt).days

        for item in order.get("line_items", []):
            vid = str(item.get("variant_id", ""))
            if not vid:
                continue
            qty = item.get("quantity", 0)
            if vid not in variant_sales:
                variant_sales[vid] = {"7d": 0, "30d": 0, "90d": 0}
            variant_sales[vid]["90d"] += qty
            if days_ago <= 30:
                variant_sales[vid]["30d"] += qty
            if days_ago <= 7:
                variant_sales[vid]["7d"] += qty

    # Update SalesVelocity records
    all_variants = Variant.objects.filter(shop=shop)
    for variant in all_variants:
        sales = variant_sales.get(variant.shopify_variant_id, {"7d": 0, "30d": 0, "90d": 0})

        avg_7d = sales["7d"] / 7
        avg_30d = sales["30d"] / 30
        avg_90d = sales["90d"] / 90

        # Days of stock
        inv = InventoryLevel.objects.filter(variant=variant).aggregate(s=Sum("available"))
        stock = inv["s"] or 0
        days_of_stock = round(stock / avg_30d, 1) if avg_30d > 0 else None

        SalesVelocity.objects.update_or_create(
            variant=variant,
            location=None,
            defaults={
                "avg_daily_sales_7d": round(avg_7d, 4),
                "avg_daily_sales_30d": round(avg_30d, 4),
                "avg_daily_sales_90d": round(avg_90d, 4),
                "total_sold_30d": sales["30d"],
                "total_sold_90d": sales["90d"],
                "days_of_stock": days_of_stock,
                "is_dead_stock": sales["90d"] == 0,
            },
        )

    # ABC classification
    _calculate_abc(shop)

    shop.last_order_sync = timezone.now()
    shop.save(update_fields=["last_order_sync"])


def _calculate_abc(shop):
    """Classify variants into A/B/C based on revenue contribution."""
    velocities = SalesVelocity.objects.filter(
        variant__shop=shop,
    ).select_related("variant").order_by("-total_sold_90d")

    total_revenue = sum(
        v.total_sold_90d * float(v.variant.price) for v in velocities
    )
    if total_revenue == 0:
        return

    running = 0
    for v in velocities:
        revenue = v.total_sold_90d * float(v.variant.price)
        running += revenue
        pct = running / total_revenue
        if pct <= 0.8:
            v.abc_class = "A"
        elif pct <= 0.95:
            v.abc_class = "B"
        else:
            v.abc_class = "C"
        v.save(update_fields=["abc_class"])


def _fetch_orders_since(client, since_date):
    """Fetch orders from Shopify since a date."""
    orders = []
    cursor = None
    has_next = True

    while has_next:
        gql = """
        query($cursor: String, $query: String) {
            orders(first: 50, after: $cursor, query: $query) {
                pageInfo { hasNextPage endCursor }
                edges {
                    node {
                        id
                        createdAt
                        lineItems(first: 50) {
                            edges {
                                node {
                                    variant { id }
                                    quantity
                                }
                            }
                        }
                    }
                }
            }
        }
        """
        data = client.query(gql, {"cursor": cursor, "query": f"created_at:>={since_date}"})
        orders_data = data.get("orders", {})
        page_info = orders_data.get("pageInfo", {})

        for edge in orders_data.get("edges", []):
            node = edge["node"]
            line_items = []
            for li_edge in node.get("lineItems", {}).get("edges", []):
                li = li_edge["node"]
                variant = li.get("variant")
                if variant and variant.get("id"):
                    line_items.append({
                        "variant_id": variant["id"].split("/")[-1],
                        "quantity": li.get("quantity", 0),
                    })
            orders.append({
                "created_at": node.get("createdAt", ""),
                "line_items": line_items,
            })

        has_next = page_info.get("hasNextPage", False)
        cursor = page_info.get("endCursor")

    return orders


# =============================================================
# BILLING
# =============================================================

@_require_shop
def billing_select(request):
    """Show plan selection page."""
    if request.method == "POST":
        plan_key = request.POST.get("plan", "starter")
        from .billing import create_subscription
        confirmation_url = create_subscription(request.shop, plan_key)
        if confirmation_url:
            return redirect(confirmation_url)
        return redirect("dashboard")

    return render(request, "core/billing_select.html", {
        "shop": request.shop,
        "plans": settings.STOCKPILOT_PLANS,
        "current_plan": request.shop.plan,
    })


@_require_shop
def billing_callback(request):
    """Shopify redirects here after merchant approves/declines charge."""
    charge_id = request.GET.get("charge_id", "")
    plan_key = request.GET.get("plan", "starter")

    if charge_id:
        request.shop.shopify_charge_id = charge_id
        request.shop.plan = plan_key
        request.shop.billing_status = "active"
        request.shop.save(update_fields=["shopify_charge_id", "plan", "billing_status"])

    return redirect("dashboard")


def _create_po_from_post(shop, post):
    """Create a PO from form POST data."""
    supplier = get_object_or_404(Supplier, id=post.get("supplier"), shop=shop)
    location = None
    if post.get("location"):
        location = get_object_or_404(Location, id=post.get("location"), shop=shop)

    po = PurchaseOrder.objects.create(
        shop=shop,
        supplier=supplier,
        location=location,
        po_number=post.get("po_number", "").strip(),
        currency=supplier.currency,
        payment_terms=supplier.payment_terms,
        notes=post.get("notes", "").strip(),
    )
    return po


def _add_line_items_from_post(po, post):
    """Add line items from form checkboxes."""
    for key, val in post.items():
        if key.startswith("qty_") and val:
            variant_id = key.replace("qty_", "")
            qty = int(val or 0)
            if qty <= 0:
                continue
            try:
                variant = Variant.objects.get(id=variant_id, shop=po.shop)
                POLineItem.objects.create(
                    purchase_order=po,
                    variant=variant,
                    sku=variant.sku,
                    description=str(variant),
                    quantity=qty,
                    unit_cost=variant.supplier_cost or variant.cost,
                )
            except Variant.DoesNotExist:
                continue
