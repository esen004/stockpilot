"""Shopify GraphQL Admin API client for StockPilot."""

import requests
from django.conf import settings
from django.utils import timezone

from .models import Shop, Location, Product, Variant, InventoryLevel


class ShopifyClient:
    """Thin wrapper around Shopify GraphQL Admin API."""

    def __init__(self, shop: Shop):
        self.shop = shop
        self.base_url = (
            f"https://{shop.shopify_domain}/admin/api/{settings.SHOPIFY_API_VERSION}/graphql.json"
        )
        self.headers = {
            "X-Shopify-Access-Token": shop.access_token,
            "Content-Type": "application/json",
        }

    def query(self, gql: str, variables: dict = None) -> dict:
        payload = {"query": gql}
        if variables:
            payload["variables"] = variables
        resp = requests.post(self.base_url, json=payload, headers=self.headers, timeout=30)
        if resp.status_code in (401, 403):
            self.shop.is_active = False
            self.shop.save(update_fields=["is_active"])
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise Exception(f"GraphQL errors: {data['errors']}")
        return data.get("data", {})

    # --- Sync Methods ---

    def sync_locations(self):
        """Sync all locations from Shopify."""
        gql = """
        {
            locations(first: 50) {
                edges {
                    node {
                        id
                        name
                        isActive
                    }
                }
            }
        }
        """
        data = self.query(gql)
        for edge in data.get("locations", {}).get("edges", []):
            node = edge["node"]
            loc_id = node["id"].split("/")[-1]
            Location.objects.update_or_create(
                shop=self.shop,
                shopify_location_id=loc_id,
                defaults={
                    "name": node["name"],
                    "is_active": node["isActive"],
                },
            )

    def sync_products(self):
        """Sync products and variants from Shopify, respecting SKU limits."""
        from django.conf import settings as django_settings
        plan = django_settings.STOCKPILOT_PLANS.get(self.shop.plan, django_settings.STOCKPILOT_PLANS["starter"])
        sku_limit = plan.get("sku_limit")  # None = unlimited

        cursor = None
        has_next = True
        variant_count = 0

        while has_next:
            gql = """
            query($cursor: String) {
                products(first: 50, after: $cursor) {
                    pageInfo { hasNextPage endCursor }
                    edges {
                        node {
                            id
                            title
                            vendor
                            productType
                            status
                            featuredMedia {
                                preview {
                                    image { url }
                                }
                            }
                            variants(first: 100) {
                                edges {
                                    node {
                                        id
                                        title
                                        sku
                                        barcode
                                        price
                                        inventoryItem {
                                            id
                                            unitCost { amount }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            """
            data = self.query(gql, {"cursor": cursor})
            products_data = data.get("products", {})
            page_info = products_data.get("pageInfo", {})

            for edge in products_data.get("edges", []):
                node = edge["node"]
                product_id = node["id"].split("/")[-1]

                image_url = ""
                if node.get("featuredMedia"):
                    preview = node["featuredMedia"].get("preview", {})
                    if preview and preview.get("image"):
                        image_url = preview["image"].get("url", "")

                product, _ = Product.objects.update_or_create(
                    shop=self.shop,
                    shopify_product_id=product_id,
                    defaults={
                        "title": node["title"],
                        "vendor": node.get("vendor", ""),
                        "product_type": node.get("productType", ""),
                        "status": node.get("status", "ACTIVE").lower(),
                        "image_url": image_url,
                    },
                )

                for v_edge in node.get("variants", {}).get("edges", []):
                    if sku_limit and variant_count >= sku_limit:
                        has_next = False
                        break

                    v = v_edge["node"]
                    variant_id = v["id"].split("/")[-1]
                    inv_item = v.get("inventoryItem", {})
                    inv_item_id = inv_item["id"].split("/")[-1] if inv_item.get("id") else ""
                    cost = 0
                    if inv_item.get("unitCost"):
                        cost = float(inv_item["unitCost"].get("amount", 0))

                    Variant.objects.update_or_create(
                        shop=self.shop,
                        shopify_variant_id=variant_id,
                        defaults={
                            "product": product,
                            "shopify_inventory_item_id": inv_item_id,
                            "title": v.get("title") or "Default Title",
                            "sku": v.get("sku") or "",
                            "barcode": v.get("barcode") or "",
                            "price": float(v.get("price") or 0),
                            "cost": cost,
                        },
                    )
                    variant_count += 1

            has_next = page_info.get("hasNextPage", False)
            cursor = page_info.get("endCursor")

        self.shop.last_product_sync = timezone.now()
        self.shop.save(update_fields=["last_product_sync"])

    def sync_inventory_levels(self):
        """Sync inventory levels for all locations."""
        locations = Location.objects.filter(shop=self.shop, is_active=True)

        for location in locations:
            cursor = None
            has_next = True

            while has_next:
                gql = """
                query($locationId: ID!, $cursor: String) {
                    location(id: $locationId) {
                        inventoryLevels(first: 50, after: $cursor) {
                            pageInfo { hasNextPage endCursor }
                            edges {
                                node {
                                    id
                                    quantities(names: ["available", "on_hand", "incoming", "committed"]) {
                                        name
                                        quantity
                                    }
                                    item {
                                        id
                                        variant { id }
                                    }
                                }
                            }
                        }
                    }
                }
                """
                location_gid = f"gid://shopify/Location/{location.shopify_location_id}"
                data = self.query(gql, {"locationId": location_gid, "cursor": cursor})

                loc_data = data.get("location", {})
                inv_levels = loc_data.get("inventoryLevels", {})
                page_info = inv_levels.get("pageInfo", {})

                for edge in inv_levels.get("edges", []):
                    node = edge["node"]
                    item = node.get("item", {})
                    variant_ref = item.get("variant", {})
                    if not variant_ref or not variant_ref.get("id"):
                        continue

                    variant_id = variant_ref["id"].split("/")[-1]

                    quantities = {q["name"]: q["quantity"] for q in node.get("quantities", [])}

                    try:
                        variant = Variant.objects.get(
                            shop=self.shop, shopify_variant_id=variant_id
                        )
                        InventoryLevel.objects.update_or_create(
                            variant=variant,
                            location=location,
                            defaults={
                                "available": quantities.get("available", 0),
                                "on_hand": quantities.get("on_hand", 0),
                                "incoming": quantities.get("incoming", 0),
                                "committed": quantities.get("committed", 0),
                            },
                        )
                    except Variant.DoesNotExist:
                        continue

                has_next = page_info.get("hasNextPage", False)
                cursor = page_info.get("endCursor")

    def adjust_inventory(self, inventory_item_id: str, location_id: str, delta: int, reason: str = "received"):
        """Adjust inventory quantity in Shopify."""
        gql = """
        mutation inventoryAdjust($input: InventoryAdjustQuantitiesInput!) {
            inventoryAdjustQuantities(input: $input) {
                inventoryAdjustmentGroup { id }
                userErrors { field message }
            }
        }
        """
        variables = {
            "input": {
                "reason": reason,
                "name": "available",
                "changes": [
                    {
                        "inventoryItemId": f"gid://shopify/InventoryItem/{inventory_item_id}",
                        "locationId": f"gid://shopify/Location/{location_id}",
                        "delta": delta,
                    }
                ],
            }
        }
        data = self.query(gql, variables)
        errors = data.get("inventoryAdjustQuantities", {}).get("userErrors", [])
        if errors:
            raise Exception(f"Inventory adjust failed: {errors}")
        return data

    def update_inventory_cost(self, inventory_item_id: str, cost: float, currency: str = "USD"):
        """Update unit cost of an inventory item in Shopify."""
        gql = """
        mutation updateCost($id: ID!, $input: InventoryItemInput!) {
            inventoryItemUpdate(id: $id, input: $input) {
                inventoryItem { id }
                userErrors { field message }
            }
        }
        """
        variables = {
            "id": f"gid://shopify/InventoryItem/{inventory_item_id}",
            "input": {
                "cost": cost,
            },
        }
        data = self.query(gql, variables)
        errors = data.get("inventoryItemUpdate", {}).get("userErrors", [])
        if errors:
            raise Exception(f"Cost update failed: {errors}")
        return data

    def sync_all(self):
        """Full sync: locations → products → inventory levels."""
        self.sync_locations()
        self.sync_products()
        self.sync_inventory_levels()
