"""Build deterministic CSVs that satisfy the PRD segment quotas."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from logic.segmentation import assign_segments, customer_metrics  # noqa: E402

DATA = ROOT / "data"
NAMES = [
    "Aarav Mehta", "Vivaan Shah", "Aditya Rao", "Vihaan Iyer", "Arjun Nair",
    "Sai Kapoor", "Reyansh Gupta", "Krishna Das", "Ishaan Joshi", "Shaurya Jain",
    "Ayaan Khan", "Atharv Bose", "Kabir Malhotra", "Anay Singh", "Dhruv Patel",
    "Aanya Sharma", "Diya Reddy", "Myra Verma", "Anika Chawla", "Sara Kulkarni",
    "Navya Bansal", "Kiara Menon", "Pari Deshmukh", "Anvi Saxena", "Aadhya Pillai",
    "Meera Chopra", "Ira Bhatt", "Saanvi Kaur", "Zara Qureshi", "Mira Sethi",
    "Rohan Desai", "Kunal Agarwal", "Nikhil Pandey", "Harsh Trivedi", "Yash Goyal",
    "Manav Chauhan", "Dev Mishra", "Omkar Jadhav", "Rahul Shetty", "Varun Khanna",
    "Neha Kapoor", "Pooja Nair", "Ritika Shah", "Sneha Iyer", "Tanvi Rao",
    "Ishita Jain", "Kavya Menon", "Lakshmi Nair", "Naina Gupta", "Ojasvi Das",
    "Pranav Kulkarni", "Raghav Bose", "Siddharth Jain", "Tarun Shah", "Uday Rao",
    "Vikram Singh", "Yuvraj Patel", "Zaid Khan", "Amit Sharma", "Bhavya Reddy",
    "Chirag Mehta", "Deepak Joshi", "Esha Kapoor", "Farhan Ali", "Gauri Nair",
    "Harshita Rao", "Imran Sheikh", "Juhi Malhotra", "Karan Bansal", "Lavanya Iyer",
    "Mohit Gupta", "Nandini Shah", "Om Prakash", "Pallavi Das", "Qasim Khan",
    "Riya Sen", "Sahil Verma", "Trisha Bose", "Uma Krishnan", "Vinay Nair",
]


def _txn(txn_id, merchant, customer, when, amount, mode="UPI", status="SUCCESS"):
    return {
        "txn_id": txn_id,
        "merchant_id": merchant,
        "customer_id": customer,
        "txn_datetime": when,
        "amount": round(amount, 2),
        "payment_mode": mode,
        "status": status,
    }


def _spread(start: str, count: int, end: str) -> list[str]:
    days = pd.date_range(start, end, periods=count)
    return [d.strftime("%Y-%m-%d %H:%M:%S") for d in days]


def build_m001_transactions() -> tuple[list[dict], list[dict]]:
    customers = []
    txns = []
    seq = 1

    def add_customer(cid, name, phone_tail, tx_rows):
        customers.append(
            {
                "customer_id": cid,
                "customer_name": name,
                "phone_masked": f"98XXXX{phone_tail}",
                "created_at": "2025-01-03 12:30:00",
                "is_active": True,
            }
        )
        nonlocal seq
        for when, amount, mode in tx_rows:
            txns.append(_txn(f"TXN{seq:04d}", "M001", cid, when, amount, mode))
            seq += 1

    # C001-C015 one-time, recent, low spend
    for i in range(15):
        cid = f"C{i + 1:03d}"
        day = 10 + (i % 15)
        add_customer(
            cid,
            NAMES[i],
            f"{20 + i:02d}",
            [(f"2026-01-{day:02d} 18:10:00", 160 + i * 8, "UPI")],
        )

    # C016-C035 regular: 3 recent visits, total under 800
    for i in range(20):
        cid = f"C{i + 16:03d}"
        dates = _spread("2026-01-05", 3, "2026-01-26")
        amount = 140 + (i % 5) * 10
        add_customer(
            cid,
            NAMES[15 + i],
            f"{40 + i:02d}",
            [(dates[0], amount, "UPI"), (dates[1], amount + 20, "Wallet"), (dates[2], amount + 10, "Card")],
        )

    # C036-C045 dormant, frequency 2, low spend
    for i in range(10):
        cid = f"C{i + 36:03d}"
        add_customer(
            cid,
            NAMES[35 + i],
            f"{60 + i:02d}",
            [
                ("2025-11-02 13:00:00", 180 + i * 5, "UPI"),
                ("2025-12-01 19:20:00", 190 + i * 5, "UPI"),
            ],
        )

    # C046-C047 loyal + dormant, low spend
    for i in range(2):
        cid = f"C{i + 46:03d}"
        dates = _spread("2025-09-01", 6, "2025-11-20")
        add_customer(
            cid,
            NAMES[45 + i],
            f"{70 + i:02d}",
            [(dates[k], 90 + i * 5, "UPI" if k % 2 == 0 else "Card") for k in range(6)],
        )

    # C048-C052 loyal, recent, low spend
    for i in range(5):
        cid = f"C{i + 48:03d}"
        dates = _spread("2025-12-20", 6, "2026-01-22")
        add_customer(
            cid,
            NAMES[47 + i],
            f"{72 + i:02d}",
            [(dates[k], 110 + i * 4, "Wallet" if k % 2 else "UPI") for k in range(6)],
        )

    # C053-C060 dormant + high value
    for i in range(8):
        cid = f"C{i + 53:03d}"
        add_customer(
            cid,
            NAMES[52 + i],
            f"{80 + i:02d}",
            [
                ("2025-10-12 12:00:00", 2100 + i * 40, "Card"),
                ("2025-11-08 17:40:00", 2300 + i * 30, "UPI"),
                ("2025-12-05 20:15:00", 2200 + i * 25, "UPI"),
            ],
        )

    # C061-C070 loyal + high value, recent
    for i in range(10):
        cid = f"C{i + 61:03d}"
        dates = _spread("2025-12-18", 6, "2026-01-25")
        add_customer(
            cid,
            NAMES[60 + i],
            f"{10 + i:02d}",
            [(dates[k], 1500 + i * 20, "UPI" if k % 2 == 0 else "Card") for k in range(6)],
        )

    # Failed and refunded rows do not affect segments.
    noise = [
        ("C001", "2026-01-11 09:00:00", 99, "FAILED"),
        ("C016", "2026-01-12 09:00:00", 80, "FAILED"),
        ("C020", "2026-01-13 09:00:00", 70, "REFUNDED"),
        ("C036", "2025-12-02 09:00:00", 50, "FAILED"),
        ("C048", "2026-01-14 09:00:00", 40, "REFUNDED"),
        ("C053", "2025-12-06 09:00:00", 100, "FAILED"),
        ("C061", "2026-01-16 09:00:00", 120, "FAILED"),
        ("C070", "2026-01-26 09:00:00", 60, "REFUNDED"),
        ("C010", "2026-01-19 09:00:00", 30, "FAILED"),
        ("C030", "2026-01-21 09:00:00", 45, "REFUNDED"),
    ]
    for cid, when, amount, status in noise:
        txns.append(_txn(f"TXN{seq:04d}", "M001", cid, when, amount, "UPI", status))
        seq += 1

    return customers, txns


def build_m002(start_seq: int) -> tuple[list[dict], list[dict]]:
    customers = []
    txns = []
    seq = start_seq
    for i in range(8):
        cid = f"C2{i + 1:02d}"
        customers.append(
            {
                "customer_id": cid,
                "customer_name": f"North Guest {i + 1}",
                "phone_masked": f"97XXXX{i:02d}",
                "created_at": "2025-06-01 10:00:00",
                "is_active": True,
            }
        )
        visits = 1 if i < 3 else 3
        for k in range(visits):
            txns.append(
                _txn(
                    f"TXN{seq:04d}",
                    "M002",
                    cid,
                    f"2026-01-{10 + i:02d} {10 + k:02d}:00:00",
                    250 + i * 15,
                    "UPI",
                )
            )
            seq += 1
    return customers, txns


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    m001_customers, m001_txns = build_m001_transactions()
    m002_customers, m002_txns = build_m002(len(m001_txns) + 1)

    merchants = pd.DataFrame(
        [
            {
                "merchant_id": "M001",
                "store_name": "Sharma Snacks & More",
                "owner_name": "Raj Sharma",
                "city": "Mumbai",
                "default_margin_pct": 25.0,
                "created_at": "2025-01-01 10:00:00",
                "is_active": True,
            },
            {
                "merchant_id": "M002",
                "store_name": "Cafe North",
                "owner_name": "Priya Nair",
                "city": "Delhi",
                "default_margin_pct": 30.0,
                "created_at": "2025-03-12 11:00:00",
                "is_active": True,
            },
            {
                "merchant_id": "M003",
                "store_name": "Closed Kiosk",
                "owner_name": "Amit Das",
                "city": "Pune",
                "default_margin_pct": 20.0,
                "created_at": "2024-08-01 11:00:00",
                "is_active": False,
            },
        ]
    )
    customers = pd.DataFrame(m001_customers + m002_customers)
    transactions = pd.DataFrame(m001_txns + m002_txns)

    success = transactions.loc[transactions["status"] == "SUCCESS"].copy()
    success["txn_datetime"] = pd.to_datetime(success["txn_datetime"])
    segmented = assign_segments(customer_metrics(success))
    expected = segmented[["merchant_id", "customer_id", "segment_label", "reason"]].rename(
        columns={"segment_label": "expected_tags"}
    )

    m001 = segmented.loc[segmented["merchant_id"] == "M001"]
    labels = m001["segment_label"]
    checks = {
        "one_time": int(labels.str.contains("One-time").sum()),
        "loyal": int(labels.str.contains("Loyal").sum()),
        "dormant": int(labels.str.contains("Dormant").sum()),
        "high_value": int(labels.str.contains("High Value").sum()),
        "dormant_high_value": int(
            labels.apply(lambda s: "Dormant" in s.split(",") and "High Value" in s.split(",")).sum()
        ),
        "regular": int(labels.eq("Regular").sum()),
        "failed_or_refunded": int(transactions["status"].isin(["FAILED", "REFUNDED"]).sum()),
        "merchants": int(merchants["merchant_id"].nunique()),
    }
    print(checks)
    required = {
        "one_time": 15,
        "loyal": 15,
        "dormant": 15,
        "high_value": 10,
        "dormant_high_value": 5,
        "regular": 20,
        "failed_or_refunded": 10,
        "merchants": 2,
    }
    for key, minimum in required.items():
        if checks[key] < minimum:
            raise SystemExit(f"{key}={checks[key]} is below {minimum}")

    merchants.to_csv(DATA / "merchants.csv", index=False)
    customers.to_csv(DATA / "customers.csv", index=False)
    transactions.to_csv(DATA / "transactions.csv", index=False)
    expected.to_csv(DATA / "expected_segments.csv", index=False)
    print(f"Wrote {len(customers)} customers and {len(transactions)} transactions")


if __name__ == "__main__":
    main()
