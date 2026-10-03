import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from logic.campaign import (  # noqa: E402
    build_campaign_row,
    offer_terms,
    render_campaign_copy,
    send_campaign,
    teaser_discount,
)
from logic.constants import DEMO_TODAY, LARGE_CUSTOMER_ID, SMALL_CUSTOMER_ID  # noqa: E402
from logic.data_loader import load_expected_segments, load_transactions  # noqa: E402
from logic.metrics import campaign_results  # noqa: E402
from logic.redemption import list_customer_offers, redeem, validate_coupon  # noqa: E402
from logic.segmentation import apply_filters, assign_segments, customer_metrics  # noqa: E402
from logic.poster import build_poster_brief, poster_prompt  # noqa: E402
from logic.targeting import rule_to_filters  # noqa: E402


def _tx(customer, when, amount, status="SUCCESS", merchant="M001", txn="T1"):
    return {
        "txn_id": txn,
        "merchant_id": merchant,
        "customer_id": customer,
        "txn_datetime": when,
        "amount": amount,
        "payment_mode": "UPI",
        "status": status,
    }


def test_metrics_ignore_failed_and_compute_recency():
    frame = pd.DataFrame(
        [
            _tx("C1", "2026-01-01 10:00:00", 100, txn="T1"),
            _tx("C1", "2026-01-11 10:00:00", 300, txn="T2"),
            _tx("C1", "2026-01-12 10:00:00", 999, status="FAILED", txn="T3"),
        ]
    )
    frame["txn_datetime"] = pd.to_datetime(frame["txn_datetime"])
    success = frame.loc[frame["status"] == "SUCCESS"]
    metrics = customer_metrics(success, demo_today=datetime(2026, 2, 1))
    row = metrics.iloc[0]
    assert row["frequency"] == 2
    assert row["total_spend"] == 400
    assert row["avg_spend"] == 200
    assert int(row["recency_days"]) == 21


def test_segment_rules_and_priority():
    rows = []
    spec = [
        ("C1", 1, 100, 10),
        ("C2", 6, 200, 5),
        ("C3", 2, 300, 60),
        ("C4", 6, 1000, 3),
    ]
    for cid, freq, total, recency in spec:
        each = total / freq
        for i in range(freq):
            day = datetime(2026, 2, 1).toordinal() - recency
            when = datetime.fromordinal(day).strftime("%Y-%m-%d 10:00:00")
            rows.append(_tx(cid, when, each, txn=f"{cid}-{i}"))
    frame = pd.DataFrame(rows)
    frame["txn_datetime"] = pd.to_datetime(frame["txn_datetime"])
    tagged = assign_segments(customer_metrics(frame))
    labels = dict(zip(tagged["customer_id"], tagged["segment_label"]))
    assert labels["C1"] == "One-time"
    assert labels["C2"] == "Loyal"
    assert labels["C3"] == "Dormant"
    assert labels["C4"] == "High Value,Loyal"


def test_csv_matches_expected_segments_and_quotas():
    txns = load_transactions()
    success = txns.loc[txns["status"] == "SUCCESS"].copy()
    tagged = assign_segments(customer_metrics(success))
    expected = load_expected_segments()
    merged = tagged.merge(expected, on=["merchant_id", "customer_id"])
    assert len(merged) == len(expected)
    assert (merged["segment_label"] == merged["expected_tags"]).all()

    m001 = tagged.loc[tagged["merchant_id"] == "M001", "segment_label"]
    assert m001.str.contains("One-time").sum() >= 15
    assert m001.str.contains("Loyal").sum() >= 15
    assert m001.str.contains("Dormant").sum() >= 15
    assert m001.str.contains("High Value").sum() >= 10
    both = m001.apply(lambda s: "Dormant" in s.split(",") and "High Value" in s.split(","))
    assert int(both.sum()) >= 5
    assert int(m001.eq("Regular").sum()) >= 20
    assert int(txns["status"].isin(["FAILED", "REFUNDED"]).sum()) >= 10
    assert txns["merchant_id"].nunique() >= 2


def test_filters_return_dormant_audience():
    txns = load_transactions()
    success = txns.loc[(txns["merchant_id"] == "M001") & (txns["status"] == "SUCCESS")]
    tagged = assign_segments(customer_metrics(success))
    audience = apply_filters(tagged, {"segment": ["Dormant"], "min_frequency": 2})
    assert not audience.empty
    assert audience["segments"].apply(lambda tags: "Dormant" in tags).all()
    assert (audience["frequency"] >= 2).all()


def test_copy_keeps_locked_terms():
    text = render_campaign_copy(
        "Sharma Snacks & More",
        "FLAT_CASHBACK",
        50,
        299,
        datetime(2026, 2, 15, 23, 59, 59),
        goal="Bring back dormant customers",
    )
    assert "₹50" in text
    assert "₹299" in text
    assert "15 Feb 2026" in text
    assert "Sharma Snacks & More" in text
    assert "Bring back dormant customers" not in text
    assert teaser_discount("PERCENT_DISCOUNT", 5) == "5% off*"
    assert teaser_discount("STOCK_CLEARANCE", 0, "Samosa") == "Buy 1 Get 1 Free*"
    brief = build_poster_brief(
        "Sharma Snacks & More",
        "Diwali offer",
        "STOCK_CLEARANCE",
        40,
        299,
        "2026-02-15",
        free_item_name="Samosa",
        merchant_note="Make it 90% off",
    )
    assert brief["offer_details"]["headline_offer"] == "BUY 1 GET 1 FREE"
    assert "Samosa" in brief["offer_details"]["terms"]
    assert "299" in brief["offer_details"]["terms"]
    assert "90%" not in brief["offer_details"]["headline_offer"]
    assert "BUY 1 GET 1 FREE" in poster_prompt(brief)
    terms = offer_terms("PERCENT_DISCOUNT", 5, 500, datetime(2027, 1, 1), max_discount=100)
    assert "Minimum bill ₹500" in terms
    assert "Up to ₹100" in " ".join(terms)
    assert "5% off" not in " ".join(terms)


def _draft(audience_ids):
    campaign = build_campaign_row(
        pd.DataFrame(columns=["campaign_id", "merchant_id"]),
        "M001",
        "Sharma Snacks & More",
        "FLAT_CASHBACK",
        50,
        299,
        datetime(2026, 2, 15, 23, 59, 59),
        {"segment": ["Dormant"]},
        True,
        "Bring back dormant customers",
    )
    audience = pd.DataFrame({"customer_id": audience_ids})
    sent, recipients, messages = send_campaign(
        campaign,
        audience,
        pd.DataFrame(),
        pd.DataFrame(),
        sent_at=DEMO_TODAY,
    )
    campaigns = pd.DataFrame([sent])
    return campaigns, recipients, messages


def test_send_creates_unique_coupons():
    campaigns, recipients, messages = _draft(["C001", "C002", "C053"])
    assert campaigns.iloc[0]["status"] == "SENT"
    assert recipients["coupon_code"].nunique() == 3
    assert set(recipients["coupon_code"]) == {
        "PTM-M001-001-C001",
        "PTM-M001-001-C002",
        "PTM-M001-001-C053",
    }
    assert len(messages) == 3


def test_redemption_rules_and_dashboard():
    campaigns, recipients, _messages = _draft(["C053", "C054"])
    empty_pay = pd.DataFrame()
    empty_red = pd.DataFrame()

    fake = validate_coupon("PTM-NOPE", "C053", "M001", 500, recipients, campaigns)
    assert fake["reason"] == "Invalid coupon"

    wrong = validate_coupon(
        "PTM-M001-001-C053", "C054", "M001", 500, recipients, campaigns
    )
    assert wrong["reason"] == "Coupon belongs to another customer"

    low = validate_coupon(
        "PTM-M001-001-C053", "C053", "M001", 100, recipients, campaigns
    )
    assert low["reason"] == "Bill amount below minimum"

    expired = validate_coupon(
        "PTM-M001-001-C053",
        "C053",
        "M001",
        500,
        recipients,
        campaigns,
        as_of=datetime(2026, 2, 16),
    )
    assert expired["reason"] == "Coupon expired"

    first = redeem(
        "PTM-M001-001-C053",
        "C053",
        "M001",
        500,
        recipients,
        campaigns,
        empty_pay,
        empty_red,
        as_of=DEMO_TODAY,
    )
    assert first["ok"]
    assert first["payment"]["discount_applied"] == 50
    assert first["payment"]["final_paid_amount"] == 450

    second = redeem(
        "PTM-M001-001-C053",
        "C053",
        "M001",
        500,
        first["recipients"],
        campaigns,
        first["payments"],
        first["redemptions"],
        as_of=DEMO_TODAY,
    )
    assert second["reason"] == "Coupon already redeemed"

    stats = campaign_results("CMP-M001-001", first["recipients"], first["redemptions"], 25)
    assert stats["sent_count"] == 2
    assert stats["redeemed_count"] == 1
    assert stats["campaign_revenue"] == 500
    assert stats["discount_cost"] == 50
    assert stats["estimated_profit"] == pytest.approx(75)


def _m001_scores():
    txns = load_transactions()
    success = txns.loc[(txns["merchant_id"] == "M001") & (txns["status"] == "SUCCESS")]
    return assign_segments(customer_metrics(success))


def test_amount_rules_split_recent_small_and_large_customers():
    scores = _m001_scores()
    small = apply_filters(scores, rule_to_filters("amount", "avg", "at_most", 500, 45))
    large = apply_filters(scores, rule_to_filters("amount", "sum", "at_least", 5000, 45))
    small_ids = set(small["customer_id"])
    large_ids = set(large["customer_id"])
    assert SMALL_CUSTOMER_ID in small_ids
    assert LARGE_CUSTOMER_ID not in small_ids
    assert LARGE_CUSTOMER_ID in large_ids
    assert SMALL_CUSTOMER_ID not in large_ids
    assert "C053" not in large_ids


def test_customer_picks_one_offer_when_several_campaigns_match():
    first_campaigns, recipients, _messages = _draft(["C001"])
    second = build_campaign_row(
        first_campaigns,
        "M001",
        "Sharma Snacks & More",
        "PERCENT_DISCOUNT",
        10,
        100,
        datetime(2026, 2, 15, 23, 59, 59),
        {"max_avg_spend": 500},
        True,
        "Increase repeat visits",
        max_discount=100,
    )
    sent, recipients, _messages = send_campaign(
        second,
        pd.DataFrame({"customer_id": ["C001"]}),
        recipients,
        pd.DataFrame(),
        sent_at=DEMO_TODAY,
    )
    campaigns = pd.concat([first_campaigns, pd.DataFrame([sent])], ignore_index=True)
    offers = list_customer_offers("C001", recipients, campaigns, 500, as_of=DEMO_TODAY)
    selectable = [offer["coupon_code"] for offer in offers if offer["selectable"]]
    assert len(selectable) == 2

    low_bill = list_customer_offers("C001", recipients, campaigns, 150, as_of=DEMO_TODAY)
    by_code = {offer["coupon_code"]: offer for offer in low_bill}
    assert by_code["PTM-M001-001-C001"]["reason"] == "Bill amount below minimum"
    assert by_code["PTM-M001-002-C001"]["selectable"]

    paid = redeem(
        "PTM-M001-002-C001",
        "C001",
        "M001",
        500,
        recipients,
        campaigns,
        pd.DataFrame(),
        pd.DataFrame(),
        as_of=DEMO_TODAY,
    )
    assert paid["ok"]
    assert paid["payment"]["final_paid_amount"] == 450
    after = list_customer_offers("C001", paid["recipients"], campaigns, 500, as_of=DEMO_TODAY)
    states = {offer["coupon_code"]: offer["selectable"] for offer in after}
    assert states["PTM-M001-002-C001"] is False
    assert states["PTM-M001-001-C001"] is True
