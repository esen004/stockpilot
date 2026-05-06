"""Shopify Billing API — subscription plan management via GraphQL."""

from django.conf import settings

from .models import Shop
from .shopify_client import ShopifyClient


def create_subscription(shop: Shop, plan_key: str) -> str:
    """Create a Shopify app subscription and return the confirmation URL.
    Merchant must visit confirmation URL to approve the charge.

    If the shop already has an active subscription on a different plan,
    cancel it first so the merchant can upgrade/downgrade cleanly without
    contacting support (App Store requirement 1.2.3).
    """
    plan = settings.STOCKPILOT_PLANS.get(plan_key)
    if not plan:
        raise ValueError(f"Unknown plan: {plan_key}")

    client = ShopifyClient(shop)

    if shop.shopify_charge_id and shop.plan != plan_key and shop.billing_status == "active":
        try:
            cancel_subscription(shop)
        except Exception:
            pass

    # test: false in production — Shopify automatically forces test mode on
    # development stores regardless of this flag, so real merchants get billed
    # while dev/review stores stay free. Setting test: true here would skip real
    # charges in production and silently break revenue.
    gql = """
    mutation appSubscriptionCreate(
        $name: String!,
        $returnUrl: URL!,
        $trialDays: Int!,
        $amount: Decimal!,
        $currencyCode: CurrencyCode!,
        $test: Boolean!
    ) {
        appSubscriptionCreate(
            name: $name,
            returnUrl: $returnUrl,
            trialDays: $trialDays,
            lineItems: [{
                plan: {
                    appRecurringPricingDetails: {
                        price: { amount: $amount, currencyCode: $currencyCode }
                        interval: EVERY_30_DAYS
                    }
                }
            }]
            test: $test
        ) {
            appSubscription {
                id
                status
            }
            confirmationUrl
            userErrors {
                field
                message
            }
        }
    }
    """

    variables = {
        "name": f"StockPilot {plan['name']}",
        "returnUrl": f"{settings.SHOPIFY_APP_URL}/billing/callback?plan={plan_key}",
        "trialDays": plan["trial_days"],
        "amount": str(plan["price"]),
        "currencyCode": "USD",
        "test": bool(settings.DEBUG),
    }

    data = client.query(gql, variables)
    result = data.get("appSubscriptionCreate", {})
    errors = result.get("userErrors", [])

    if errors:
        raise Exception(f"Billing error: {errors}")

    confirmation_url = result.get("confirmationUrl")
    subscription = result.get("appSubscription", {})

    if subscription.get("id"):
        shop.shopify_charge_id = subscription["id"].split("/")[-1]
        shop.plan = plan_key
        shop.save(update_fields=["shopify_charge_id", "plan"])

    return confirmation_url


def check_subscription_status(shop: Shop) -> dict:
    """Check current subscription status."""
    client = ShopifyClient(shop)

    gql = """
    {
        currentAppInstallation {
            activeSubscriptions {
                id
                name
                status
                currentPeriodEnd
                trialDays
                lineItems {
                    plan {
                        pricingDetails {
                            ... on AppRecurringPricing {
                                price { amount currencyCode }
                                interval
                            }
                        }
                    }
                }
            }
        }
    }
    """

    data = client.query(gql)
    subs = data.get("currentAppInstallation", {}).get("activeSubscriptions", [])

    if not subs:
        return {"status": "none", "plan": None}

    active = subs[0]
    return {
        "status": active.get("status", "").lower(),
        "name": active.get("name", ""),
        "period_end": active.get("currentPeriodEnd"),
        "trial_days": active.get("trialDays", 0),
    }


def cancel_subscription(shop: Shop) -> bool:
    """Cancel the active subscription."""
    if not shop.shopify_charge_id:
        return False

    client = ShopifyClient(shop)

    gql = """
    mutation appSubscriptionCancel($id: ID!) {
        appSubscriptionCancel(id: $id) {
            appSubscription { id status }
            userErrors { field message }
        }
    }
    """

    sub_gid = f"gid://shopify/AppSubscription/{shop.shopify_charge_id}"
    data = client.query(gql, {"id": sub_gid})
    errors = data.get("appSubscriptionCancel", {}).get("userErrors", [])

    if not errors:
        shop.billing_status = "cancelled"
        shop.save(update_fields=["billing_status"])
        return True

    return False
