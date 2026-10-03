from datetime import datetime

DEMO_TODAY = datetime(2026, 2, 1)
MERCHANT_ID = "M001"
SMALL_CUSTOMER_ID = "C001"
LARGE_CUSTOMER_ID = "C061"

SEGMENT_PRIORITY = ["Dormant", "High Value", "Loyal", "One-time", "Regular"]

OFFER_TYPES = ["FLAT_CASHBACK", "PERCENT_DISCOUNT", "FREE_ITEM", "STOCK_CLEARANCE"]
OFFER_LABELS = {
    "FLAT_CASHBACK": "Flat cashback",
    "PERCENT_DISCOUNT": "Percent off",
    "FREE_ITEM": "Free item",
    "STOCK_CLEARANCE": "Stock clearance sale",
}
CAMPAIGN_GOALS = [
    "Bring back dormant customers",
    "Reward loyal customers",
    "Increase repeat visits",
    "Push high-value customers",
]
