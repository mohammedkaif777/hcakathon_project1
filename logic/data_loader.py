from __future__ import annotations

from pathlib import Path

import pandas as pd

from logic.constants import DEMO_TODAY, MERCHANT_ID

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RUNTIME_DIR = DATA_DIR / "runtime"

CAMPAIGN_COLUMNS = [
    "campaign_id",
    "merchant_id",
    "store_name",
    "campaign_name",
    "status",
    "offer_type",
    "discount_value",
    "max_discount",
    "min_bill_amount",
    "free_item_name",
    "free_item_cost",
    "expiry_datetime",
    "target_filters_json",
    "campaign_copy",
    "include_poster",
    "campaign_goal",
    "poster_file",
    "created_at",
    "sent_at",
]

RECIPIENT_COLUMNS = [
    "campaign_id",
    "merchant_id",
    "customer_id",
    "coupon_code",
    "conversation_id",
    "delivery_status",
    "notification_seen",
    "redeemed_status",
    "sent_at",
]

MESSAGE_COLUMNS = [
    "message_id",
    "conversation_id",
    "campaign_id",
    "merchant_id",
    "customer_id",
    "message_type",
    "message_text",
    "coupon_code",
    "created_at",
    "include_poster",
    "store_name",
    "campaign_name",
    "offer_text",
    "min_bill_amount",
    "expiry_datetime",
    "poster_file",
]

PAYMENT_COLUMNS = [
    "payment_txn_id",
    "merchant_id",
    "customer_id",
    "gross_amount",
    "coupon_code",
    "discount_applied",
    "final_paid_amount",
    "payment_datetime",
    "status",
]

REDEMPTION_COLUMNS = [
    "redemption_id",
    "campaign_id",
    "coupon_code",
    "customer_id",
    "merchant_id",
    "payment_txn_id",
    "gross_bill_amount",
    "discount_amount",
    "final_paid_amount",
    "redeemed_at",
    "validation_status",
]


def _csv(name: str) -> Path:
    return DATA_DIR / name


def _read(name: str) -> pd.DataFrame:
    return pd.read_csv(_csv(name))


def load_merchants() -> pd.DataFrame:
    df = _read("merchants.csv")
    df["is_active"] = df["is_active"].astype(str).str.lower().eq("true")
    return df


def load_customers() -> pd.DataFrame:
    df = _read("customers.csv")
    df["is_active"] = df["is_active"].astype(str).str.lower().eq("true")
    return df


def load_transactions() -> pd.DataFrame:
    df = _read("transactions.csv")
    df["txn_datetime"] = pd.to_datetime(df["txn_datetime"])
    df["amount"] = df["amount"].astype(float)
    return df


def load_expected_segments() -> pd.DataFrame:
    return _read("expected_segments.csv")


def active_merchant(merchant_id: str = MERCHANT_ID) -> pd.Series:
    merchants = load_merchants()
    row = merchants.loc[merchants["merchant_id"] == merchant_id]
    if row.empty:
        raise KeyError(f"Unknown merchant {merchant_id}")
    return row.iloc[0]


def success_transactions(merchant_id: str = MERCHANT_ID) -> pd.DataFrame:
    txns = load_transactions()
    mask = (txns["merchant_id"] == merchant_id) & (txns["status"] == "SUCCESS")
    return txns.loc[mask].copy()


def _empty(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _runtime_path(name: str) -> Path:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    return RUNTIME_DIR / name


def load_table(name: str, columns: list[str]) -> pd.DataFrame:
    path = _runtime_path(name)
    if not path.exists() or path.stat().st_size == 0:
        return _empty(columns)
    df = pd.read_csv(path)
    for column in columns:
        if column not in df.columns:
            df[column] = pd.NA
    return df[columns]


def save_table(name: str, df: pd.DataFrame, columns: list[str]) -> None:
    path = _runtime_path(name)
    out = df.copy()
    for column in columns:
        if column not in out.columns:
            out[column] = pd.NA
    out[columns].to_csv(path, index=False)


def load_campaigns() -> pd.DataFrame:
    return load_table("campaigns.csv", CAMPAIGN_COLUMNS)


def load_recipients() -> pd.DataFrame:
    return load_table("campaign_recipients.csv", RECIPIENT_COLUMNS)


def load_messages() -> pd.DataFrame:
    return load_table("messages.csv", MESSAGE_COLUMNS)


def load_payments() -> pd.DataFrame:
    return load_table("payments.csv", PAYMENT_COLUMNS)


def load_redemptions() -> pd.DataFrame:
    return load_table("redemptions.csv", REDEMPTION_COLUMNS)


def save_campaigns(df: pd.DataFrame) -> None:
    save_table("campaigns.csv", df, CAMPAIGN_COLUMNS)


def save_recipients(df: pd.DataFrame) -> None:
    save_table("campaign_recipients.csv", df, RECIPIENT_COLUMNS)


def save_messages(df: pd.DataFrame) -> None:
    save_table("messages.csv", df, MESSAGE_COLUMNS)


def save_payments(df: pd.DataFrame) -> None:
    save_table("payments.csv", df, PAYMENT_COLUMNS)


def save_redemptions(df: pd.DataFrame) -> None:
    save_table("redemptions.csv", df, REDEMPTION_COLUMNS)


def demo_today_label() -> str:
    return DEMO_TODAY.strftime("%d %b %Y")
