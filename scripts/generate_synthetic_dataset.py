"""
Deterministic synthetic dataset generator for the Merchant Growth AI /
Paytm Merchant Offer Loop project.

Design principles (documented in data/synthetic/README.md):

1.  Behaviour first.  Customer behavioural cohorts are an INTERNAL generation
    concept and are never written to disk.  Segmentation, lifecycle change,
    high value, dormancy, cross-merchant asymmetry etc. must be *derived* by the
    application from `transactions.csv` only.

2.  Generation order = causal order:
        merchants -> customers -> transactions -> offers -> campaigns ->
        campaign_versions -> campaign_events -> campaign_recipients ->
        redemptions (+ the transactions they point at) -> audit_logs

3.  Every redemptions row points at a real transaction row, and every
    CONFIRMED redemption satisfies the offer rules exactly.  Imperfections
    (failed/reversed transactions, delivery failures, rejected AI copy,
    cancelled campaigns, invalid/duplicate redemptions) obey the rules instead
    of corrupting them.

Run:
    python scripts/generate_synthetic_dataset.py
"""

from __future__ import annotations

import calendar
import csv
import json
import math
import os
import random
from bisect import bisect_right
from collections import defaultdict
from datetime import date, datetime, time, timedelta

# --------------------------------------------------------------------------
# 0. Paths / constants
# --------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SYNTH_DIR = os.path.join(ROOT, "data", "synthetic")
BRIDGE_DIR = os.path.join(ROOT, "data")

SEED = 20261003
DEMO_TODAY = date(2026, 10, 3)              # "today" for every recency calc
END_OF_DAY = datetime(2026, 10, 3, 23, 59, 59)
ACTIVITY_START = datetime(2026, 1, 1, 0, 0, 0)
ACTIVITY_END = datetime(2026, 10, 3, 23, 59, 59)
MONTHS = list(range(1, 11))                 # Oct is truncated to the 1st-3rd
DAEMON_NOW = datetime(2026, 10, 3, 12, 0, 0)

CSV_COLUMNS = {
    "merchants": [
        "merchant_id", "store_name", "category", "city", "default_margin_pct",
        "created_at", "is_active",
    ],
    "customers": [
        "customer_id", "customer_reference", "city", "created_at", "status",
        "status_changed_at", "signup_channel",
    ],
    "transactions": [
        "transaction_id", "customer_id", "merchant_id", "transaction_reference",
        "amount", "transaction_time", "status", "payment_method", "channel",
    ],
    "offers": [
        "offer_id", "merchant_id", "name", "discount_type", "discount_value",
        "minimum_spend", "max_discount", "start_time", "expiry_time", "budget",
        "status", "created_at", "updated_at",
    ],
    "campaigns": [
        "campaign_id", "merchant_id", "offer_id", "objective",
        "audience_definition", "status", "approved_version",
        "experiment_enabled", "created_at", "updated_at",
    ],
    "campaign_versions": [
        "campaign_version_id", "campaign_id", "version_number", "headline",
        "message", "call_to_action", "ai_model", "prompt_version", "status",
        "rejection_reason", "created_at", "approved_at",
    ],
    "campaign_recipients": [
        "recipient_id", "campaign_id", "customer_id", "eligibility_status",
        "delivery_status", "sent_at", "delivered_at", "experiment_group",
        "coupon_code", "conversation_id", "suppression_reason",
    ],
    "redemptions": [
        "redemption_id", "campaign_id", "offer_id", "customer_id",
        "transaction_id", "gross_bill_amount", "discount_applied", "redeemed_at",
        "status", "rejection_reason",
    ],
    "campaign_events": [
        "event_id", "campaign_id", "event_type", "event_time", "metadata",
    ],
    "audit_logs": [
        "audit_id", "actor_id", "action", "entity_type", "entity_id",
        "timestamp", "metadata",
    ],
}

# --------------------------------------------------------------------------
# 1. Reference configuration
# --------------------------------------------------------------------------

MERCHANT_SEED = [
    # id, store_name, category, city, margin_pct, is_active, inactive_since
    ("MER-0001", "Brew Junction", "CAFE", "Mumbai", 32.0, True, None),
    ("MER-0002", "Saffron Tandoor House", "RESTAURANT", "Pune", 28.0, True, None),
    ("MER-0003", "Glow & Mane Studio", "SALON", "Bengaluru", 34.0, True, None),
    ("MER-0004", "Indigo Thread Apparel", "CLOTHING", "Hyderabad", 38.0, True, None),
    ("MER-0005", "GreenLeaf Daily Mart", "GROCERY", "Delhi", 12.0, True, None),
    ("MER-0006", "Crumb Theory Bakes", "BAKERY", "Chennai", 30.0, True, None),
    ("MER-0007", "PulseForge Fitness", "FITNESS", "Ahmedabad", 35.0, True, None),
    ("MER-0008", "VoltCave Electronics", "ELECTRONICS", "Jaipur", 18.0, True, None),
    ("MER-0009", "Aura Beauty Lab", "BEAUTY", "Kolkata", 36.0, True, None),
    ("MER-0010", "MediPlus Pharmacy", "PHARMACY", "Mumbai", 15.0, True, None),
    ("MER-0011", "PawPal Pet Care", "PET", "Pune", 32.0, True, None),
    ("MER-0012", "Chapter One Books", "BOOKSTORE", "Bengaluru", 28.0, True, None),
    ("MER-0013", "Chai & Co Cafe", "CAFE", "Hyderabad", 31.0, True, None),
    ("MER-0014", "Tandoori Nights", "RESTAURANT", "Delhi", 27.0, True, None),
    ("MER-0015", "StyleMint Boutique", "CLOTHING", "Chennai", 40.0, True, None),
    ("MER-0016", "FreshKart Supermarket", "GROCERY", "Ahmedabad", 11.0, True, None),
    ("MER-0017", "Urban Grind Coffee", "CAFE", "Jaipur", 33.0, True, None),
    ("MER-0018", "Curry Leaf Kitchen", "RESTAURANT", "Kolkata", 26.0, True, None),
    ("MER-0019", "Zenith Electronics", "ELECTRONICS", "Mumbai", 17.0, False,
     date(2026, 9, 1)),
    ("MER-0020", "Bloom Botanicals", "BEAUTY", "Mumbai", 35.0, False,
     date(2026, 9, 1)),
]

CAT_AMOUNT = {
    "CAFE": (120, 950),
    "RESTAURANT": (300, 3500),
    "SALON": (400, 6000),
    "CLOTHING": (500, 12000),
    "GROCERY": (200, 4000),
    "BAKERY": (80, 1200),
    "FITNESS": (500, 9000),
    "ELECTRONICS": (1000, 50000),
    "BEAUTY": (300, 8000),
    "PHARMACY": (100, 3000),
    "PET": (250, 4500),
    "BOOKSTORE": (150, 5000),
}

CAT_VISIT_RATE = {
    "CAFE": 11.0, "BAKERY": 5.0, "RESTAURANT": 6.0, "GROCERY": 8.5,
    "FITNESS": 4.2, "PHARMACY": 5.0, "SALON": 3.0, "CLOTHING": 2.8,
    "BEAUTY": 3.0, "PET": 2.5, "BOOKSTORE": 2.6, "ELECTRONICS": 0.9,
}

# Jan..Oct seasonality index per category
CAT_SEASONALITY = {
    "CAFE": [1.20, 1.06, 0.95, 0.88, 0.84, 0.90, 1.00, 1.00, 1.06, 1.18],
    "RESTAURANT": [0.95, 0.98, 1.02, 1.04, 1.06, 1.02, 1.00, 1.02, 1.06, 1.15],
    "SALON": [0.85, 1.05, 0.95, 0.95, 1.05, 1.15, 0.95, 0.95, 1.05, 1.10],
    "CLOTHING": [0.70, 0.75, 0.90, 0.95, 0.90, 0.95, 1.00, 1.15, 1.10, 1.60],
    "GROCERY": [1.00, 0.95, 1.00, 1.02, 1.00, 0.98, 1.00, 1.02, 1.02, 1.15],
    "BAKERY": [1.05, 1.00, 0.98, 0.98, 0.95, 0.95, 0.98, 1.00, 1.02, 1.20],
    "FITNESS": [1.60, 1.05, 0.90, 0.85, 0.85, 0.95, 1.00, 1.05, 1.10, 1.05],
    "ELECTRONICS": [0.95, 0.90, 1.00, 1.10, 0.95, 0.85, 0.95, 1.20, 1.10, 1.70],
    "BEAUTY": [1.00, 1.10, 1.05, 1.00, 1.00, 0.95, 0.95, 1.10, 1.05, 1.25],
    "PHARMACY": [1.10, 1.00, 0.95, 1.00, 0.95, 1.05, 1.05, 1.00, 1.00, 1.05],
    "PET": [0.95, 1.00, 1.00, 1.00, 1.05, 1.00, 1.05, 1.00, 1.00, 1.05],
    "BOOKSTORE": [1.20, 0.95, 0.85, 0.95, 0.90, 0.95, 1.05, 1.25, 1.05, 1.30],
}

CAT_HOURS = {
    "CAFE": ((7, 11), (16, 20)),
    "RESTAURANT": ((12, 15), (19, 22)),
    "SALON": ((10, 20),),
    "CLOTHING": ((11, 21),),
    "GROCERY": ((9, 13), (17, 21)),
    "BAKERY": ((7, 12), (16, 19)),
    "FITNESS": ((6, 9), (17, 21)),
    "ELECTRONICS": ((10, 21),),
    "BEAUTY": ((10, 21),),
    "PHARMACY": ((8, 14), (17, 22)),
    "PET": ((10, 19),),
    "BOOKSTORE": ((11, 20),),
}

PAYMENT_WEIGHTS = {
    "ELECTRONICS": {"UPI": 0.45, "WALLET": 0.15, "CARD": 0.40},
    "CLOTHING": {"UPI": 0.55, "WALLET": 0.20, "CARD": 0.25},
    "FITNESS": {"UPI": 0.68, "WALLET": 0.18, "CARD": 0.14},
    "default": {"UPI": 0.64, "WALLET": 0.22, "CARD": 0.14},
}
CHANNEL_WEIGHTS = {"APP": 0.62, "POS": 0.26, "WEB_CHECKOUT": 0.12}

COHORT_CATEGORIES = {
    "A_FREQUENT_ACTIVE": {"CAFE": 4, "GROCERY": 3, "BAKERY": 3, "RESTAURANT": 3,
                          "PHARMACY": 2, "FITNESS": 2},
    "B_FORMERLY_FREQUENT_INACTIVE": {"CAFE": 4, "GROCERY": 3, "RESTAURANT": 3,
                                     "BAKERY": 3, "PHARMACY": 3, "FITNESS": 1},
    "C_NEW_CUSTOMER": {"CAFE": 3, "RESTAURANT": 3, "GROCERY": 2, "CLOTHING": 2,
                       "BEAUTY": 2, "SALON": 2, "PET": 1, "BOOKSTORE": 1},
    "D_ONE_TIME": {"CAFE": 2, "RESTAURANT": 2, "GROCERY": 2, "SALON": 1,
                   "BEAUTY": 1, "PET": 1, "ELECTRONICS": 1},
    "E_HIGH_VALUE": {"ELECTRONICS": 4, "CLOTHING": 3, "FITNESS": 2, "BEAUTY": 2,
                     "SALON": 2, "RESTAURANT": 1},
    "F_FREQUENT_LOW_VALUE": {"CAFE": 5, "BAKERY": 4, "BOOKSTORE": 2, "PET": 2,
                             "PHARMACY": 2, "GROCERY": 2},
    "G_HIGH_VALUE_INFREQUENT": {"ELECTRONICS": 5, "CLOTHING": 3, "FITNESS": 2,
                                "SALON": 1, "RESTAURANT": 1},
    "H_RECENTLY_REACTIVATED": {"RESTAURANT": 3, "CAFE": 3, "CLOTHING": 2,
                                "BEAUTY": 2, "GROCERY": 2, "SALON": 1},
    "I_SEASONAL": {"RESTAURANT": 3, "BEAUTY": 2, "CLOTHING": 3, "PET": 2,
                   "BOOKSTORE": 2, "SALON": 2, "ELECTRONICS": 1},
    "J_MULTI_MERCHANT": {"CAFE": 3, "RESTAURANT": 3, "GROCERY": 3, "CLOTHING": 2,
                         "BEAUTY": 2, "FITNESS": 2, "PET": 1, "BOOKSTORE": 1},
    "K_DECLINING": {"CAFE": 4, "GROCERY": 3, "RESTAURANT": 3, "PHARMACY": 2,
                    "FITNESS": 2},
    "L_GROWING": {"CAFE": 3, "RESTAURANT": 3, "GROCERY": 3, "CLOTHING": 2,
                  "BEAUTY": 2, "FITNESS": 2, "SALON": 1},
    "M_DORMANT_LONG_TERM": {"RESTAURANT": 3, "CLOTHING": 3, "SALON": 2,
                            "ELECTRONICS": 2, "PET": 2, "BEAUTY": 2},
    "N_RECENT_NO_REPEAT": {"CLOTHING": 3, "BEAUTY": 2, "RESTAURANT": 3,
                           "CAFE": 2, "PET": 1, "SALON": 1},
    "Z_NO_PURCHASE": {"CAFE": 3, "RESTAURANT": 3, "CLOTHING": 2, "BEAUTY": 2,
                      "GROCERY": 2, "ELECTRONICS": 1},
}

COHORT_SIZES = [
    ("A_FREQUENT_ACTIVE", 100),
    ("B_FORMERLY_FREQUENT_INACTIVE", 100),
    ("C_NEW_CUSTOMER", 90),
    ("D_ONE_TIME", 70),
    ("E_HIGH_VALUE", 60),
    ("F_FREQUENT_LOW_VALUE", 80),
    ("G_HIGH_VALUE_INFREQUENT", 50),
    ("H_RECENTLY_REACTIVATED", 60),
    ("I_SEASONAL", 60),
    ("J_MULTI_MERCHANT", 90),
    ("K_DECLINING", 70),
    ("L_GROWING", 70),
    ("M_DORMANT_LONG_TERM", 50),
    ("N_RECENT_NO_REPEAT", 30),
    ("Z_NO_PURCHASE", 20),
]
assert sum(n for _, n in COHORT_SIZES) == 1000

AMOUNT_LEVEL = {
    "A_FREQUENT_ACTIVE": "typical",
    "B_FORMERLY_FREQUENT_INACTIVE": "typical",
    "C_NEW_CUSTOMER": "typical",
    "D_ONE_TIME": "typical",
    "E_HIGH_VALUE": "high",
    "F_FREQUENT_LOW_VALUE": "low",
    "G_HIGH_VALUE_INFREQUENT": "high",
    "H_RECENTLY_REACTIVATED": "typical",
    "I_SEASONAL": "typical_high",
    "J_MULTI_MERCHANT": "typical",
    "K_DECLINING": "typical",
    "L_GROWING": "typical",
    "M_DORMANT_LONG_TERM": "typical",
    "N_RECENT_NO_REPEAT": "high",
    "Z_NO_PURCHASE": "typical",
}

OBJECTIVES = [
    "REACTIVATE_INACTIVE",
    "REWARD_FREQUENT_CUSTOMERS",
    "WELCOME_NEW_CUSTOMERS",
    "INCREASE_REPEAT_PURCHASE",
    "PROMOTE_HIGH_VALUE_OFFER",
    "SEASONAL_PROMOTION",
]

AUDIENCE_RULES = {
    "REACTIVATE_INACTIVE": {
        "rule": "DORMANT_REENGAGE", "min_frequency": 1,
        "recency_days_min": 30, "recency_days_max": 400,
    },
    "REWARD_FREQUENT_CUSTOMERS": {
        "rule": "TOP_FREQUENCY", "min_frequency": 2,
        "recency_days_max": 90, "frequency_percentile_min": 30,
    },
    "WELCOME_NEW_CUSTOMERS": {
        "rule": "RECENTLY_ACQUIRED", "first_purchase_within_days": 90,
        "max_frequency": 8,
    },
    "INCREASE_REPEAT_PURCHASE": {
        "rule": "RECENT_ACTIVE", "min_frequency": 1, "recency_days_max": 90,
    },
    "PROMOTE_HIGH_VALUE_OFFER": {
        "rule": "TOP_SPENDERS", "min_frequency": 1, "spend_percentile_min": 30,
    },
    "SEASONAL_PROMOTION": {
        "rule": "BROAD_REACH", "min_frequency": 1, "recency_days_max": 280,
    },
}

AI_MODELS = ["mock-copywriter-v1", "mock-copywriter-v2", "template-fallback-v1"]
PROMPT_VERSIONS = ["p1.0", "p1.1", "p1.2", "p2.0"]
CTA_VALID = [
    "Tap to pay with Paytm", "Pay now with Paytm", "Use code at checkout",
    "Claim offer in Paytm", "Pay with Paytm to redeem",
]
CTA_INVALID = [
    "click here http://tiny-url.example/offer",
    "DM us on +91-98XXXXXX21 to claim",
    "!!!ACT NOW!!!",
    "Reply YES to claim",
]
REJECTION_REASONS_COPY = [
    "DISCOUNT_VALUE_MISMATCH", "MINIMUM_SPEND_MISMATCH", "UNSUPPORTED_CLAIM",
    "EXCESSIVE_DISCOUNT_LANGUAGE", "INVALID_CALL_TO_ACTION", "MISSING_TERMS",
]

OFFER_NAME_TEMPLATES = {
    "FIXED": ["Flat {v} off above {m}", "Rs {v} off on bills above {m}",
              "Save {v} on your next bill of {m}+"],
    "PERCENTAGE": ["{p}% off above {m}", "Flat {p}% off on orders above {m}",
                   "Get {p}% off when you spend {m}+"],
}

AUDIT_ACTIONS = {
    "LOGIN", "CREATE_OFFER", "UPDATE_OFFER", "CREATE_CAMPAIGN",
    "GENERATE_AI_CONTENT", "APPROVE_CAMPAIGN", "REJECT_CAMPAIGN",
    "LAUNCH_CAMPAIGN", "CANCEL_CAMPAIGN", "VIEW_ANALYTICS",
    "REDEMPTION_ATTEMPT", "REDEMPTION_CONFIRMED", "REDEMPTION_REJECTED",
}

# --------------------------------------------------------------------------
# 2. Helpers
# --------------------------------------------------------------------------


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def parse_iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")


def month_day_count(month: int) -> int:
    if month == 10:
        return 3
    return calendar.monthrange(2026, month)[1]


def weighted_choice(rng: random.Random, weights: dict) -> str:
    total = sum(weights.values())
    pick = rng.random() * total
    acc = 0.0
    for key, weight in weights.items():
        acc += weight
        if pick <= acc:
            return key
    return list(weights)[-1]


def money(value: float) -> float:
    return round(value + 1e-9, 2)


def ref_code(rng: random.Random, length: int = 9) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(rng.choice(alphabet) for _ in range(length))


def poisson(rng: random.Random, lam: float) -> int:
    """Knuth's exact Poisson sampler (small lambda only)."""
    if lam <= 0:
        return 0
    target = math.exp(-lam)
    k = 0
    p = 1.0
    while True:
        p *= rng.random()
        if p <= target or k >= 15:
            return k
        k += 1


def sample_amount(rng: random.Random, category: str, level: str) -> float:
    lo, hi = CAT_AMOUNT[category]
    span = hi - lo
    if level == "low":
        mu, sigma = math.log(lo + span * 0.20), 0.30
    elif level == "high":
        mu, sigma = math.log(lo + span * 0.72), 0.34
    elif level == "typical_high":
        mu, sigma = math.log(lo + span * 0.55), 0.40
    else:
        mu, sigma = math.log(lo + span * 0.38), 0.48
    value = max(lo, min(hi, rng.lognormvariate(mu, sigma)))
    if rng.random() < 0.72:
        value = float(int(round(value)))
    return money(value)


def make_timestamp(rng: random.Random, month: int, day: int, category: str) -> datetime:
    lo, hi = rng.choice(CAT_HOURS[category])
    hour = rng.randint(lo, max(lo, hi - 1))
    return datetime(2026, month, day, hour, rng.randint(0, 59), rng.randint(0, 59))


# --------------------------------------------------------------------------
# 3. Merchants
# --------------------------------------------------------------------------


def build_merchants(rng: random.Random) -> list:
    merchants = []
    for mid, name, cat, city, margin, is_active, inactive_since in MERCHANT_SEED:
        created = datetime(2024, rng.randint(1, 12), rng.randint(1, 28),
                           rng.randint(9, 18), rng.randint(0, 59))
        merchants.append({
            "merchant_id": mid,
            "store_name": name,
            "category": cat,
            "city": city,
            "default_margin_pct": margin,
            "created_at": iso(created),
            "is_active": "true" if is_active else "false",
            "_inactive_since": inactive_since,
        })
    return merchants


# --------------------------------------------------------------------------
# 4. Customers + internal behaviour plans
# --------------------------------------------------------------------------


def assign_cohorts() -> dict:
    rng = random.Random(SEED + 2)
    cohort_of = {}
    idx = 1
    for cohort, size in COHORT_SIZES:
        for _ in range(size):
            cohort_of["CUS-%06d" % idx] = cohort
            idx += 1
    # deterministic demo anchors named in the specification
    swap_a = cohort_of["CUS-000123"]
    cohort_of["CUS-000123"] = "J_MULTI_MERCHANT"
    free = [c for c, v in cohort_of.items()
            if v == "A_FREQUENT_ACTIVE" and c not in ("CUS-000001",)]
    cohort_of[rng.choice(free)] = swap_a
    cohort_of["CUS-000001"] = "A_FREQUENT_ACTIVE"
    cohort_of["CUS-000002"] = "B_FORMERLY_FREQUENT_INACTIVE"
    cohort_of["CUS-000456"] = "B_FORMERLY_FREQUENT_INACTIVE"
    return cohort_of


# shopper footprint tunables: how many merchants a customer touches and how the
# purchase volume is spread between the habitual merchant and the rest
HOME_W = (0.42, 0.56)          # weight of the habitual merchant
REG_N = (3, 5)                 # number of regularly visited merchants
REG_W = (0.09, 0.16)
OCC_RANGE = (7, 11)            # number of occasionally visited merchants
OCC_W = (0.035, 0.075)
PLAN_SCALE = 1.40              # multiplies every monthly purchase plan


def monthly_plan(cohort: str, rng: random.Random) -> list:
    """Expected purchase count per month, Jan..Oct (index 0..9)."""
    plan = [0.0] * 10
    if cohort == "A_FREQUENT_ACTIVE":
        base = rng.uniform(2.7, 4.3)
        for m in range(9):
            plan[m] = base * rng.uniform(0.75, 1.30)
        plan[9] = float(rng.choice([0, 1, 1, 2]))
    elif cohort == "B_FORMERLY_FREQUENT_INACTIVE":
        for m in range(5):
            plan[m] = rng.uniform(3.4, 5.6)
        plan[5] = rng.choice([0.5, 1.0, 1.0, 1.5])
        plan[6] = plan[7] = plan[8] = plan[9] = 0.0
    elif cohort == "C_NEW_CUSTOMER":
        start = rng.choice([6, 6, 7])
        for m in range(start, 9):
            plan[m] = rng.uniform(0.8, 2.4)
        plan[9] = float(rng.choice([0, 1, 1]))
    elif cohort == "D_ONE_TIME":
        plan[rng.choice([2, 3, 4, 4, 5, 6, 7])] = 1.0 if rng.random() < 0.75 else 2.0
    elif cohort == "E_HIGH_VALUE":
        for m in rng.sample([5, 6, 7, 8], 3):
            plan[m] = 1.0
    elif cohort == "F_FREQUENT_LOW_VALUE":
        base = rng.uniform(1.6, 2.4)
        for m in range(9):
            plan[m] = base * rng.uniform(0.7, 1.35)
        plan[9] = float(rng.choice([0, 1]))
    elif cohort == "G_HIGH_VALUE_INFREQUENT":
        for m in rng.sample([4, 6, 7, 8], 2):
            plan[m] = 1.0
    elif cohort == "H_RECENTLY_REACTIVATED":
        early = rng.choice([2, 3, 4])
        plan[early] = rng.uniform(1.0, 2.2)
        if rng.random() < 0.5:
            plan[early + 1] = 1.0
        plan[7] = 1.0
        plan[8] = rng.uniform(2.4, 3.6)
    elif cohort == "I_SEASONAL":
        pattern = rng.choice([[2, 5, 8], [1, 4, 8], [2, 6, 8], [0, 4, 7]])
        for m, c in zip(pattern, [1, 1, 5] if pattern[-1] == 8 else [1, 1, 4]):
            plan[m] = float(c)
    elif cohort == "J_MULTI_MERCHANT":
        base = rng.uniform(2.0, 3.2)
        for m in range(9):
            plan[m] = base * rng.uniform(0.7, 1.3)
        plan[9] = float(rng.choice([0, 1]))
    elif cohort == "K_DECLINING":
        for m, c in enumerate([8, 7, 6, 5, 4, 2, 1, 0, 0]):
            plan[m] = max(0.0, c * rng.uniform(0.75, 1.25))
        plan[8] = plan[9] = 0.0
    elif cohort == "L_GROWING":
        for m, c in enumerate([1, 1, 2, 2, 3, 4, 5, 6, 7]):
            plan[m] = max(0.0, c * rng.uniform(0.7, 1.3))
        plan[9] = float(rng.choice([0, 1]))
    elif cohort == "M_DORMANT_LONG_TERM":
        for m, c in enumerate([3, 2, 1, 0, 0, 0, 0, 0, 0]):
            plan[m] = float(c)
    elif cohort == "N_RECENT_NO_REPEAT":
        plan[8] = 2.0
    elif cohort == "Z_NO_PURCHASE":
        pass
    else:
        raise ValueError(cohort)
    return [round(v * PLAN_SCALE, 3) for v in plan]


def build_customers(merchants: list, cohort_of: dict, rng: random.Random) -> list:
    by_cat = defaultdict(list)
    for m in merchants:
        by_cat[m["category"]].append(m["merchant_id"])
    merchant_cat = {m["merchant_id"]: m["category"] for m in merchants}
    merchant_city = {m["merchant_id"]: m["city"] for m in merchants}
    all_cities = sorted({m["city"] for m in merchants})
    load = defaultdict(int)
    all_merchants = list(merchant_cat)

    def least_loaded(candidates):
        best, best_key = None, None
        for mid in candidates:
            key = (load[mid], rng.random())
            if best_key is None or key < best_key:
                best_key, best = key, mid
        return best

    customers = []
    for index in range(1, 1001):
        cid = "CUS-%06d" % index
        cohort = cohort_of[cid]
        cats = list(COHORT_CATEGORIES[cohort])

        home = least_loaded([m for cat in cats for m in by_cat[cat]])
        load[home] += 1

        # A shopper on a payments platform touches several merchants: one
        # habitual home merchant plus regular and occasional merchants.  This is
        # what makes customer x merchant segmentation (and the
        # frequent-at-A / inactive-at-B case) meaningful.
        weights = {home: rng.uniform(*HOME_W)}
        assigned = [home]
        n_regular = rng.randint(*REG_N)
        for _ in range(n_regular):
            pick = least_loaded([m for m in all_merchants if m not in assigned])
            weights[pick] = rng.uniform(*REG_W)
            assigned.append(pick)
            load[pick] += 1
        n_occasional = rng.randint(*OCC_RANGE)
        for _ in range(n_occasional):
            pick = least_loaded([m for m in all_merchants if m not in assigned])
            weights[pick] = rng.uniform(*OCC_W)
            assigned.append(pick)
            load[pick] += 1

        if cid == "CUS-000123":            # spec section 25 anchor
            extra = {mid: weights.get(mid, 0.03) for mid in all_merchants
                     if mid not in weights}
            weights = {"MER-0001": 0.58, "MER-0007": 0.05}
            weights.update({k: v for k, v in extra.items()
                            if k not in weights})
            weights["MER-0007"] = 0.06

        home_cat = merchant_cat[home]
        # multi-merchant / cross-merchant anchor: one non-home merchant goes
        # quiet after April, so the customer is frequent at A and inactive at B
        quiet = None
        if cohort == "J_MULTI_MERCHANT":
            non_home = [m for m in assigned if m != home]
            if non_home and rng.random() < 0.55:
                quiet = non_home[0] if rng.random() < 0.5 else non_home[-1]
        if cid == "CUS-000123":
            quiet = "MER-0007"

        plan = monthly_plan(cohort, rng)
        city = merchant_city[home] if rng.random() < 0.72 else rng.choice(all_cities)
        created_date = date(2026, 1, 1) - timedelta(days=rng.randint(25, 420))
        created = datetime.combine(created_date,
                                   time(rng.randint(8, 21), rng.randint(0, 59)))
        if cohort == "Z_NO_PURCHASE":
            created = datetime(2026, rng.randint(1, 9), rng.randint(1, 28),
                               rng.randint(8, 21), rng.randint(0, 59))

        customers.append({
            "customer_id": cid,
            "customer_reference": "CREF-" + ref_code(rng),
            "city": city,
            "created_at": iso(created),
            "status": "INACTIVE" if rng.random() < 0.055 else "ACTIVE",
            "status_changed_at": "",
            "signup_channel": weighted_choice(rng, {
                "ORGANIC": 34, "PAID_AD": 18, "REFERRAL": 14,
                "LOYALTY_PROGRAM": 12, "ONBOARDING_PARTNER": 12,
                "REACTIVATION_CAMPAIGN": 10}),
            "_cohort": cohort,
            "_home": home,
            "_merchants": weights,
            "_quiet": quiet,
            "_plan": plan,
            "_level": AMOUNT_LEVEL[cohort],
            "_responsive": rng.random() < 0.28,
        })
    return customers


# --------------------------------------------------------------------------
# 5. Transactions (the source of every downstream truth)
# --------------------------------------------------------------------------


def generate_transactions(customers: list, merchants: list,
                          rng: random.Random) -> list:
    by_id = {m["merchant_id"]: m for m in merchants}
    inactive_since = {m["merchant_id"]: m["_inactive_since"] for m in merchants}
    rows = []
    for cust in customers:
        cohort = cust["_cohort"]
        if cohort == "Z_NO_PURCHASE":
            continue
        plan = cust["_plan"]
        home = cust["_home"]
        weights = cust["_merchants"]
        quiet = cust["_quiet"]
        visit_mult = CAT_VISIT_RATE[by_id[home]["category"]] / CAT_VISIT_RATE["CAFE"]

        for month in MONTHS:
            expected = plan[month - 1] * (0.55 + 0.75 *
                                          CAT_SEASONALITY[by_id[home]["category"]][month - 1])
            expected *= visit_mult
            count = poisson(rng, expected)
            if count <= 0:
                continue
            days = sorted(rng.sample(range(1, month_day_count(month) + 1),
                                     min(count, month_day_count(month))))
            for day in days:
                candidates = {mid: w for mid, w in weights.items()
                              if mid != quiet or month <= 4}
                if not candidates:
                    candidates = {home: 1.0}
                mid = weighted_choice(rng, candidates)
                cutoff = inactive_since.get(mid)
                if cutoff and date(2026, month, day) >= cutoff:
                    continue
                ts = make_timestamp(rng, month, day, by_id[mid]["category"])
                amount = sample_amount(rng, by_id[mid]["category"], cust["_level"])
                roll = rng.random()
                status = "FAILED" if roll < 0.030 else (
                    "REVERSED" if roll < 0.045 else "COMPLETED")
                rows.append({
                    "customer_id": cust["customer_id"],
                    "merchant_id": mid,
                    "amount": money(amount),
                    "transaction_time": ts,
                    "status": status,
                    "payment_method": weighted_choice(
                        rng, PAYMENT_WEIGHTS.get(by_id[mid]["category"],
                                                 PAYMENT_WEIGHTS["default"])),
                    "channel": weighted_choice(rng, CHANNEL_WEIGHTS),
                    "transaction_reference": "GAX-%s-%s" % (
                        ts.strftime("%Y%m%d"), ref_code(rng)),
                })
    return rows


def finalise_customer_status(customers: list, txns: list, rng: random.Random) -> None:
    last = {}
    for t in txns:
        if t["status"] != "COMPLETED":
            continue
        key = t["customer_id"]
        if key not in last or t["transaction_time"] > last[key]:
            last[key] = t["transaction_time"]
    for cust in customers:
        if cust["status"] != "INACTIVE":
            continue
        base = last.get(cust["customer_id"], parse_iso(cust["created_at"]))
        changed = base + timedelta(days=rng.randint(3, 25), hours=rng.randint(0, 12))
        cust["status_changed_at"] = iso(min(changed, datetime(2026, 9, 30, 12, 0, 0)))


# --------------------------------------------------------------------------
# 6. (customer, merchant) behaviour index
# --------------------------------------------------------------------------


class PairIndex:
    def __init__(self, txns: list):
        self.times = defaultdict(list)
        self.amounts = defaultdict(list)
        for t in txns:
            if t["status"] != "COMPLETED":
                continue
            key = (t["customer_id"], t["merchant_id"])
            self.times[key].append((t["transaction_time"], t["amount"]))
        for key in self.times:
            self.times[key].sort()
        self._cache = {}

    def refresh(self, extra: list) -> None:
        for t in extra:
            if t["status"] != "COMPLETED":
                continue
            key = (t["customer_id"], t["merchant_id"])
            self.times[key].append((t["transaction_time"], t["amount"]))
            self.times[key].sort()
        self._cache.clear()

    def stats(self, cid: str, mid: str, as_of: datetime) -> dict:
        key = (cid, mid)
        rows = self.times.get(key, [])
        idx = bisect_right([r[0] for r in rows], as_of)
        rows = rows[:idx]
        amounts = [r[1] for r in rows]
        total = money(sum(amounts))
        freq = len(rows)
        return {
            "frequency": freq,
            "total_spend": total,
            "avg_spend": money(total / freq) if freq else 0.0,
            "first_purchase": rows[0][0] if rows else None,
            "last_purchase": rows[-1][0] if rows else None,
            "freq_90d": sum(1 for r in rows if (as_of - r[0]).days <= 90),
        }

    def pairs(self, mid: str = None):
        for (cid, m) in self.times:
            if mid is None or m == mid:
                yield cid, m


# --------------------------------------------------------------------------
# 7. Offers
# --------------------------------------------------------------------------


class OfferFactory:
    def __init__(self, rng: random.Random):
        self.rng = rng
        self.counter = 0
        self.rows = []

    def next_id(self) -> str:
        self.counter += 1
        return "OFR-%04d" % self.counter

    def _terms(self, cat: str, counter: int, percentage_bias: bool):
        rng = self.rng
        lo, hi = CAT_AMOUNT[cat]
        d_type = "PERCENTAGE" if percentage_bias else "FIXED"
        if d_type == "FIXED":
            step = 50 if hi <= 1500 else 100
            value = float(step * rng.randint(max(1, int(lo * 1.2 / step)),
                                             max(2, int(hi * 0.40 / step))))
            max_disc = ""
        else:
            value = float(rng.choice([5, 10, 10, 15, 20, 25]))
            max_disc = money(min(hi * 0.40, value / 100.0 * hi * 0.8))
        min_spend = float(rng.choice([299, 399, 499, 599, 699, 799, 899, 999,
                                      1199, 1499, 1999, 2499, 4999, 9999]))
        if d_type == "FIXED" and min_spend < value:
            min_spend = float(rng.choice([499, 799, 999, 1499]))
        return d_type, money(value), money(min_spend), max_disc

    def standalone(self, merchant: dict, monthly_revenue: float) -> dict:
        rng = self.rng
        cat = merchant["category"]
        counter = self.counter + 1
        percentage_bias = cat in ("CLOTHING", "BEAUTY", "SALON", "FITNESS",
                                  "ELECTRONICS", "RESTAURANT") and counter % 3 == 0
        d_type, value, min_spend, max_disc = self._terms(cat, counter,
                                                         percentage_bias)
        tpl = rng.choice(OFFER_NAME_TEMPLATES[d_type])
        name = tpl.format(v=int(value), p=int(value), m=int(min_spend))
        created_date = date(2026, rng.randint(1, 9), rng.randint(1, 27))
        created = datetime.combine(created_date, time(rng.randint(9, 19),
                                                      rng.randint(0, 59)))
        validity = rng.choice([7, 10, 12, 14, 18, 21, 28])
        start = created + timedelta(days=rng.randint(0, 2), hours=rng.randint(0, 6))
        expiry = min(start + timedelta(days=validity),
                     datetime(2026, 10, 20, 23, 59, 59))
        if expiry < END_OF_DAY:
            status = "EXPIRED"
        else:
            roll = rng.random()
            status = ("DRAFT" if roll < 0.10 else
                      "CANCELLED" if roll < 0.16 else "ACTIVE")
        updated = min(created + timedelta(days=rng.randint(0, 3),
                                          minutes=rng.randint(0, 240)), DAEMON_NOW)
        row = {
            "offer_id": self.next_id(),
            "merchant_id": merchant["merchant_id"],
            "name": name,
            "discount_type": d_type,
            "discount_value": value,
            "minimum_spend": min_spend,
            "max_discount": max_disc,
            "start_time": iso(start),
            "expiry_time": iso(expiry),
            "budget": money(max(1500.0, monthly_revenue * rng.uniform(0.05, 0.16))),
            "status": status,
            "created_at": iso(created),
            "updated_at": iso(updated),
            "_consumed": 0.0,
        }
        self.rows.append(row)
        return row

    def for_campaign(self, merchant: dict, campaign_id: str, created: datetime,
                      launch: datetime, validity: int, force_expired: bool,
                      tight_budget: bool) -> dict:
        rng = self.rng
        cat = merchant["category"]
        counter = self.counter + 1
        percentage_bias = counter % 4 == 0 or (
            cat == "ELECTRONICS" and counter % 2 == 0)
        d_type, value, min_spend, max_disc = self._terms(cat, counter,
                                                         percentage_bias)
        tpl = rng.choice(OFFER_NAME_TEMPLATES[d_type])
        name = tpl.format(v=int(value), p=int(value), m=int(min_spend))
        if force_expired:
            start = launch - timedelta(days=6)
            expiry = launch - timedelta(days=2)
        else:
            start = launch - timedelta(days=rng.randint(1, 3))
            expiry = min(launch + timedelta(days=validity),
                         datetime(2026, 10, 18, 23, 59, 59))
        lo, hi = CAT_AMOUNT[cat]
        budget = (money(rng.choice([400, 600, 900])) if tight_budget
                  else money(max(3000.0, hi * rng.uniform(3.0, 8.0))))
        row = {
            "offer_id": self.next_id(),
            "merchant_id": merchant["merchant_id"],
            "name": name,
            "discount_type": d_type,
            "discount_value": value,
            "minimum_spend": min_spend,
            "max_discount": max_disc,
            "start_time": iso(start),
            "expiry_time": iso(expiry),
            "budget": budget,
            "status": "EXPIRED" if expiry < END_OF_DAY else "ACTIVE",
            "created_at": iso(created - timedelta(hours=rng.randint(2, 40))),
            "updated_at": iso(min(created + timedelta(days=rng.randint(0, 2)),
                                  DAEMON_NOW)),
            "_consumed": 0.0,
        }
        self.rows.append(row)
        return row


def build_offers(merchants: list, txns: list, factory: OfferFactory) -> None:
    monthly_revenue = defaultdict(float)
    for t in txns:
        if t["status"] == "COMPLETED":
            monthly_revenue[t["merchant_id"]] += t["amount"]
    for mid in monthly_revenue:
        monthly_revenue[mid] /= 9.0
    per_category = 1
    for merchant in merchants:
        for _ in range(per_category):
            factory.standalone(merchant, monthly_revenue.get(merchant["merchant_id"],
                                                            20000.0))


# --------------------------------------------------------------------------
# 8. Campaigns
# --------------------------------------------------------------------------


def qualifies(index: PairIndex, cid: str, mid: str, as_of: datetime,
              rule: dict) -> bool:
    st = index.stats(cid, mid, as_of)
    if st["frequency"] == 0:
        return False
    name = rule["rule"]
    if name == "RECENTLY_ACQUIRED":
        return ((as_of - st["first_purchase"]).days
                <= rule["first_purchase_within_days"]
                and st["frequency"] <= rule["max_frequency"])
    if name == "TOP_FREQUENCY":
        # frequency floor; the percentile cut is applied by percentile_allow()
        return (st["frequency"] >= rule["min_frequency"]
                and (as_of - st["last_purchase"]).days <= rule["recency_days_max"])
    if name in ("DORMANT_REENGAGE", "RECENT_ACTIVE", "BROAD_REACH"):
        if st["frequency"] < rule["min_frequency"]:
            return False
        recency = (as_of - st["last_purchase"]).days
        if name == "RECENT_ACTIVE":
            return recency <= rule["recency_days_max"]
        return (rule.get("recency_days_min", 0) <= recency
                <= rule["recency_days_max"])
    if name == "TOP_SPENDERS":
        return True
    raise ValueError(name)


def percentile_allow(index: PairIndex, mid: str, as_of: datetime,
                     rule: dict) -> set:
    """Merchant-relative cut: only the top slice of the merchant's own base.

    Mirrors PRD section 8 ("High Value = total_spend >= 75th percentile of
    merchant customers") so the application can reproduce it exactly.
    """
    spend_rows, freq_rows = [], []
    for cid, _ in index.pairs(mid):
        st = index.stats(cid, mid, as_of)
        if st["frequency"] == 0:
            continue
        if (rule["rule"] == "TOP_FREQUENCY"
                and (as_of - st["last_purchase"]).days > rule["recency_days_max"]):
            continue
        spend_rows.append((cid, st["total_spend"]))
        freq_rows.append((cid, st["frequency"]))
    if not spend_rows:
        return set()

    def cut_off(rows, pct):
        values = sorted(v for _, v in rows)
        return values[min(len(values) - 1, int(len(values) * pct / 100.0))]

    if rule["rule"] == "TOP_FREQUENCY":
        limit = cut_off(freq_rows, rule["frequency_percentile_min"])
        return {cid for cid, freq in freq_rows if freq >= limit}
    limit = cut_off(spend_rows, rule["spend_percentile_min"])
    return {cid for cid, spend in spend_rows if spend >= limit}


def campaign_specs(rng: random.Random, merchants: list) -> list:
    """(merchant_id, objective, launch_month, launch_day, outcome, experiment)"""
    primary = [
        ("REACTIVATE_INACTIVE", (3, 14), "COMPLETED", True),
        ("REWARD_FREQUENT_CUSTOMERS", (4, 9), "COMPLETED", False),
        ("INCREASE_REPEAT_PURCHASE", (6, 20), "FAILED", False),
        ("WELCOME_NEW_CUSTOMERS", (2, 11), "COMPLETED", False),
        ("SEASONAL_PROMOTION", (7, 6), "COMPLETED", False),
        ("PROMOTE_HIGH_VALUE_OFFER", (8, 19), "COMPLETED", True),
        ("REACTIVATE_INACTIVE", (5, 22), "CANCELLED", False),
        ("REWARD_FREQUENT_CUSTOMERS", (6, 2), "REJECTED", False),
        ("WELCOME_NEW_CUSTOMERS", (9, 24), "SENDING", False),
        ("SEASONAL_PROMOTION", (10, 8), "SCHEDULED", False),
        ("PROMOTE_HIGH_VALUE_OFFER", (9, 6), "COMPLETED", False),
        ("INCREASE_REPEAT_PURCHASE", (10, 1), "PENDING_APPROVAL", False),
        ("SEASONAL_PROMOTION", (10, 2), "APPROVED_PENDING_SCHEDULE", False),
        ("REACTIVATE_INACTIVE", (10, 3), "DRAFT", False),
    ]
    specs = [("MER-0001", obj, md[0], md[1], outcome, exp)
             for obj, md, outcome, exp in primary]
    others = [m["merchant_id"] for m in merchants
              if m["merchant_id"] != "MER-0001" and m["is_active"] == "true"]
    for i, mid in enumerate(others):
        count = 4 if i < 12 else 3
        for k in range(count):
            objective = OBJECTIVES[(i + k * 2) % 6]
            month = ((i + k * 3) % 9) + 1
            day = ((i * 7 + k * 11) % 26) + 1
            specs.append((mid, objective, month, day, None, (i + k) % 4 == 0))
    return specs


def build_campaigns(merchants: list, factory: OfferFactory, index: PairIndex,
                    rng: random.Random) -> list:
    by_id = {m["merchant_id"]: m for m in merchants}
    campaigns = []
    for counter, (mid, objective, month, day, outcome, experiment) in enumerate(
            campaign_specs(rng, merchants), start=1):
        cid = "CPN-%04d" % counter
        merchant = by_id[mid]
        cat = merchant["category"]

        launch_day = date(2026, month, min(day, month_day_count(month)))
        launch = datetime.combine(launch_day, time(rng.randint(9, 18),
                                                   rng.randint(0, 59)))
        created = launch - timedelta(days=rng.randint(4, 12), hours=rng.randint(1, 9))
        if created < datetime(2026, 1, 2, 8, 0, 0):
            created = datetime(2026, 1, 2, 8, 0, 0)

        validity = rng.choice([7, 10, 12, 14, 18, 21])
        force_expired = (counter > 10 and counter % 11 == 4)
        tight_budget = (counter % 19 == 5)
        offer = factory.for_campaign(merchant, cid, created, launch, validity,
                                     force_expired, tight_budget)

        if outcome is None:
            outcome = decide_outcome(rng, offer, launch)
        status = STATUS_BY_OUTCOME[outcome]

        rule = dict(AUDIENCE_RULES[objective])
        rule["evaluation_date"] = (created + timedelta(days=1)).strftime("%Y-%m-%d")
        rule["merchant_scoped"] = True
        rule["grain"] = "customer_x_merchant"

        campaigns.append({
            "campaign_id": cid,
            "merchant_id": mid,
            "offer_id": offer["offer_id"],
            "objective": objective,
            "audience_definition": json.dumps(rule, sort_keys=True),
            "status": status,
            "approved_version": "",
            "experiment_enabled": "true" if experiment else "false",
            "created_at": iso(created),
            "updated_at": iso(created),
            "_launch": launch,
            "_outcome": outcome,
            "_rule": rule,
            "_eval_as_of": datetime.combine(created + timedelta(days=1),
                                           time(23, 59, 59)),
            "_offer": offer,
            "_cat": cat,
            "_cancel_at": None,
        })
    return campaigns


STATUS_BY_OUTCOME = {
    "COMPLETED": "COMPLETED", "SENDING": "SENDING", "SCHEDULED": "SCHEDULED",
    "REJECTED": "REJECTED", "CANCELLED": "CANCELLED", "FAILED": "FAILED",
    "EXPIRED": "EXPIRED", "DRAFT": "DRAFT",
    "PENDING_APPROVAL": "PENDING_APPROVAL",
    "APPROVED_PENDING_SCHEDULE": "APPROVED",
}

# campaigns in these states have been (or are being) sent to customers
LIVE_OUTCOMES = ("COMPLETED", "SENDING", "CANCELLED", "FAILED")


def decide_outcome(rng: random.Random, offer: dict, launch: datetime) -> str:
    if parse_iso(offer["expiry_time"]) < launch:
        return "EXPIRED"
    if launch > END_OF_DAY:
        return "SCHEDULED"
    if parse_iso(offer["expiry_time"]) > DAEMON_NOW:
        return "SENDING"
    roll = rng.random()
    if roll < 0.06:
        return "REJECTED"
    if roll < 0.10:
        return "CANCELLED"
    if roll < 0.14:
        return "FAILED"
    return "COMPLETED"


# --------------------------------------------------------------------------
# 9. Campaign versions (AI copy) and lifecycle events
# --------------------------------------------------------------------------


def format_discount(offer: dict) -> str:
    if offer["discount_type"] == "FIXED":
        return "Rs %d OFF" % int(round(float(offer["discount_value"])))
    text = "%d%% OFF" % int(round(float(offer["discount_value"])))
    if offer["max_discount"]:
        text += " (up to Rs %d)" % int(round(float(offer["max_discount"])))
    return text


def expected_discount(offer: dict, amount: float) -> float:
    if offer["discount_type"] == "FIXED":
        return money(min(float(offer["discount_value"]), amount))
    raw = round(amount * float(offer["discount_value"]) / 100.0, 2)
    cap = float(offer["max_discount"]) if offer["max_discount"] else None
    return money(min(raw, cap) if cap is not None else raw)


def valid_copy(offer: dict, store: str, rng: random.Random) -> dict:
    disc = format_discount(offer)
    min_spend = int(round(float(offer["minimum_spend"])))
    expiry = parse_iso(offer["expiry_time"])
    return {
        "headline": "Get %s above Rs %d" % (disc, min_spend),
        "message": ("Pay with Paytm at %s and get %s on bills above Rs %d. "
                    "Valid till %d %s." % (store, disc, min_spend, expiry.day,
                                           expiry.strftime("%b"))),
        "call_to_action": rng.choice(CTA_VALID),
    }


def broken_copy(offer: dict, store: str, reason: str, rng: random.Random) -> dict:
    disc = format_discount(offer)
    min_spend = int(round(float(offer["minimum_spend"])))
    expiry = parse_iso(offer["expiry_time"])
    doubled = int(round(float(offer["discount_value"]) * 2)) + 50
    wrong_min = int(round(float(offer["minimum_spend"]) *
                          rng.choice([0.4, 0.5, 1.8, 2.5])))
    if reason == "DISCOUNT_VALUE_MISMATCH":
        return {
            "headline": "Get Rs %d OFF above Rs %d" % (doubled, min_spend),
            "message": ("Pay with Paytm at %s and get Rs %d OFF on all purchases. "
                        "No minimum order value required." % (store, doubled)),
            "call_to_action": rng.choice(CTA_VALID),
        }
    if reason == "MINIMUM_SPEND_MISMATCH":
        return {
            "headline": "Get %s above Rs %d" % (disc, wrong_min),
            "message": ("Pay with Paytm at %s and get %s on bills above Rs %d. "
                        "Valid till %d %s." % (store, disc, wrong_min,
                                               expiry.day, expiry.strftime("%b"))),
            "call_to_action": rng.choice(CTA_VALID),
        }
    if reason == "UNSUPPORTED_CLAIM":
        return {
            "headline": "Unlimited cashback, every single visit",
            "message": ("Pay with Paytm at %s and receive guaranteed cashback of "
                        "Rs %d on every single payment, forever."
                        % (store, doubled)),
            "call_to_action": rng.choice(CTA_VALID),
        }
    if reason == "EXCESSIVE_DISCOUNT_LANGUAGE":
        return {
            "headline": "%s - the biggest discount ever" % disc,
            "message": ("Pay with Paytm at %s. Enjoy our record-breaking %s on a "
                        "minimum bill of just Rs 1." % (store, disc)),
            "call_to_action": rng.choice(CTA_VALID),
        }
    if reason == "INVALID_CALL_TO_ACTION":
        copy = valid_copy(offer, store, rng)
        copy["call_to_action"] = rng.choice(CTA_INVALID)
        return copy
    return {
        "headline": "Exclusive Paytm offer at %s" % store,
        "message": ("Use Paytm at %s and get an exclusive discount. Terms and "
                    "conditions apply." % store),
        "call_to_action": rng.choice(CTA_VALID),
    }


def build_versions(campaigns: list, merchants_by_id: dict,
                   rng: random.Random) -> list:
    versions = []
    counter = 0
    for camp in campaigns:
        offer = camp["_offer"]
        store = merchants_by_id[camp["merchant_id"]]["store_name"]
        created = parse_iso(camp["created_at"])
        rejected_campaign = camp["_outcome"] == "REJECTED"
        total = rng.choice([1, 1, 2, 2, 2, 3])

        times = []
        t = created + timedelta(minutes=rng.randint(10, 90))
        for _ in range(total):
            times.append(t)
            t = t + timedelta(hours=rng.randint(2, 26))

        rejected = set()
        if rejected_campaign:
            rejected = set(range(total))
        elif camp["_outcome"] in ("DRAFT", "PENDING_APPROVAL",
                                  "APPROVED_PENDING_SCHEDULE"):
            rejected = set()          # nothing approved yet
        elif total >= 2 and rng.random() < 0.35:
            rejected = {rng.randrange(total - 1)}

        awaiting_review = camp["_outcome"] in ("DRAFT", "PENDING_APPROVAL",
                                               "APPROVED_PENDING_SCHEDULE")
        for i, vt in enumerate(times, start=1):
            counter += 1
            vid = "CPV-%05d" % counter
            if i in rejected:
                reason = rng.choice(REJECTION_REASONS_COPY)
                copy = broken_copy(offer, store, reason, rng)
                status, approved_at = "REJECTED", ""
            else:
                reason = ""
                copy = valid_copy(offer, store, rng)
                if awaiting_review:
                    status = "VALIDATED"
                    approved_at = ""
                elif i < total:
                    status = "VALIDATED" if rng.random() < 0.5 else "GENERATED"
                    approved_at = ""
                else:
                    status = "APPROVED"
                    approved_at = iso(vt + timedelta(hours=rng.randint(2, 30),
                                                     minutes=rng.randint(0, 59)))
            versions.append({
                "campaign_version_id": vid,
                "campaign_id": camp["campaign_id"],
                "version_number": i,
                "headline": copy["headline"],
                "message": copy["message"],
                "call_to_action": copy["call_to_action"],
                "ai_model": rng.choice(AI_MODELS),
                "prompt_version": rng.choice(PROMPT_VERSIONS),
                "status": status,
                "rejection_reason": reason,
                "created_at": iso(vt),
                "approved_at": approved_at,
            })
            if status == "APPROVED":
                camp["approved_version"] = vid
                camp["updated_at"] = iso(max(parse_iso(camp["updated_at"]),
                                            parse_iso(approved_at)))
    return versions


def ev(campaign_id: str, event_type: str, when: datetime, meta: dict) -> dict:
    return {"campaign_id": campaign_id, "event_type": event_type,
            "event_time": when, "metadata": json.dumps(meta, sort_keys=True)}


def build_lifecycle_events(campaigns: list, versions: list,
                           rng: random.Random) -> list:
    by_campaign = defaultdict(list)
    for v in versions:
        by_campaign[v["campaign_id"]].append(v)

    events = []
    for camp in campaigns:
        cid = camp["campaign_id"]
        created = parse_iso(camp["created_at"])
        launch = camp["_launch"]
        outcome = camp["_outcome"]
        offer = camp["_offer"]
        actor = "USR-%s-01" % camp["merchant_id"]

        events.append(ev(cid, "CAMPAIGN_CREATED", created, {
            "merchant_id": camp["merchant_id"], "offer_id": camp["offer_id"],
            "objective": camp["objective"], "actor": "merchant_portal"}))
        events.append(ev(cid, "VALIDATED", created + timedelta(
            minutes=rng.randint(2, 40)), {
            "checks_passed": ["offer_exists", "budget_present", "validity_window",
                              "audience_rule_parsed"],
            "audience_rule": camp["audience_definition"]}))

        vs = sorted(by_campaign[cid], key=lambda v: v["version_number"])
        for v in vs:
            vt = parse_iso(v["created_at"])
            events.append(ev(cid, "AI_GENERATION_STARTED",
                             vt - timedelta(minutes=rng.randint(1, 4)), {
                                 "campaign_version_id": v["campaign_version_id"],
                                 "model": v["ai_model"],
                                 "prompt_version": v["prompt_version"]}))
            if v["status"] == "REJECTED" and v["rejection_reason"] in (
                    "UNSUPPORTED_CLAIM", "EXCESSIVE_DISCOUNT_LANGUAGE"):
                events.append(ev(cid, "AI_GENERATION_FAILED", vt, {
                    "campaign_version_id": v["campaign_version_id"],
                    "error": "model_output_policy_violation",
                    "detail": "generated copy asserted an unsupported guarantee"}))
            else:
                events.append(ev(cid, "AI_GENERATION_COMPLETED", vt, {
                    "campaign_version_id": v["campaign_version_id"],
                    "characters": len(v["headline"]) + len(v["message"])}))

        last_gen = max([parse_iso(v["created_at"]) for v in vs], default=created)
        submitted = max(created + timedelta(hours=rng.randint(3, 20)),
                        last_gen + timedelta(hours=1))
        events.append(ev(cid, "SUBMITTED", submitted, {
            "submitted_by": "merchant_portal",
            "campaign_version_id": camp["approved_version"] or vs[-1]["campaign_version_id"]}))

        if outcome == "REJECTED":
            events.append(ev(cid, "REJECTED", submitted + timedelta(
                hours=rng.randint(2, 26)), {
                "actor_id": actor,
                "reason": rng.choice(["copy_review_failed", "audience_too_narrow",
                                      "discount_margins_not_approved"])}))
            continue

        if outcome == "DRAFT":                       # copy still being generated
            continue

        if outcome == "PENDING_APPROVAL":           # waiting for a reviewer
            continue

        events.append(ev(cid, "APPROVED", submitted + timedelta(
            hours=rng.randint(2, 30)), {
            "actor_id": actor, "campaign_version_id": camp["approved_version"],
            "approved_by_role": "MERCHANT_ADMIN"}))

        if outcome == "APPROVED_PENDING_SCHEDULE":  # approved, not scheduled yet
            continue

        scheduled = launch - timedelta(hours=rng.randint(2, 40))
        if scheduled <= parse_iso(camp["updated_at"]):
            scheduled = parse_iso(camp["updated_at"]) + timedelta(minutes=30)
        events.append(ev(cid, "SCHEDULED", scheduled,
                         {"scheduled_for": iso(launch)}))

        if outcome in ("SCHEDULED", "EXPIRED"):
            if outcome == "EXPIRED":
                events.append(ev(cid, "CAMPAIGN_CANCELLED",
                                 scheduled + timedelta(hours=6), {
                                     "actor_id": actor,
                                     "reason": "offer_expired_before_launch",
                                     "offer_id": camp["offer_id"]}))
            continue

        events.append(ev(cid, "LAUNCHED", launch, {
            "channel": "PAYTM_CHAT_OFFER_CARD", "offer_id": camp["offer_id"],
            "discount_type": offer["discount_type"],
            "discount_value": float(offer["discount_value"]),
            "minimum_spend": float(offer["minimum_spend"]),
            "budget": float(offer["budget"])}))

        if outcome == "CANCELLED":
            cancel_at = launch + timedelta(hours=rng.randint(6, 40))
            camp["_cancel_at"] = cancel_at
            events.append(ev(cid, "CAMPAIGN_CANCELLED", cancel_at, {
                "actor_id": actor,
                "reason": rng.choice(["merchant_paused_campaign",
                                      "offer_withdrawn_by_merchant",
                                      "budget_review"])}))
        elif outcome == "FAILED":
            end = parse_iso(offer["expiry_time"])
            events.append(ev(cid, "CAMPAIGN_FAILED", end + timedelta(hours=5), {
                "reason": "all_message_deliveries_failed",
                "deliveries_succeeded": 0}))
    return events


def add_message_events(recipients: list, campaigns_by_id: dict, events: list,
                       rng: random.Random) -> None:
    for r in recipients:
        camp = campaigns_by_id[r["campaign_id"]]
        launch = camp["_launch"]
        base = {"recipient_id": r["recipient_id"], "customer_id": r["customer_id"],
                "coupon_code": r["coupon_code"]}
        if r["delivery_status"] == "DELIVERED":
            events.append(ev(r["campaign_id"], "MESSAGE_QUEUED",
                             launch + timedelta(minutes=rng.randint(0, 30)), base))
            events.append(ev(r["campaign_id"], "MESSAGE_SENT",
                             parse_iso(r["sent_at"]), base))
            events.append(ev(r["campaign_id"], "MESSAGE_DELIVERED",
                             parse_iso(r["delivered_at"]),
                             dict(base, message_type="OFFER_CARD")))
        elif r["delivery_status"] == "FAILED" and r["sent_at"]:
            sent = parse_iso(r["sent_at"])
            events.append(ev(r["campaign_id"], "MESSAGE_QUEUED",
                             launch + timedelta(minutes=rng.randint(0, 30)), base))
            events.append(ev(r["campaign_id"], "MESSAGE_SENT", sent, base))
            events.append(ev(r["campaign_id"], "MESSAGE_FAILED",
                             sent + timedelta(minutes=rng.randint(1, 25)),
                             dict(base, error_code=rng.choice(
                                 ["CARRIER_UNREACHABLE", "TEMPLATE_REJECTED",
                                  "RATE_LIMITED", "INVALID_DESTINATION"]))))


# --------------------------------------------------------------------------
# 10. Recipients
# --------------------------------------------------------------------------


def idx_pairs(index: PairIndex, mid: str):
    return list(index.pairs(mid))


def build_recipients(campaigns: list, index: PairIndex, rng: random.Random) -> list:
    campaign_dates = defaultdict(list)     # customer -> launch datetimes sent
    rows = []
    for camp in sorted(campaigns, key=lambda c: c["_launch"]):
        if camp["_outcome"] not in LIVE_OUTCOMES:
            continue
        cid, mid = camp["campaign_id"], camp["merchant_id"]
        as_of = camp["_eval_as_of"]
        launch = camp["_launch"]
        rule = camp["_rule"]

        pool, near_miss = [], []
        for customer_id, m in index.pairs(mid):
            (pool if qualifies(index, customer_id, mid, as_of, rule)
             else near_miss).append(customer_id)
        if rule["rule"] in ("TOP_SPENDERS", "TOP_FREQUENCY"):
            pool = [c for c in pool
                    if c in percentile_allow(index, mid, as_of, rule)]
        if not pool:
            # "audience too small, broaden it": the merchant falls back to its
            # whole known customer base.  The applied rule is written back into
            # audience_definition so the definition always matches reality.
            rule = {"rule": "BROAD_REACH", "min_frequency": 1,
                    "recency_days_max": 400, "evaluation_date":
                    camp["_eval_as_of"].strftime("%Y-%m-%d"),
                    "merchant_scoped": True, "grain": "customer_x_merchant",
                    "broadened_from": camp["audience_definition"]}
            camp["_rule"] = rule
            camp["audience_definition"] = json.dumps(rule, sort_keys=True)
            pool = [cid for cid, _ in idx_pairs(index, mid)
                    if index.stats(cid, mid, as_of)["frequency"] > 0
                    and (as_of - index.stats(cid, mid, as_of)["last_purchase"]).days
                    <= 400]
            near_miss = []
        rng.shuffle(pool)
        cap = rng.randint(120, 420)
        selected, suppressed = [], []
        for customer_id in pool[:cap]:
            recent = sum(1 for t in campaign_dates[customer_id]
                         if 0 <= (launch - t).days <= 30)
            if recent >= 5:
                suppressed.append((customer_id, "FREQUENCY_CAP_REACHED"))
            else:
                selected.append(customer_id)
                campaign_dates[customer_id].append(launch)
        for customer_id in pool[cap:cap + rng.randint(5, 20)]:
            suppressed.append((customer_id, rng.choice(
                ["ALREADY_REDEEMED_THIS_CAMPAIGN", "MERCHANT_FREQUENCY_CAP",
                 "OPT_OUT_PREVIOUS_CAMPAIGN"])))
        ineligible = (rng.sample(near_miss, min(len(near_miss), rng.randint(10, 40)))
                      if near_miss else [])

        if camp["experiment_enabled"] == "true":
            cut = int(len(selected) * 0.80)
            treatment, control = selected[:cut], selected[cut:]
        else:
            treatment, control = selected, []

        cancel_at = camp["_cancel_at"]
        failed_campaign = camp["_outcome"] == "FAILED"

        pending = []
        for customer_id in treatment:
            sent_at = launch + timedelta(minutes=rng.randint(1, 240))
            if failed_campaign or (cancel_at and sent_at > cancel_at):
                pending.append((customer_id, "ELIGIBLE", "FAILED", None, None,
                                "TREATMENT", ""))
            elif rng.random() < 0.055:
                pending.append((customer_id, "ELIGIBLE", "FAILED", sent_at, None,
                                "TREATMENT", ""))
            else:
                delivered = sent_at + timedelta(minutes=rng.randint(1, 180),
                                                 seconds=rng.randint(0, 59))
                pending.append((customer_id, "ELIGIBLE", "DELIVERED", sent_at,
                                delivered, "TREATMENT", ""))
        for customer_id in control:
            pending.append((customer_id, "ELIGIBLE", "PENDING", None, None,
                            "CONTROL", ""))
        for customer_id, reason in suppressed:
            pending.append((customer_id, "SUPPRESSED", "PENDING", None, None, "",
                            reason))
        for customer_id in ineligible:
            pending.append((customer_id, "INELIGIBLE", "PENDING", None, None, "",
                            ""))

        mnum = mid.replace("MER-", "")
        cnum = cid.split("-")[1]
        seen = set()
        for customer_id, elig, delivery, sent_at, delivered, group, reason in pending:
            if customer_id in seen:
                continue
            seen.add(customer_id)
            rows.append({
                "campaign_id": cid,
                "customer_id": customer_id,
                "eligibility_status": elig,
                "delivery_status": delivery,
                "sent_at": iso(sent_at) if sent_at else "",
                "delivered_at": iso(delivered) if delivered else "",
                "experiment_group": group,
                "coupon_code": "PTM-%s-%s-%s" % (mnum, cnum,
                                                  customer_id.replace("CUS-", "")),
                "conversation_id": "%s_%s" % (mid, customer_id),
                "suppression_reason": reason,
            })

    recipients = rows
    recipients.sort(key=lambda r: (r["campaign_id"], r["customer_id"]))
    for i, row in enumerate(recipients, start=1):
        row["recipient_id"] = "RCP-%07d" % i
    return recipients


# --------------------------------------------------------------------------
# 11. Redemptions (+ the transactions they reference)
# --------------------------------------------------------------------------


class TxnFactory:
    """Allocates provisional transaction ids; final ids are assigned at the end."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.rows = []
        self.counter = 500000

    def add(self, cid: str, mid: str, when: datetime, amount: float,
            status: str = "COMPLETED") -> dict:
        self.counter += 1
        cat = CAT_AMOUNT and mid
        row = {
            "_tmp_id": "TXN-T%07d" % self.counter,
            "customer_id": cid,
            "merchant_id": mid,
            "transaction_reference": "GAX-%s-%s" % (when.strftime("%Y%m%d"),
                                                    ref_code(self.rng)),
            "amount": money(amount),
            "transaction_time": when,
            "status": status,
            "payment_method": weighted_choice(
                self.rng, PAYMENT_WEIGHTS["default"]),
            "channel": "APP",
        }
        self.rows.append(row)
        return row


def build_redemptions(campaigns: list, recipients: list, merchants_by_id: dict,
                      customers_by_id: dict, factory: TxnFactory,
                      rng: random.Random) -> list:
    redemptions = []
    by_campaign = defaultdict(list)
    for r in recipients:
        by_campaign[r["campaign_id"]].append(r)

    for camp in sorted(campaigns, key=lambda c: c["_launch"]):
        if camp["_outcome"] not in ("COMPLETED", "SENDING", "CANCELLED"):
            continue
        offer = camp["_offer"]
        mid = camp["merchant_id"]
        cat = camp["_cat"]
        min_spend = float(offer["minimum_spend"])
        start = parse_iso(offer["start_time"])
        expiry = parse_iso(offer["expiry_time"])
        budget = float(offer["budget"])
        cancel_at = camp["_cancel_at"]
        delivered = {r["customer_id"]: r
                     for r in by_campaign[camp["campaign_id"]]
                     if r["eligibility_status"] == "ELIGIBLE"
                     and r["delivery_status"] == "DELIVERED"}

        for customer_id, rec in delivered.items():
            # most recipients ignore the offer; campaign-responsive customers
            # are far more likely to buy, but the relationship is not causal
            cust = customers_by_id[customer_id]
            p = 0.42 if cust["_responsive"] else 0.07
            if rng.random() > p:
                continue
            when = parse_iso(rec["delivered_at"]) + timedelta(
                days=min(rng.choice([0, 0, 1, 1, 2, 3, 4, 5, 6, 8]), 12),
                hours=rng.randint(0, 20), minutes=rng.randint(0, 59))
            if cancel_at and when > cancel_at:
                when = cancel_at - timedelta(hours=rng.randint(1, 6))
            if when > expiry:
                if rng.random() < 0.5:
                    continue
                when = expiry - timedelta(hours=rng.randint(1, 40))
            if when < start:
                when = start + timedelta(minutes=rng.randint(10, 300))
            if when > ACTIVITY_END:
                continue

            amount = max(min_spend + 1.0, sample_amount(rng, cat, "typical_high"))
            if rng.random() < 0.55:
                amount = round(min_spend * rng.uniform(1.02, 1.9), 2)
            amount = min(amount, 60000.0)

            txn = factory.add(customer_id, mid, when, amount)
            discount = expected_discount(offer, amount)
            base = {
                "campaign_id": camp["campaign_id"],
                "offer_id": offer["offer_id"],
                "customer_id": customer_id,
                "transaction_id": txn["_tmp_id"],
                "gross_bill_amount": money(amount),
                "discount_applied": discount,
            }
            if discount > 0 and offer["_consumed"] + discount <= budget:
                offer["_consumed"] += discount
                redemptions.append(dict(base, redeemed_at=iso(when),
                                        status="CONFIRMED", rejection_reason=""))
                if rng.random() < 0.11:
                    redemptions.append(dict(
                        base, redeemed_at=iso(when + timedelta(
                            minutes=rng.randint(1, 90))),
                        status="DUPLICATE_REJECTED",
                        rejection_reason="DUPLICATE_REDEMPTION"))
            else:
                redemptions.append(dict(
                    base, redeemed_at=iso(when), status="REJECTED",
                    rejection_reason="OFFER_BUDGET_EXHAUSTED"))
    return redemptions


def inject_invalid_redemptions(campaigns: list, recipients: list,
                               redemptions: list, merchants_by_id: dict,
                               merchant_cat: dict, factory: TxnFactory,
                               rng: random.Random) -> None:
    delivered = defaultdict(list)
    for r in recipients:
        if (r["eligibility_status"] == "ELIGIBLE"
                and r["delivery_status"] == "DELIVERED"):
            delivered[r["campaign_id"]].append(r)
    counts = defaultdict(int)
    for row in redemptions:
        counts[row["rejection_reason"]] += 1

    def need(reason, minimum):
        return counts[reason] < minimum

    def add_red(camp, cust, txn, when, status, reason):
        redemptions.append({
            "campaign_id": camp["campaign_id"],
            "offer_id": camp["_offer"]["offer_id"],
            "customer_id": cust,
            "transaction_id": txn["_tmp_id"],
            "gross_bill_amount": money(txn["amount"]),
            "discount_applied": expected_discount(camp["_offer"], txn["amount"]),
            "redeemed_at": iso(when),
            "status": status,
            "rejection_reason": reason,
        })
        counts[reason] += 1

    targets = [c for c in campaigns if delivered.get(c["campaign_id"])]
    rng.shuffle(targets)

    for camp in targets:
        rows = delivered[camp["campaign_id"]]
        offer = camp["_offer"]
        mid = camp["merchant_id"]
        cat = camp["_cat"]
        min_spend = float(offer["minimum_spend"])
        start = parse_iso(offer["start_time"])
        expiry = parse_iso(offer["expiry_time"])
        pick = rng.choice(rows)
        cust = pick["customer_id"]
        base_when = parse_iso(pick["delivered_at"]) + timedelta(
            days=rng.randint(1, 4), hours=rng.randint(1, 9))
        base_when = min(base_when, expiry - timedelta(days=1))

        if need("BELOW_MINIMUM_SPEND", 6):
            amount = max(20.0, round(min_spend * rng.uniform(0.25, 0.85), 2))
            txn = factory.add(cust, mid, base_when, amount)
            add_red(camp, cust, txn, base_when + timedelta(minutes=3),
                    "REJECTED", "BELOW_MINIMUM_SPEND")

        if need("WRONG_MERCHANT", 6):
            other = rng.choice([m for m in merchant_cat if m != mid])
            txn = factory.add(cust, other, base_when + timedelta(minutes=40),
                              max(min_spend + 100,
                                  sample_amount(rng, merchant_cat[other],
                                                "typical_high")))
            add_red(camp, cust, txn, base_when + timedelta(minutes=44),
                    "REJECTED", "WRONG_MERCHANT")

        if need("WRONG_CUSTOMER", 6) and len(rows) >= 2:
            other = rng.choice([r for r in rows if r["customer_id"] != cust])
            txn = factory.add(other["customer_id"], mid,
                              base_when + timedelta(minutes=50),
                              max(min_spend + 150,
                                  sample_amount(rng, cat, "typical_high")))
            add_red(camp, cust, txn, base_when + timedelta(minutes=52),
                    "REJECTED", "WRONG_CUSTOMER")

        if need("TRANSACTION_FAILED", 6):
            txn = factory.add(cust, mid, base_when + timedelta(minutes=60),
                              max(min_spend + 200,
                                  sample_amount(rng, cat, "typical_high")),
                              status="FAILED")
            add_red(camp, cust, txn, base_when + timedelta(minutes=61),
                    "REJECTED", "TRANSACTION_FAILED")

        if need("TRANSACTION_REVERSED", 4):
            txn = factory.add(cust, mid, base_when + timedelta(minutes=70),
                              max(min_spend + 200,
                                  sample_amount(rng, cat, "typical_high")),
                              status="REVERSED")
            add_red(camp, cust, txn, base_when + timedelta(minutes=72),
                    "REJECTED", "TRANSACTION_REVERSED")

        if need("OFFER_NOT_STARTED", 4) and start > datetime(2026, 1, 20):
            early = start - timedelta(hours=rng.randint(2, 20))
            if early > datetime(2026, 1, 5):
                txn = factory.add(cust, mid, early,
                                  max(min_spend + 100,
                                      sample_amount(rng, cat, "typical_high")))
                add_red(camp, cust, txn, early + timedelta(minutes=2),
                        "REJECTED", "OFFER_NOT_STARTED")

        if need("OFFER_EXPIRED", 6) and expiry < datetime(2026, 9, 25):
            late = expiry + timedelta(days=rng.randint(1, 6),
                                      hours=rng.randint(1, 12))
            if late <= ACTIVITY_END:
                txn = factory.add(cust, mid, late,
                                  max(min_spend + 120,
                                      sample_amount(rng, cat, "typical_high")))
                add_red(camp, cust, txn, late + timedelta(minutes=2),
                        "REJECTED", "OFFER_EXPIRED")

        if need("INELIGIBLE_CUSTOMER", 6):
            bad = [r for r in recipients
                   if r["campaign_id"] == camp["campaign_id"]
                   and r["eligibility_status"] in ("INELIGIBLE", "SUPPRESSED")]
            if bad:
                pick_bad = rng.choice(bad)
                when = (parse_iso(pick_bad["delivered_at"])
                        if pick_bad["delivered_at"]
                        else camp["_launch"] + timedelta(days=2))
                when = min(when, expiry - timedelta(hours=2))
                txn = factory.add(pick_bad["customer_id"], mid, when,
                                  max(min_spend + 130,
                                      sample_amount(rng, cat, "typical_high")))
                add_red(camp, pick_bad["customer_id"], txn,
                        when + timedelta(minutes=5), "REJECTED",
                        "INELIGIBLE_CUSTOMER")

    for camp in campaigns:
        if camp["_outcome"] != "CANCELLED" or not camp["_cancel_at"]:
            continue
        rows = delivered.get(camp["campaign_id"], [])
        if not rows:
            continue
        when = camp["_cancel_at"] + timedelta(hours=rng.randint(1, 20))
        if when > ACTIVITY_END:
            continue
        pick = rng.choice(rows)
        txn = factory.add(pick["customer_id"], camp["merchant_id"], when,
                          max(float(camp["_offer"]["minimum_spend"]) + 200,
                              sample_amount(rng, camp["_cat"], "typical_high")))
        add_red(camp, pick["customer_id"], txn, when + timedelta(minutes=1),
                "REJECTED", "CAMPAIGN_NOT_LIVE")
        break


def add_redemption_events(redemptions: list, campaigns_by_id: dict, events: list,
                          rng: random.Random) -> None:
    for row in redemptions:
        when = parse_iso(row["redeemed_at"])
        meta = {"redemption_id": row["redemption_id"],
                "transaction_id": row["transaction_id"],
                "customer_id": row["customer_id"],
                "offer_id": row["offer_id"]}
        events.append(ev(row["campaign_id"], "REDEMPTION_ATTEMPT", when, meta))
        if row["status"] == "CONFIRMED":
            events.append(ev(row["campaign_id"], "REDEMPTION_CONFIRMED",
                             when + timedelta(seconds=rng.randint(1, 40)),
                             dict(meta,
                                  discount_applied=float(row["discount_applied"]))))
        elif row["status"] == "DUPLICATE_REJECTED":
            events.append(ev(row["campaign_id"], "DUPLICATE_REJECTED",
                             when + timedelta(seconds=rng.randint(1, 40)),
                             dict(meta, reason=row["rejection_reason"])))
        else:
            events.append(ev(row["campaign_id"], "REDEMPTION_REJECTED",
                             when + timedelta(seconds=rng.randint(1, 40)),
                             dict(meta, reason=row["rejection_reason"])))


# --------------------------------------------------------------------------
# 12. Audit logs
# --------------------------------------------------------------------------


def build_audit_logs(campaigns: list, offers: list, redemptions: list,
                     events: list, rng: random.Random) -> list:
    logs = []

    def add(actor, action, entity_type, entity_id, when, **meta):
        logs.append({"actor_id": actor, "action": action,
                     "entity_type": entity_type, "entity_id": entity_id,
                     "timestamp": iso(when),
                     "metadata": json.dumps(meta, sort_keys=True) if meta else "{}"})

    for offer in offers[:45]:
        actor = "USR-%s-01" % offer["merchant_id"]
        when = parse_iso(offer["created_at"]) + timedelta(minutes=rng.randint(1, 300))
        add(actor, "CREATE_OFFER", "OFFER", offer["offer_id"], when,
            merchant_id=offer["merchant_id"],
            discount_type=offer["discount_type"],
            discount_value=float(offer["discount_value"]),
            minimum_spend=float(offer["minimum_spend"]),
            budget=float(offer["budget"]))
        if rng.random() < 0.45:
            add(actor, "UPDATE_OFFER", "OFFER", offer["offer_id"],
                when + timedelta(hours=rng.randint(1, 60)),
                merchant_id=offer["merchant_id"],
                changed_fields=rng.choice([["expiry_time"], ["budget"],
                                           ["minimum_spend"]]))

    first_event = {}
    for e in events:
        first_event.setdefault((e["campaign_id"], e["event_type"]), e["event_time"])
    for camp in campaigns:
        cid = camp["campaign_id"]
        actor = "USR-%s-01" % camp["merchant_id"]
        add(actor, "CREATE_CAMPAIGN", "CAMPAIGN", cid,
            parse_iso(camp["created_at"]) + timedelta(minutes=rng.randint(1, 90)),
            merchant_id=camp["merchant_id"], offer_id=camp["offer_id"],
            objective=camp["objective"])
        add("SYS-AI-COPY", "GENERATE_AI_CONTENT", "CAMPAIGN", cid,
            parse_iso(camp["created_at"]) + timedelta(minutes=rng.randint(2, 40)),
            model=rng.choice(AI_MODELS), prompt_version=rng.choice(PROMPT_VERSIONS),
            output_chars=rng.randint(120, 320))
        for etype, action in (("APPROVED", "APPROVE_CAMPAIGN"),
                              ("REJECTED", "REJECT_CAMPAIGN"),
                              ("LAUNCHED", "LAUNCH_CAMPAIGN"),
                              ("CANCELLED", "CANCEL_CAMPAIGN")):
            key = (cid, "CAMPAIGN_CANCELLED" if action == "CANCEL_CAMPAIGN" else etype)
            if key in first_event:
                add(actor, action, "CAMPAIGN", cid, first_event[key],
                    campaign_id=cid)

    for row in redemptions:
        meta = {"redemption_id": row["redemption_id"],
                "transaction_id": row["transaction_id"],
                "offer_id": row["offer_id"],
                "gross_bill_amount": float(row["gross_bill_amount"]),
                "discount_applied": float(row["discount_applied"])}
        if row["status"] != "CONFIRMED":
            meta["reason"] = row["rejection_reason"]
        add("SYS-REDEEM", "REDEMPTION_CONFIRMED" if row["status"] == "CONFIRMED"
            else "REDEMPTION_REJECTED", "REDEMPTION", row["redemption_id"],
            parse_iso(row["redeemed_at"]), **meta)

    for merchant_id in sorted({m["merchant_id"] for m in campaigns}):
        for _ in range(rng.randint(150, 195)):
            when = datetime(2026, rng.randint(1, 9), rng.randint(1, 28),
                            rng.randint(8, 21), rng.randint(0, 59))
            if rng.random() < 0.45:
                add("USR-%s-01" % merchant_id, "LOGIN", "MERCHANT_SESSION",
                    merchant_id, when, method="password+mfa", outcome="success")
            else:
                add("USR-%s-01" % merchant_id, "VIEW_ANALYTICS",
                    "MERCHANT_DASHBOARD", merchant_id, when,
                    view=rng.choice(["segmentation", "campaign_performance",
                                     "redemption_log", "customer_detail"]),
                    filters_applied=rng.randint(0, 4))

    logs.sort(key=lambda r: (r["timestamp"], r["actor_id"], r["action"]))
    for i, row in enumerate(logs, start=1):
        row["audit_id"] = "AUD-%07d" % i
    return logs


# --------------------------------------------------------------------------
# 13. PRD contract bridge (data/*.csv, PRD section 6 naming)
# --------------------------------------------------------------------------


def build_prd_bridge(merchants: list, customers: list, txns: list) -> int:
    os.makedirs(BRIDGE_DIR, exist_ok=True)
    owners = ["R. Kulkarni", "A. Fernandes", "S. Banerjee", "N. Iyer",
              "V. Deshpande", "M. Qureshi", "P. Menon", "D. Ghosh",
              "K. Pillai", "T. Varma"]
    with open(os.path.join(BRIDGE_DIR, "merchants.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["merchant_id", "store_name", "owner_name", "city",
                    "default_margin_pct", "created_at", "is_active"])
        for i, m in enumerate(merchants):
            w.writerow([m["merchant_id"].replace("MER-", "M"), m["store_name"],
                        owners[i % len(owners)], m["city"],
                        m["default_margin_pct"], m["created_at"],
                        m["is_active"]])

    rng = random.Random(SEED + 16)
    with open(os.path.join(BRIDGE_DIR, "customers.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["customer_id", "customer_name", "phone_masked", "created_at",
                    "is_active"])
        for c in customers:
            masked = "%02dXXXXXX%02d" % (rng.randint(70, 99), rng.randint(10, 99))
            w.writerow([c["customer_id"].replace("CUS-", "C"),
                        "Customer %s" % c["customer_reference"].replace("CREF-", ""),
                        masked, c["created_at"],
                        "true" if c["status"] == "ACTIVE" else "false"])

    status_map = {"COMPLETED": "SUCCESS", "FAILED": "FAILED", "REVERSED": "REFUNDED"}
    with open(os.path.join(BRIDGE_DIR, "transactions.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["txn_id", "merchant_id", "customer_id", "txn_datetime",
                    "amount", "payment_mode", "status"])
        for t in txns:
            w.writerow([t["transaction_id"].replace("TXN-", ""),
                        t["merchant_id"].replace("MER-", "M"),
                        t["customer_id"].replace("CUS-", "C"),
                        iso(t["transaction_time"]), "%.2f" % t["amount"],
                        t["payment_method"], status_map[t["status"]]])

    as_of = datetime(2026, 10, 3, 23, 59, 59)
    pairs = defaultdict(list)
    for t in txns:
        if t["status"] != "COMPLETED":
            continue
        pairs[(t["merchant_id"], t["customer_id"])].append(
            (t["transaction_time"], t["amount"]))
    rows = []
    for mid in sorted({m["merchant_id"] for m in merchants}):
        custs = [c for (m, c) in pairs if m == mid]
        if not custs:
            continue
        totals = {c: (len(pairs[(mid, c)]),
                      round(sum(a for _, a in pairs[(mid, c)]), 2)) for c in custs}
        spends = sorted(v[1] for v in totals.values())
        p75 = spends[min(len(spends) - 1, int(len(spends) * 0.75))]
        for c in custs:
            freq, total = totals[c]
            last = max(t for t, _ in pairs[(mid, c)])
            recency = (as_of - last).days
            tags, bits = [], []
            if recency >= 45:
                tags.append("Dormant")
                bits.append("recency %d >= 45" % recency)
            if total >= p75:
                tags.append("High Value")
                bits.append("total_spend %.2f >= p75 %.2f" % (total, p75))
            if freq >= 5:
                tags.append("Loyal")
                bits.append("frequency %d >= 5" % freq)
            if freq == 1:
                tags.append("One-time")
                bits.append("frequency == 1")
            if not tags:
                tags.append("Regular")
            rows.append([mid.replace("MER-", "M"), c.replace("CUS-", "C"),
                         ",".join(tags),
                         "; ".join(bits) if bits else "no rule matched"])
    with open(os.path.join(BRIDGE_DIR, "expected_segments.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["merchant_id", "customer_id", "expected_tags", "reason"])
        w.writerows(rows)
    return len(rows)


# --------------------------------------------------------------------------
# 14. Writers
# --------------------------------------------------------------------------


def write_csv(name: str, rows: list) -> int:
    cols = CSV_COLUMNS[name]
    with open(os.path.join(SYNTH_DIR, name + ".csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            out = {}
            for key in cols:
                value = row.get(key, "")
                if isinstance(value, datetime):
                    value = iso(value)
                elif isinstance(value, float):
                    value = "%.2f" % value
                elif value is None:
                    value = ""
                out[key] = value
            w.writerow(out)
    return len(rows)


# --------------------------------------------------------------------------
# 15. Main
# --------------------------------------------------------------------------


def main() -> None:
    os.makedirs(SYNTH_DIR, exist_ok=True)
    os.makedirs(BRIDGE_DIR, exist_ok=True)

    merchants = build_merchants(random.Random(SEED + 1))
    merchants_by_id = {m["merchant_id"]: m for m in merchants}
    merchant_cat = {m["merchant_id"]: m["category"] for m in merchants}

    cohort_of = assign_cohorts()
    customers = build_customers(merchants, cohort_of, random.Random(SEED + 3))
    customers_by_id = {c["customer_id"]: c for c in customers}

    txns = generate_transactions(customers, merchants, random.Random(SEED + 4))
    finalise_customer_status(customers, txns, random.Random(SEED + 5))

    index = PairIndex(txns)

    factory = OfferFactory(random.Random(SEED + 6))
    build_offers(merchants, txns, factory)
    campaigns = build_campaigns(merchants, factory, index, random.Random(SEED + 7))

    versions = build_versions(campaigns, merchants_by_id, random.Random(SEED + 8))
    events = build_lifecycle_events(campaigns, versions, random.Random(SEED + 9))

    recipients = build_recipients(campaigns, index, random.Random(SEED + 10))
    campaigns_by_id = {c["campaign_id"]: c for c in campaigns}
    add_message_events(recipients, campaigns_by_id, events, random.Random(SEED + 11))

    txn_factory = TxnFactory(random.Random(SEED + 17))
    redemptions = build_redemptions(campaigns, recipients, merchants_by_id,
                                    customers_by_id, txn_factory,
                                    random.Random(SEED + 12))
    inject_invalid_redemptions(campaigns, recipients, redemptions, merchants_by_id,
                               merchant_cat, txn_factory, random.Random(SEED + 13))

    # ---- final transaction ids (redemption references must be remapped) ----
    txns = txns + txn_factory.rows
    txns.sort(key=lambda r: r["transaction_time"])
    remap = {}
    for i, row in enumerate(txns, start=1):
        new_id = "TXN-%07d" % i
        remap[row.get("_tmp_id", row.get("transaction_id"))] = new_id
        row["transaction_id"] = new_id
        row.pop("_tmp_id", None)
    for row in redemptions:
        row["transaction_id"] = remap[row["transaction_id"]]

    # ---- offer budget must cover confirmed discount spend -----------------
    consumed = defaultdict(float)
    for row in redemptions:
        if row["status"] == "CONFIRMED":
            consumed[row["offer_id"]] += float(row["discount_applied"])
    for offer in factory.rows:
        spent = consumed.get(offer["offer_id"], 0.0)
        if spent > float(offer["budget"]):
            offer["budget"] = money(math.ceil(spent * 1.25))
            offer["updated_at"] = iso(min(
                DAEMON_NOW, parse_iso(offer["created_at"]) + timedelta(days=30)))

    # ---- redemption ids / events -----------------------------------------
    redemptions.sort(key=lambda r: (r["redeemed_at"], r["campaign_id"],
                                    r["customer_id"]))
    for i, row in enumerate(redemptions, start=1):
        row["redemption_id"] = "RED-%06d" % i
    add_redemption_events(redemptions, campaigns_by_id, events,
                          random.Random(SEED + 14))

    events.sort(key=lambda e: (e["event_time"], e["campaign_id"], e["event_type"]))
    for i, row in enumerate(events, start=1):
        row["event_id"] = "EVT-%08d" % i

    audits = build_audit_logs(campaigns, factory.rows, redemptions, events,
                              random.Random(SEED + 15))

    counts = {
        "merchants": write_csv("merchants", merchants),
        "customers": write_csv("customers", customers),
        "transactions": write_csv("transactions", txns),
        "offers": write_csv("offers", factory.rows),
        "campaigns": write_csv("campaigns", campaigns),
        "campaign_versions": write_csv("campaign_versions", versions),
        "campaign_recipients": write_csv("campaign_recipients", recipients),
        "redemptions": write_csv("redemptions", redemptions),
        "campaign_events": write_csv("campaign_events", events),
        "audit_logs": write_csv("audit_logs", audits),
    }
    bridge = build_prd_bridge(merchants, customers, txns)

    width = max(len(k) for k in counts)
    for key, value in counts.items():
        print("%-*s : %d" % (width, key, value))
    print("%-*s : %d" % (width, "prd_expected_segments", bridge))


if __name__ == "__main__":
    main()
