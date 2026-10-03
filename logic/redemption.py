from __future__ import annotations

from datetime import datetime

import pandas as pd

from logic.constants import DEMO_TODAY


def discount_amount(campaign: pd.Series | dict, gross_amount: float) -> float:
    offer_type = campaign["offer_type"]
    value = float(campaign["discount_value"])
    if offer_type == "FLAT_CASHBACK":
        discount = value
    elif offer_type == "PERCENT_DISCOUNT":
        discount = gross_amount * value / 100.0
        cap = campaign.get("max_discount")
        if cap not in (None, "") and not _is_na(cap):
            discount = min(discount, float(cap))
    else:
        cost = campaign.get("free_item_cost")
        discount = float(cost) if cost not in (None, "") and not _is_na(cost) else 0.0
    return round(min(discount, gross_amount), 2)


def validate_coupon(
    coupon_code: str,
    customer_id: str,
    merchant_id: str,
    bill_amount: float,
    recipients: pd.DataFrame,
    campaigns: pd.DataFrame,
    as_of: datetime | None = None,
) -> dict:
    clock = pd.Timestamp(as_of or DEMO_TODAY)
    code = (coupon_code or "").strip()
    match = recipients.loc[recipients["coupon_code"] == code] if not recipients.empty else recipients
    if match.empty:
        return _invalid("Invalid coupon")

    recipient = match.iloc[0]
    if str(recipient["customer_id"]) != str(customer_id):
        return _invalid("Coupon belongs to another customer")
    if str(recipient["merchant_id"]) != str(merchant_id):
        return _invalid("Invalid coupon")

    campaign_rows = campaigns.loc[campaigns["campaign_id"] == recipient["campaign_id"]]
    if campaign_rows.empty:
        return _invalid("Invalid coupon")
    campaign = campaign_rows.iloc[0]
    if str(campaign["status"]) != "SENT":
        return _invalid("Invalid coupon")
    if str(recipient["redeemed_status"]) == "REDEEMED":
        return _invalid("Coupon already redeemed")

    expiry = pd.Timestamp(campaign["expiry_datetime"])
    if clock.normalize() > expiry.normalize():
        return _invalid("Coupon expired")
    if float(bill_amount) < float(campaign["min_bill_amount"]):
        return _invalid("Bill amount below minimum")

    discount = discount_amount(campaign, float(bill_amount))
    final_paid = round(float(bill_amount) - discount, 2)
    return {
        "valid": True,
        "reason": "VALID",
        "recipient": recipient,
        "campaign": campaign,
        "discount_applied": discount,
        "final_paid_amount": final_paid,
    }


def redeem(
    coupon_code: str,
    customer_id: str,
    merchant_id: str,
    bill_amount: float,
    recipients: pd.DataFrame,
    campaigns: pd.DataFrame,
    payments: pd.DataFrame,
    redemptions: pd.DataFrame,
    as_of: datetime | None = None,
) -> dict:
    clock = pd.Timestamp(as_of or DEMO_TODAY)
    result = validate_coupon(
        coupon_code,
        customer_id,
        merchant_id,
        bill_amount,
        recipients,
        campaigns,
        as_of=clock,
    )
    if not result["valid"]:
        return {
            "ok": False,
            "reason": result["reason"],
            "recipients": recipients,
            "payments": payments,
            "redemptions": redemptions,
        }

    paid_at = clock.strftime("%Y-%m-%d %H:%M:%S")
    payment_id = _next_id(payments, "payment_txn_id", "PAY")
    redemption_id = _next_id(redemptions, "redemption_id", "RED")
    payment = {
        "payment_txn_id": f"PAY{payment_id:04d}",
        "merchant_id": merchant_id,
        "customer_id": customer_id,
        "gross_amount": round(float(bill_amount), 2),
        "coupon_code": coupon_code.strip(),
        "discount_applied": result["discount_applied"],
        "final_paid_amount": result["final_paid_amount"],
        "payment_datetime": paid_at,
        "status": "SUCCESS",
    }
    redemption = {
        "redemption_id": f"RED{redemption_id:04d}",
        "campaign_id": result["campaign"]["campaign_id"],
        "coupon_code": coupon_code.strip(),
        "customer_id": customer_id,
        "merchant_id": merchant_id,
        "payment_txn_id": payment["payment_txn_id"],
        "gross_bill_amount": payment["gross_amount"],
        "discount_amount": result["discount_applied"],
        "final_paid_amount": result["final_paid_amount"],
        "redeemed_at": paid_at,
        "validation_status": "VALID",
    }

    rec = recipients.copy()
    rec.loc[rec["coupon_code"] == coupon_code.strip(), "redeemed_status"] = "REDEEMED"
    pay_out = pd.concat([payments, pd.DataFrame([payment])], ignore_index=True)
    red_out = pd.concat([redemptions, pd.DataFrame([redemption])], ignore_index=True)
    return {
        "ok": True,
        "reason": "VALID",
        "payment": payment,
        "redemption": redemption,
        "recipients": rec,
        "payments": pay_out,
        "redemptions": red_out,
    }


def list_customer_offers(
    customer_id: str,
    recipients: pd.DataFrame,
    campaigns: pd.DataFrame,
    bill_amount: float,
    as_of: datetime | None = None,
) -> list[dict]:
    """Offers this customer can see at pay time. One coupon belongs to one campaign."""
    if recipients.empty or campaigns.empty:
        return []
    clock = pd.Timestamp(as_of or DEMO_TODAY)
    mine = recipients.loc[recipients["customer_id"].astype(str) == str(customer_id)]
    offers = []
    for recipient in mine.itertuples(index=False):
        rows = campaigns.loc[campaigns["campaign_id"] == recipient.campaign_id]
        if rows.empty or str(rows.iloc[0]["status"]) != "SENT":
            continue
        campaign = rows.iloc[0]
        reason = ""
        if str(recipient.redeemed_status) == "REDEEMED":
            reason = "Coupon already redeemed"
        elif clock.normalize() > pd.Timestamp(campaign["expiry_datetime"]).normalize():
            reason = "Coupon expired"
        elif float(bill_amount) < float(campaign["min_bill_amount"]):
            reason = "Bill amount below minimum"
        discount = discount_amount(campaign, float(bill_amount)) if not reason else 0.0
        final_paid = round(float(bill_amount) - discount, 2) if not reason else float(bill_amount)
        offers.append(
            {
                "campaign_id": campaign["campaign_id"],
                "coupon_code": recipient.coupon_code,
                "offer_type": campaign["offer_type"],
                "discount_value": campaign["discount_value"],
                "max_discount": campaign.get("max_discount"),
                "min_bill_amount": float(campaign["min_bill_amount"]),
                "free_item_name": campaign.get("free_item_name") or "",
                "campaign_name": _text(campaign.get("campaign_name"), "Offer"),
                "store_name": campaign["store_name"],
                "expiry_datetime": campaign["expiry_datetime"],
                "campaign_copy": campaign["campaign_copy"],
                "include_poster": campaign["include_poster"],
                "selectable": reason == "",
                "reason": reason,
                "discount_applied": discount,
                "final_paid_amount": final_paid,
            }
        )
    return offers


def _text(value, fallback: str) -> str:
    if value is None:
        return fallback
    try:
        if pd.isna(value):
            return fallback
    except TypeError:
        pass
    text = str(value).strip()
    return text or fallback


def _invalid(reason: str) -> dict:
    return {"valid": False, "reason": reason}


def _is_na(value) -> bool:
    try:
        return bool(pd.isna(value))
    except TypeError:
        return False


def _next_id(df: pd.DataFrame, column: str, prefix: str) -> int:
    if df.empty or column not in df.columns:
        return 1
    numbers = []
    for value in df[column].astype(str):
        tail = value.replace(prefix, "")
        if tail.isdigit():
            numbers.append(int(tail))
    return (max(numbers) + 1) if numbers else 1
