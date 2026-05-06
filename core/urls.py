from django.urls import path
from . import views

urlpatterns = [
    # Shopify session token bounce page (REQUIRED for embedded auth)
    path("session-token-bounce", views.session_token_bounce, name="session_token_bounce"),

    # Legal
    path("privacy", views.privacy_policy, name="privacy"),

    # Dashboard
    path("", views.dashboard, name="dashboard"),
    path("sync", views.sync_from_shopify, name="sync"),

    # Suppliers
    path("suppliers", views.supplier_list, name="supplier_list"),
    path("suppliers/new", views.supplier_create, name="supplier_create"),
    path("suppliers/<int:supplier_id>", views.supplier_detail, name="supplier_detail"),
    path("suppliers/<int:supplier_id>/edit", views.supplier_edit, name="supplier_edit"),
    path("suppliers/<int:supplier_id>/delete", views.supplier_delete, name="supplier_delete"),

    # Purchase Orders
    path("purchase-orders", views.po_list, name="po_list"),
    path("purchase-orders/new", views.po_create, name="po_create"),
    path("purchase-orders/<int:po_id>", views.po_detail, name="po_detail"),
    path("purchase-orders/<int:po_id>/add-items", views.po_add_items, name="po_add_items"),
    path("purchase-orders/<int:po_id>/fill-shelves", views.po_fill_shelves, name="po_fill_shelves"),
    path("purchase-orders/<int:po_id>/mark-ordered", views.po_mark_ordered, name="po_mark_ordered"),
    path("purchase-orders/<int:po_id>/send-email", views.po_send_email, name="po_send_email"),
    path("purchase-orders/<int:po_id>/pdf", views.po_pdf, name="po_pdf"),
    path("purchase-orders/<int:po_id>/receive", views.po_receive, name="po_receive"),
    path("purchase-orders/<int:po_id>/labels", views.print_labels, name="print_labels"),

    # Inventory
    path("inventory", views.inventory_list, name="inventory_list"),
    path("inventory/fill-shelves", views.fill_shelves_global, name="fill_shelves_global"),
    path("inventory/sync", views.sync_from_shopify, name="inventory_sync"),

    # Stocktakes
    path("stocktakes", views.stocktake_list, name="stocktake_list"),
    path("stocktakes/new", views.stocktake_create, name="stocktake_create"),
    path("stocktakes/<int:stocktake_id>/count", views.stocktake_count, name="stocktake_count"),
    path("stocktakes/<int:stocktake_id>/review", views.stocktake_review, name="stocktake_review"),
    path("stocktakes/<int:stocktake_id>/apply", views.stocktake_apply, name="stocktake_apply"),

    # Transfers
    path("transfers", views.transfer_list, name="transfer_list"),
    path("transfers/suggestions", views.transfer_suggestions, name="transfer_suggestions"),
    path("transfers/new", views.transfer_create, name="transfer_create"),

    # Reports
    path("reports", views.reports_overview, name="reports"),
    path("reports/abc", views.report_abc, name="report_abc"),
    path("reports/low-stock", views.report_low_stock, name="report_low_stock"),
    path("reports/dead-stock", views.report_dead_stock, name="report_dead_stock"),
    path("reports/forecast", views.forecast_view, name="forecast"),

    # Billing
    path("billing/select", views.billing_select, name="billing_select"),
    path("billing/callback", views.billing_callback, name="billing_callback"),

    # Cron
    path("cron/weekly-report", views.weekly_report, name="weekly_report"),
]
