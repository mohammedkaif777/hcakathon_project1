from __future__ import annotations

import pandas as pd

from logic.constants import DEMO_TODAY, SEGMENT_PRIORITY


def customer_metrics(transactions: pd.DataFrame, demo_today=DEMO_TODAY) -> pd.DataFrame:
    """Metrics from SUCCESS transactions only. Caller should pre-filter status."""
    if transactions.empty:
        return pd.DataFrame(
            columns=[
                "merchant_id",
                "customer_id",
                "frequency",
                "total_spend",
                "avg_spend",
                "last_txn_date",
                "recency_days",
            ]
        )

    grouped = (
        transactions.groupby(["merchant_id", "customer_id"], as_index=False)
        .agg(
            frequency=("txn_id", "count"),
            total_spend=("amount", "sum"),
            last_txn_date=("txn_datetime", "max"),
        )
    )
    grouped["avg_spend"] = grouped["total_spend"] / grouped["frequency"]
    last = pd.to_datetime(grouped["last_txn_date"])
    today = pd.Timestamp(demo_today)
    grouped["recency_days"] = (today.normalize() - last.dt.normalize()).dt.days
    grouped["total_spend"] = grouped["total_spend"].round(2)
    grouped["avg_spend"] = grouped["avg_spend"].round(2)
    return grouped


def _order_tags(tags: list[str]) -> list[str]:
    return [tag for tag in SEGMENT_PRIORITY if tag in tags]


def assign_segments(metrics: pd.DataFrame) -> pd.DataFrame:
    """Tag every merchant-customer row. High value is the merchant's 75th percentile."""
    if metrics.empty:
        out = metrics.copy()
        out["segments"] = []
        out["segment_label"] = ""
        out["primary_segment"] = ""
        out["high_value_threshold"] = pd.NA
        return out

    frames = []
    for merchant_id, group in metrics.groupby("merchant_id", sort=False):
        tagged = group.copy()
        threshold = float(tagged["total_spend"].quantile(0.75))
        labels = []
        primary = []
        reasons = []
        for row in tagged.itertuples(index=False):
            tags: list[str] = []
            reason_parts: list[str] = []
            if int(row.frequency) == 1:
                tags.append("One-time")
                reason_parts.append("frequency == 1")
            if int(row.frequency) >= 5:
                tags.append("Loyal")
                reason_parts.append("frequency >= 5")
            if int(row.recency_days) >= 45:
                tags.append("Dormant")
                reason_parts.append("recency_days >= 45")
            if float(row.total_spend) >= threshold:
                tags.append("High Value")
                reason_parts.append("spend in top 25%")
            if not tags:
                tags.append("Regular")
                reason_parts.append("no special segment rule matched")
            ordered = _order_tags(tags)
            labels.append(ordered)
            primary.append(ordered[0])
            reasons.append(" and ".join(reason_parts))
        tagged["segments"] = labels
        tagged["segment_label"] = [",".join(item) for item in labels]
        tagged["primary_segment"] = primary
        tagged["reason"] = reasons
        tagged["high_value_threshold"] = round(threshold, 2)
        frames.append(tagged)

    return pd.concat(frames, ignore_index=True)


def apply_filters(segmented: pd.DataFrame, filters: dict | None) -> pd.DataFrame:
    filters = filters or {}
    out = segmented.copy()

    def _bound(column: str, lower_key: str, upper_key: str) -> None:
        nonlocal out
        lower = filters.get(lower_key)
        upper = filters.get(upper_key)
        if lower is not None and lower != "":
            out = out[out[column] >= float(lower)]
        if upper is not None and upper != "":
            out = out[out[column] <= float(upper)]

    _bound("frequency", "min_frequency", "max_frequency")
    _bound("total_spend", "min_total_spend", "max_total_spend")
    _bound("avg_spend", "min_avg_spend", "max_avg_spend")
    _bound("recency_days", "min_recency_days", "max_recency_days")

    selected = filters.get("segment") or filters.get("segments") or []
    if isinstance(selected, str):
        selected = [selected] if selected else []
    if selected:
        wanted = set(selected)
        out = out[out["segments"].apply(lambda tags: bool(wanted.intersection(tags)))]

    return out.reset_index(drop=True)
