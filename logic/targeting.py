from __future__ import annotations


def rule_to_filters(
    basis: str,
    aggregation: str,
    comparator: str,
    value: float,
    within_days: int | None,
) -> dict:
    """One merchant rule: frequency, or amount as sum/average, then at least/at most a value."""
    filters: dict = {}
    if within_days not in (None, 0, ""):
        filters["max_recency_days"] = int(within_days)

    if basis == "frequency":
        key = "min_frequency" if comparator == "at_least" else "max_frequency"
        filters[key] = int(value)
        return filters

    if aggregation == "sum":
        key = "min_total_spend" if comparator == "at_least" else "max_total_spend"
    else:
        key = "min_avg_spend" if comparator == "at_least" else "max_avg_spend"
    filters[key] = float(value)
    return filters


def describe_rule(
    basis: str,
    aggregation: str,
    comparator: str,
    value: float,
    within_days: int | None,
) -> str:
    phrase = "at least" if comparator == "at_least" else "at most"
    number = int(value) if float(value).is_integer() else value
    if basis == "frequency":
        metric = f"visit count {phrase} {number}"
    elif aggregation == "sum":
        metric = f"total spend {phrase} ₹{number}"
    else:
        metric = f"average bill {phrase} ₹{number}"
    if within_days not in (None, 0, ""):
        return f"{metric}, last payment within {int(within_days)} days"
    return metric
