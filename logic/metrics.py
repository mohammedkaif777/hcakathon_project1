from __future__ import annotations

import pandas as pd


def merchant_snapshot(transactions: pd.DataFrame) -> dict:
    if transactions.empty:
        return {
            "customers": 0,
            "transactions": 0,
            "revenue": 0.0,
            "average_bill": 0.0,
        }
    return {
        "customers": int(transactions["customer_id"].nunique()),
        "transactions": int(len(transactions)),
        "revenue": round(float(transactions["amount"].sum()), 2),
        "average_bill": round(float(transactions["amount"].mean()), 2),
    }


def campaign_results(
    campaign_id: str,
    recipients: pd.DataFrame,
    redemptions: pd.DataFrame,
    margin_pct: float,
) -> dict:
    sent = recipients.loc[recipients["campaign_id"] == campaign_id] if not recipients.empty else recipients
    redeemed = (
        redemptions.loc[
            (redemptions["campaign_id"] == campaign_id)
            & (redemptions["validation_status"] == "VALID")
        ]
        if not redemptions.empty
        else redemptions
    )
    sent_count = int(len(sent))
    redeemed_count = int(len(redeemed))
    revenue = round(float(redeemed["gross_bill_amount"].sum()), 2) if redeemed_count else 0.0
    discount_cost = round(float(redeemed["discount_amount"].sum()), 2) if redeemed_count else 0.0
    rate = round(redeemed_count / sent_count, 4) if sent_count else 0.0
    profit = round(revenue * float(margin_pct) / 100.0 - discount_cost, 2)
    return {
        "sent_count": sent_count,
        "redeemed_count": redeemed_count,
        "redemption_rate": rate,
        "campaign_revenue": revenue,
        "discount_cost": discount_cost,
        "estimated_profit": profit,
    }
