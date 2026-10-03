from __future__ import annotations

import json
from datetime import datetime

import pandas as pd

from logic.constants import DEMO_TODAY


def teaser_discount(
    offer_type: str,
    discount_value: float,
    free_item_name: str | None = None,
) -> str:
    """Customer-facing line. The asterisk points at the terms."""
    if offer_type == "FLAT_CASHBACK":
        return f"₹{_inr(discount_value)} cashback*"
    if offer_type == "PERCENT_DISCOUNT":
        return f"{_inr(discount_value)}% off*"
    if offer_type == "STOCK_CLEARANCE":
        return "Buy 1 Get 1 Free*"
    item = free_item_name or "item"
    return f"Free {item}*"


def offer_terms(
    offer_type: str,
    discount_value: float,
    min_bill_amount: float,
    expiry: datetime,
    max_discount: float | None = None,
    free_item_name: str | None = None,
    coupon_code: str | None = None,
) -> list[str]:
    lines = []
    if offer_type == "PERCENT_DISCOUNT" and max_discount:
        lines.append(f"*Up to ₹{_inr(max_discount)}")
    elif offer_type == "FLAT_CASHBACK":
        lines.append(f"*₹{_inr(discount_value)} off the bill")
    elif offer_type == "STOCK_CLEARANCE":
        lines.append(f"*Buy 1 get 1 free on {free_item_name or 'the item you choose'}")
    else:
        lines.append(f"*Free {free_item_name or 'item'}")
    lines.append(f"Minimum bill ₹{_inr(min_bill_amount)}")
    lines.append(f"Valid till {_day_month(expiry)}")
    if coupon_code:
        lines.append(f"Code {coupon_code}")
    return lines


def render_offer_text(
    offer_type: str,
    discount_value: float,
    min_bill_amount: float,
    max_discount: float | None = None,
    free_item_name: str | None = None,
) -> str:
    if offer_type == "FLAT_CASHBACK":
        return f"₹{ _inr(discount_value) } cashback on bills above ₹{ _inr(min_bill_amount) }"
    if offer_type == "PERCENT_DISCOUNT":
        cap = f" up to ₹{ _inr(max_discount) }" if max_discount else ""
        return f"{_inr(discount_value)}% off{cap} on bills above ₹{ _inr(min_bill_amount) }"
    item = free_item_name or "item"
    if offer_type == "STOCK_CLEARANCE":
        return f"buy 1 get 1 free on {item} on bills above ₹{ _inr(min_bill_amount) }"
    return f"a free {item} on bills above ₹{ _inr(min_bill_amount) }"


def render_campaign_copy(
    store_name: str,
    offer_type: str,
    discount_value: float,
    min_bill_amount: float,
    expiry: datetime,
    max_discount: float | None = None,
    free_item_name: str | None = None,
    goal: str | None = None,
) -> str:
    """Template copy. Numeric terms are copied from the locked offer, never rewritten."""
    expiry_label = pd.Timestamp(expiry).strftime("%d %b %Y").replace(" 0", " ")
    # strftime %-d is not portable on Windows; strip leading zero on the day.
    expiry_label = _day_month(expiry)
    bill = _inr(min_bill_amount)

    if offer_type == "FLAT_CASHBACK":
        body = (
            f"Pay with Paytm at {store_name} and get ₹{ _inr(discount_value) } cashback "
            f"on bills above ₹{bill}."
        )
    elif offer_type == "PERCENT_DISCOUNT":
        cap = f" up to ₹{ _inr(max_discount) }" if max_discount else ""
        body = (
            f"Get {_inr(discount_value)}% off{cap} at {store_name} when you pay with Paytm. "
            f"Minimum bill ₹{bill}."
        )
    elif offer_type == "STOCK_CLEARANCE":
        item = free_item_name or "item"
        body = (
            f"Stock clearance at {store_name}: buy 1 get 1 free on {item} "
            f"on bills above ₹{bill}."
        )
    else:
        item = free_item_name or "item"
        body = (
            f"Pay with Paytm at {store_name} and get a free {item} on bills above ₹{bill}."
        )

    # Goal stays on the merchant record. It is not part of customer-facing copy.
    del goal
    return f"{body} Use your personal coupon code before {expiry_label}."


def next_campaign_id(existing: pd.DataFrame, merchant_id: str) -> tuple[str, str]:
    prefix = f"CMP-{merchant_id}-"
    numbers = []
    if not existing.empty:
        for campaign_id in existing.loc[existing["merchant_id"] == merchant_id, "campaign_id"]:
            parts = str(campaign_id).split("-")
            if len(parts) >= 3 and parts[-1].isdigit():
                numbers.append(int(parts[-1]))
    number = (max(numbers) + 1) if numbers else 1
    token = f"{number:03d}"
    return f"{prefix}{token}", token


def coupon_code(merchant_id: str, campaign_number: str, customer_id: str) -> str:
    return f"PTM-{merchant_id}-{campaign_number}-{customer_id}"


def conversation_id(merchant_id: str, customer_id: str) -> str:
    return f"{merchant_id}_{customer_id}"


def build_campaign_row(
    existing: pd.DataFrame,
    merchant_id: str,
    store_name: str,
    offer_type: str,
    discount_value: float,
    min_bill_amount: float,
    expiry: datetime,
    filters: dict,
    include_poster: bool,
    goal: str,
    max_discount: float | None = None,
    free_item_name: str | None = None,
    free_item_cost: float | None = None,
    created_at: datetime | None = None,
    campaign_name: str = "Offer",
    poster_file: str = "",
) -> dict:
    campaign_id, _number = next_campaign_id(existing, merchant_id)
    created = created_at or DEMO_TODAY.replace(hour=14, minute=0, second=0)
    copy = render_campaign_copy(
        store_name,
        offer_type,
        discount_value,
        min_bill_amount,
        expiry,
        max_discount=max_discount,
        free_item_name=free_item_name,
        goal=goal,
    )
    return {
        "campaign_id": campaign_id,
        "merchant_id": merchant_id,
        "store_name": store_name,
        "campaign_name": (campaign_name or "Offer").strip() or "Offer",
        "status": "DRAFT",
        "offer_type": offer_type,
        "discount_value": float(discount_value),
        "max_discount": None if max_discount in (None, "") else float(max_discount),
        "min_bill_amount": float(min_bill_amount),
        "free_item_name": free_item_name or "",
        "free_item_cost": None if free_item_cost in (None, "") else float(free_item_cost),
        "expiry_datetime": pd.Timestamp(expiry).strftime("%Y-%m-%d %H:%M:%S"),
        "target_filters_json": json.dumps(filters, default=str),
        "campaign_copy": copy,
        "include_poster": bool(include_poster),
        "campaign_goal": goal,
        "poster_file": poster_file or "",
        "created_at": pd.Timestamp(created).strftime("%Y-%m-%d %H:%M:%S"),
        "sent_at": "",
    }


def send_campaign(
    campaign: dict,
    audience: pd.DataFrame,
    recipients: pd.DataFrame,
    messages: pd.DataFrame,
    sent_at: datetime | None = None,
) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    if audience.empty:
        raise ValueError("Audience is empty")

    sent = sent_at or DEMO_TODAY.replace(hour=14, minute=5, second=0)
    sent_label = pd.Timestamp(sent).strftime("%Y-%m-%d %H:%M:%S")
    number = campaign["campaign_id"].split("-")[-1]
    offer_text = render_offer_text(
        campaign["offer_type"],
        float(campaign["discount_value"]),
        float(campaign["min_bill_amount"]),
        _optional_float(campaign.get("max_discount")),
        campaign.get("free_item_name") or None,
    )

    new_recipients = []
    new_messages = []
    next_message = _next_id(messages, "message_id", "MSG")
    for customer_id in audience["customer_id"].tolist():
        code = coupon_code(campaign["merchant_id"], number, customer_id)
        conv = conversation_id(campaign["merchant_id"], customer_id)
        new_recipients.append(
            {
                "campaign_id": campaign["campaign_id"],
                "merchant_id": campaign["merchant_id"],
                "customer_id": customer_id,
                "coupon_code": code,
                "conversation_id": conv,
                "delivery_status": "SENT",
                "notification_seen": False,
                "redeemed_status": "NOT_REDEEMED",
                "sent_at": sent_label,
            }
        )
        new_messages.append(
            {
                "message_id": f"MSG{next_message:04d}",
                "conversation_id": conv,
                "campaign_id": campaign["campaign_id"],
                "merchant_id": campaign["merchant_id"],
                "customer_id": customer_id,
                "message_type": "OFFER_CARD" if campaign["include_poster"] else "TEXT",
                "message_text": campaign["campaign_copy"],
                "coupon_code": code,
                "created_at": sent_label,
                "include_poster": bool(campaign["include_poster"]),
                "store_name": campaign["store_name"],
                "campaign_name": campaign.get("campaign_name") or "Offer",
                "offer_text": offer_text,
                "min_bill_amount": campaign["min_bill_amount"],
                "expiry_datetime": campaign["expiry_datetime"],
                "poster_file": "" if _missing(campaign.get("poster_file")) else str(campaign.get("poster_file")),
            }
        )
        next_message += 1

    updated = dict(campaign)
    updated["status"] = "SENT"
    updated["sent_at"] = sent_label
    rec_out = pd.concat([recipients, pd.DataFrame(new_recipients)], ignore_index=True)
    msg_out = pd.concat([messages, pd.DataFrame(new_messages)], ignore_index=True)
    return updated, rec_out, msg_out


def _next_id(df: pd.DataFrame, column: str, prefix: str) -> int:
    if df.empty or column not in df.columns:
        return 1
    numbers = []
    for value in df[column].astype(str):
        tail = value.replace(prefix, "")
        if tail.isdigit():
            numbers.append(int(tail))
    return (max(numbers) + 1) if numbers else 1


def _missing(value) -> bool:
    if value is None or value == "":
        return True
    try:
        return bool(pd.isna(value))
    except TypeError:
        return False


def _optional_float(value) -> float | None:
    if value is None or value == "" or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    return float(value)


def _inr(value: float) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _day_month(value: datetime) -> str:
    ts = pd.Timestamp(value)
    return f"{ts.day} {ts.strftime('%b %Y')}"
