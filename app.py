from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from logic.campaign import (
    build_campaign_row,
    offer_terms,
    send_campaign,
    teaser_discount,
)
from logic.constants import (
    CAMPAIGN_GOALS,
    DEMO_TODAY,
    LARGE_CUSTOMER_ID,
    MERCHANT_ID,
    OFFER_LABELS,
    OFFER_TYPES,
    SEGMENT_PRIORITY,
    SMALL_CUSTOMER_ID,
)
from logic.data_loader import (
    active_merchant,
    demo_today_label,
    load_campaigns,
    load_customers,
    load_messages,
    load_transactions,
    load_payments,
    load_recipients,
    load_redemptions,
    save_campaigns,
    save_messages,
    save_payments,
    save_recipients,
    save_redemptions,
    success_transactions,
)
from logic.metrics import campaign_results, merchant_snapshot
from logic.poster import build_poster_brief, generate_poster_images, image_api_key
from logic.redemption import list_customer_offers, redeem
from logic.segmentation import apply_filters, assign_segments, customer_metrics
from logic.targeting import describe_rule, rule_to_filters
from ui.components import chat_bubble, teaser_card

st.set_page_config(page_title="Paytm Merchant Offer Loop", page_icon="₹", layout="wide")

st.markdown(
    """
    <style>
      .block-container {padding-top: 1.2rem; padding-bottom: 2rem;}
      .paytm-hero {
        background: linear-gradient(100deg, #002970 0%, #00baf2 70%);
        color: white; border-radius: 18px; padding: 22px 24px; margin-bottom: 16px;
      }
      .paytm-hero h1 {color: white; font-size: 1.7rem; margin: 0 0 6px 0;}
      .paytm-hero p {margin: 0; opacity: .92;}
      div[data-testid="stMetric"] {
        background: white; border: 1px solid #e6eef5; border-radius: 14px; padding: 10px 12px;
      }
    </style>
    """,
    unsafe_allow_html=True,
)


def _boot() -> None:
    if st.session_state.get("booted"):
        return
    merchant = active_merchant(MERCHANT_ID)
    st.session_state.store_name = merchant["store_name"]
    st.session_state.margin_pct = float(merchant["default_margin_pct"])
    st.session_state.owner_name = merchant["owner_name"]
    st.session_state.campaigns = load_campaigns()
    st.session_state.recipients = load_recipients()
    st.session_state.messages = load_messages()
    st.session_state.payments = load_payments()
    st.session_state.redemptions = load_redemptions()
    st.session_state.account = None
    st.session_state.booted = True


def _persist() -> None:
    save_campaigns(st.session_state.campaigns)
    save_recipients(st.session_state.recipients)
    save_messages(st.session_state.messages)
    save_payments(st.session_state.payments)
    save_redemptions(st.session_state.redemptions)


def _customers() -> pd.DataFrame:
    txns = success_transactions(MERCHANT_ID)
    tagged = assign_segments(customer_metrics(txns))
    people = load_customers()[["customer_id", "customer_name", "phone_masked"]]
    return tagged.merge(people, on="customer_id", how="left")


def _label(value, fallback: str) -> str:
    if value is None:
        return fallback
    try:
        if pd.isna(value):
            return fallback
    except TypeError:
        pass
    text = str(value).strip()
    return text or fallback


def _inr(value: float) -> str:
    return f"₹{value:,.0f}" if float(value).is_integer() else f"₹{value:,.2f}"


def _logout() -> None:
    st.session_state.account = None
    st.rerun()


def _hero(title: str, subtitle: str) -> None:
    st.markdown(
        f"""
        <div class="paytm-hero">
          <h1>{title}</h1>
          <p>{subtitle}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _customer_blurb(row: pd.Series) -> str:
    return (
        f"{int(row.frequency)} visits · total {_inr(row.total_spend)} · "
        f"average {_inr(row.avg_spend)} · last visit {int(row.recency_days)} days ago"
    )


def render_login(people: pd.DataFrame) -> None:
    _hero("Sign in", f"Demo clock {demo_today_label()}. This is a role picker, not a production login.")
    merchant_col, customer_col = st.columns(2)
    with merchant_col:
        st.subheader("Merchant")
        st.write(f"{st.session_state.owner_name} · {MERCHANT_ID}")
        st.write(st.session_state.store_name)
        st.caption("Dashboard, customer scores, offer rules, and redemption proof.")
        if st.button("Continue as merchant", type="primary", key="login_merchant"):
            st.session_state.account = {"role": "merchant", "id": MERCHANT_ID}
            st.rerun()
    with customer_col:
        st.subheader("Customer")
        st.caption("These two paid Sharma Snacks & More recently, at very different bill sizes.")
        for cid in (SMALL_CUSTOMER_ID, LARGE_CUSTOMER_ID):
            row = people.loc[people["customer_id"] == cid].iloc[0]
            st.markdown(f"**{row.customer_name}** · {cid}")
            st.caption(_customer_blurb(row))
            if st.button(f"Continue as {row.customer_name}", key=f"login_{cid}"):
                st.session_state.account = {"role": "customer", "id": cid, "name": row.customer_name}
                st.rerun()
        with st.expander("Other customers"):
            others = people.loc[~people["customer_id"].isin([SMALL_CUSTOMER_ID, LARGE_CUSTOMER_ID])]
            choice = st.selectbox(
                "Customer",
                others["customer_id"].tolist(),
                format_func=lambda cid: f"{people.loc[people.customer_id == cid, 'customer_name'].iloc[0]} ({cid})",
            )
            if st.button("Continue as selected customer"):
                name = people.loc[people["customer_id"] == choice, "customer_name"].iloc[0]
                st.session_state.account = {"role": "customer", "id": choice, "name": name}
                st.rerun()


def _apply_preset(basis: str, aggregation: str, comparator: str, value: float, within_days: int) -> None:
    st.session_state.basis = basis
    st.session_state.aggregation = aggregation
    st.session_state.comparator = comparator
    st.session_state.threshold = float(value)
    st.session_state.within_days = int(within_days)
    st.rerun()


def _current_rule() -> tuple[dict, str, pd.DataFrame]:
    basis_label = st.session_state.get("basis", "Amount")
    aggregation_label = st.session_state.get("aggregation", "Average")
    comparator_label = st.session_state.get("comparator", "At most")
    basis = "frequency" if basis_label == "Frequency" else "amount"
    aggregation = "sum" if aggregation_label == "Sum" else "avg"
    comparator = "at_least" if comparator_label == "At least" else "at_most"
    value = float(st.session_state.get("threshold", 500.0))
    within_days = int(st.session_state.get("within_days", 45))
    filters = rule_to_filters(basis, aggregation, comparator, value, within_days)
    summary = describe_rule(basis, aggregation, comparator, value, within_days)
    audience = apply_filters(_customers(), filters)
    return filters, summary, audience


def render_dashboard(people: pd.DataFrame) -> None:
    txns = success_transactions(MERCHANT_ID)
    snap = merchant_snapshot(txns)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Customers", snap["customers"])
    c2.metric("Successful payments", snap["transactions"])
    c3.metric("Revenue", _inr(snap["revenue"]))
    c4.metric("Average bill", _inr(snap["average_bill"]))

    counts = (
        people["primary_segment"]
        .value_counts()
        .reindex(SEGMENT_PRIORITY, fill_value=0)
        .rename_axis("segment")
        .reset_index(name="customers")
    )
    chart = px.bar(
        counts,
        x="segment",
        y="customers",
        color="segment",
        color_discrete_sequence=["#002970", "#00baf2", "#5ad1ff", "#8fd3f4", "#c5e8f7"],
    )
    chart.update_layout(showlegend=False, margin=dict(l=8, r=8, t=24, b=8), height=280)
    st.plotly_chart(chart, width="stretch")

    campaigns = st.session_state.campaigns
    sent = campaigns.loc[campaigns["status"] == "SENT"] if not campaigns.empty else campaigns
    if not sent.empty:
        st.subheader("Campaign results")
        labels = {}
        for row in sent.itertuples(index=False):
            title = _label(getattr(row, "campaign_name", None), row.campaign_id)
            labels[row.campaign_id] = f"{title} · {row.campaign_id}"
        campaign_id = st.selectbox("Campaign", list(labels), format_func=lambda cid: labels[cid])
        stats = campaign_results(
            campaign_id,
            st.session_state.recipients,
            st.session_state.redemptions,
            st.session_state.margin_pct,
        )
        m1, m2, m3 = st.columns(3)
        m1.metric("Sent", stats["sent_count"])
        m2.metric("Redeemed", stats["redeemed_count"])
        m3.metric("Redemption rate", f"{stats['redemption_rate'] * 100:.1f}%")
        n1, n2, n3 = st.columns(3)
        n1.metric("Attributed revenue", _inr(stats["campaign_revenue"]))
        n2.metric("Discount cost", _inr(stats["discount_cost"]))
        n3.metric("Estimated profit", _inr(stats["estimated_profit"]))


def render_customers(people: pd.DataFrame) -> None:
    st.subheader("Customers")
    st.caption("Open a customer to see the chat: payments between you, and whether each offer is redeemed.")
    show = people[
        [
            "customer_id",
            "customer_name",
            "frequency",
            "total_spend",
            "avg_spend",
            "recency_days",
            "segment_label",
        ]
    ].rename(columns={"segment_label": "segments"}).reset_index(drop=True)
    picked = st.dataframe(
        show,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="customer_table",
    )
    rows = picked.selection.rows if picked is not None else []
    if not rows:
        return
    person = show.iloc[rows[0]]
    st.subheader(f"Chat with {person.customer_name}")
    _render_chat(person.customer_id)


def render_campaign(people: pd.DataFrame) -> None:
    st.subheader("Who should get this offer")
    st.caption("Frequency is visit count. Sum and average apply only when you target by amount.")
    defaults = {
        "basis": "Amount",
        "aggregation": "Average",
        "comparator": "At most",
        "threshold": 500.0,
        "within_days": 45,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value
    preset_small, preset_large = st.columns(2)
    if preset_small.button("Preset: small recent bills", key="preset_small"):
        _apply_preset("Amount", "Average", "At most", 500, 45)
    if preset_large.button("Preset: large recent spend", key="preset_large"):
        _apply_preset("Amount", "Sum", "At least", 5000, 45)

    basis = st.selectbox("Target by", ["Amount", "Frequency"], key="basis")
    if basis == "Amount":
        st.selectbox("Amount as", ["Sum", "Average"], key="aggregation")
    else:
        st.caption("Visit count is already a total. Sum and average are not used for frequency.")
    st.selectbox("Include customers whose value is", ["At least", "At most"], key="comparator")
    st.number_input("Threshold", min_value=0.0, step=50.0, key="threshold")
    st.number_input("Last payment within days (0 = any time)", min_value=0, step=1, key="within_days")

    filters, summary, audience = _current_rule()
    st.info(summary)
    ids = set(audience["customer_id"])
    small_name = people.loc[people["customer_id"] == SMALL_CUSTOMER_ID, "customer_name"].iloc[0]
    large_name = people.loc[people["customer_id"] == LARGE_CUSTOMER_ID, "customer_name"].iloc[0]
    a1, a2, a3 = st.columns(3)
    a1.metric("Matching customers", len(audience))
    a2.metric(small_name, "Included" if SMALL_CUSTOMER_ID in ids else "Not included")
    a3.metric(large_name, "Included" if LARGE_CUSTOMER_ID in ids else "Not included")
    preview = audience[
        ["customer_id", "customer_name", "frequency", "total_spend", "avg_spend", "recency_days", "segment_label"]
    ].rename(columns={"segment_label": "segments"})
    st.dataframe(preview, width="stretch", hide_index=True)

    st.subheader("Offer")
    st.caption("Customers see the name and the discount. The goal stays on this dashboard.")
    campaign_name = st.text_input("Campaign name", value="Diwali offer", key="campaign_name")
    offer_type = st.selectbox(
        "Offer type",
        OFFER_TYPES,
        format_func=lambda key: OFFER_LABELS.get(key, key),
        key="offer_type",
    )
    discount_value = 0.0
    max_discount = None
    free_item = ""
    free_cost = None
    if offer_type in ("FLAT_CASHBACK", "PERCENT_DISCOUNT"):
        discount_value = st.number_input("Discount value (₹ or %)", min_value=0.0, value=50.0, step=10.0, key="discount_value")
    if offer_type == "PERCENT_DISCOUNT":
        max_discount = st.number_input("Max discount (0 = no cap)", min_value=0.0, value=100.0, step=10.0, key="max_discount")
        if max_discount == 0:
            max_discount = None
    if offer_type == "FREE_ITEM":
        free_item = st.text_input("Free item name", value="Cold Drink", key="free_item")
        free_cost = st.number_input("Free item cost", min_value=0.0, value=20.0, step=1.0, key="free_cost")
    if offer_type == "STOCK_CLEARANCE":
        free_item = st.text_input("Buy 1 get 1 free on", value="Samosa", key="clearance_item")
        free_cost = st.number_input("Value of the free item", min_value=0.0, value=40.0, step=1.0, key="clearance_cost")
        discount_value = free_cost
    min_bill = st.number_input("Minimum bill", min_value=0.0, value=299.0, step=1.0, key="min_bill")
    expiry = st.date_input("Expiry", value=DEMO_TODAY.date() + timedelta(days=14), key="expiry")
    include_poster = st.checkbox("Show as a Paytm card", value=True, key="include_poster")
    goal = st.selectbox("Goal (not shown to customers)", CAMPAIGN_GOALS, key="goal")

    expiry_dt = datetime.combine(expiry, datetime.max.time()).replace(microsecond=0)
    _show_teaser(
        campaign_name.strip() or "Offer",
        offer_type,
        discount_value,
        min_bill,
        expiry_dt,
        max_discount,
        free_item,
        coupon_code=None,
        poster=include_poster,
        image_path=_selected_poster(),
    )
    _render_poster_picker(
        campaign_name.strip() or "Offer",
        offer_type,
        discount_value,
        min_bill,
        expiry_dt,
        max_discount,
        free_item,
    )

    needs_item = offer_type in ("FREE_ITEM", "STOCK_CLEARANCE") and not free_item.strip()
    if st.button(
        "Approve & send",
        type="primary",
        disabled=audience.empty or not campaign_name.strip() or needs_item,
        key="send_campaign",
    ):
        draft = build_campaign_row(
            st.session_state.campaigns,
            MERCHANT_ID,
            st.session_state.store_name,
            offer_type,
            discount_value,
            min_bill,
            expiry_dt,
            filters,
            include_poster,
            goal,
            max_discount=max_discount,
            free_item_name=free_item,
            free_item_cost=free_cost,
            campaign_name=campaign_name.strip(),
            poster_file=_selected_poster(),
        )
        sent, recipients, messages = send_campaign(
            draft,
            audience[["customer_id"]],
            st.session_state.recipients,
            st.session_state.messages,
            sent_at=DEMO_TODAY.replace(hour=14, minute=5, second=0),
        )
        st.session_state.campaigns = pd.concat([st.session_state.campaigns, pd.DataFrame([sent])], ignore_index=True)
        st.session_state.recipients = recipients
        st.session_state.messages = messages
        _persist()
        st.session_state.last_send = f"Sent {sent['campaign_id']} to {len(audience)} customers. Rule: {summary}."
        st.rerun()
    if st.session_state.get("last_send"):
        st.success(st.session_state.last_send)


def render_merchant(people: pd.DataFrame) -> None:
    _hero(
        st.session_state.store_name,
        f"Signed in as {st.session_state.owner_name} · {MERCHANT_ID} · Demo clock {demo_today_label()}",
    )
    page = st.sidebar.radio("Merchant", ["Dashboard", "Customers", "New campaign"], key="merchant_page")
    if st.sidebar.button("Log out", key="logout_merchant"):
        _logout()
    if page == "Dashboard":
        render_dashboard(people)
    elif page == "Customers":
        render_customers(people)
    else:
        render_campaign(people)


def _poster_flag(value) -> bool:
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def _selected_poster() -> str:
    options = st.session_state.get("poster_options") or []
    if not options:
        return ""
    index = int(st.session_state.get("poster_choice", 0))
    index = min(max(index, 0), len(options) - 1)
    return str(options[index])


def _render_poster_picker(name, offer_type, discount_value, min_bill, expiry, max_discount, free_item) -> None:
    st.subheader("Poster")
    st.caption("The offer text is taken from the form above. Your note only sets the look. The key stays in a local secret, not in this page.")
    category = st.text_input("Business category", value="Food & Snacks", key="poster_category")
    applicable = st.text_input("Applies to", value=free_item or "The full menu", key="poster_applicable")
    occasion = st.text_input("Occasion", value=name, key="poster_occasion")
    mood = st.selectbox("Mood", ["Festive & Traditional", "Modern & Sleek", "Energetic", "Minimalist"], key="poster_mood")
    language = st.selectbox("Language", ["English", "Hindi", "Hinglish"], key="poster_language")
    note = st.text_area("Look and feel for the image model", placeholder="Warm Diwali lighting, sweets on a brass plate", key="poster_note")
    if st.button("Generate poster", key="generate_posters"):
        from logic.poster import pollinations_api_key

        if not image_api_key() and not pollinations_api_key():
            st.error("Add GEMINI_API_KEYS or POLLINATIONS_API_KEY in .streamlit/secrets.toml. Do not paste keys into chat.")
        else:
            brief = build_poster_brief(
                st.session_state.store_name,
                name,
                offer_type,
                float(discount_value),
                float(min_bill),
                expiry.strftime("%Y-%m-%d"),
                free_item_name=free_item or None,
                max_discount=max_discount,
                category=category,
                applicable_on=applicable,
                occasion=occasion,
                mood=mood,
                language=language,
                merchant_note=note,
            )
            try:
                paths = generate_poster_images(brief, count=1)
            except Exception as exc:
                st.error(str(exc))
            else:
                st.session_state.poster_options = [str(path) for path in paths]
                st.session_state.poster_choice = 0
                st.rerun()
    options = st.session_state.get("poster_options") or []
    if len(options) == 1:
        st.session_state.poster_choice = 0
        st.image(options[0], caption="Poster")
    elif options:
        st.session_state.poster_choice = st.radio(
            "Use this poster",
            list(range(len(options))),
            format_func=lambda index: f"Option {index + 1}",
            horizontal=True,
            key="poster_choice_radio",
        )
        st.session_state.poster_choice = st.session_state.poster_choice_radio
        columns = st.columns(len(options))
        for index, path in enumerate(options):
            columns[index].image(path, caption=f"Option {index + 1}")


def _show_teaser(name, offer_type, discount_value, min_bill, expiry, max_discount, free_item, coupon_code, poster, image_path=None) -> None:
    if image_path and Path(str(image_path)).is_file():
        st.image(str(image_path), width=320)
    teaser = teaser_discount(offer_type, float(discount_value), free_item or None)
    if poster:
        st.markdown(teaser_card(name, teaser), unsafe_allow_html=True)
    else:
        st.markdown(f"**{name}**")
        st.markdown(f"### {teaser}")
    with st.expander("Terms"):
        for line in offer_terms(
            offer_type,
            float(discount_value),
            float(min_bill),
            expiry,
            max_discount=_clean_number(max_discount),
            free_item_name=free_item or None,
            coupon_code=coupon_code,
        ):
            st.write(line)


def _campaign_row(campaign_id: str):
    campaigns = st.session_state.campaigns
    if campaigns.empty:
        return None
    match = campaigns.loc[campaigns["campaign_id"] == campaign_id]
    if match.empty:
        return None
    return match.iloc[0]


def _offer_saved(customer_id: str) -> tuple[float, pd.DataFrame]:
    redemptions = st.session_state.redemptions
    if redemptions.empty:
        return 0.0, redemptions
    mine = redemptions.loc[
        (redemptions["customer_id"] == customer_id) & (redemptions["validation_status"] == "VALID")
    ].copy()
    if mine.empty:
        return 0.0, mine
    saved = round(float(mine["discount_amount"].sum()), 2)
    return saved, mine


def _redeem_label(coupon_code: str) -> str:
    recipients = st.session_state.recipients
    if recipients.empty:
        return "Not redeemed yet"
    match = recipients.loc[recipients["coupon_code"] == coupon_code, "redeemed_status"]
    if match.empty:
        return "Not redeemed yet"
    if str(match.iloc[0]) == "REDEEMED":
        return "Redeemed"
    return "Not redeemed yet"


def _chat_events(customer_id: str) -> list[dict]:
    events = []
    txns = load_transactions()
    mine = txns.loc[(txns["merchant_id"] == MERCHANT_ID) & (txns["customer_id"] == customer_id)]
    for row in mine.itertuples(index=False):
        events.append(
            {
                "at": pd.Timestamp(row.txn_datetime),
                "kind": "payment",
                "text": f"Paid {_inr(row.amount)} · {row.payment_mode} · {row.status}",
            }
        )
    payments = st.session_state.payments
    if not payments.empty:
        paid = payments.loc[(payments["merchant_id"] == MERCHANT_ID) & (payments["customer_id"] == customer_id)]
        for row in paid.itertuples(index=False):
            events.append(
                {
                    "at": pd.Timestamp(row.payment_datetime),
                    "kind": "payment",
                    "text": (
                        f"Paid {_inr(row.final_paid_amount)} on a {_inr(row.gross_amount)} bill"
                        f" · coupon {row.coupon_code}"
                    ),
                }
            )
    messages = st.session_state.messages
    if not messages.empty:
        thread = messages.loc[messages["customer_id"] == customer_id]
        for row in thread.itertuples(index=False):
            events.append(
                {
                    "at": pd.Timestamp(row.created_at),
                    "kind": "offer",
                    "row": row,
                    "status": _redeem_label(row.coupon_code),
                }
            )
    events.sort(key=lambda item: item["at"])
    return events


def _render_chat(customer_id: str) -> None:
    events = _chat_events(customer_id)
    if not events:
        st.info("No payments or offers yet.")
        return
    for event in events:
        if event["kind"] == "payment":
            st.markdown(chat_bubble(event["text"]), unsafe_allow_html=True)
            st.caption(event["at"].strftime("%d %b %Y"))
            continue
        row = event["row"]
        camp = _campaign_row(row.campaign_id)
        title = row.store_name
        offer_type = "FLAT_CASHBACK"
        discount_value = 0
        max_discount = None
        free_item = ""
        poster = _poster_flag(row.include_poster)
        if camp is not None:
            title = _label(camp.get("campaign_name"), title)
            offer_type = camp["offer_type"]
            discount_value = camp["discount_value"]
            max_discount = camp.get("max_discount")
            free_item = camp.get("free_item_name") or ""
            poster = _poster_flag(camp["include_poster"])
        st.caption(event["status"])
        _show_teaser(
            title,
            offer_type,
            discount_value,
            row.min_bill_amount,
            row.expiry_datetime,
            max_discount,
            free_item,
            row.coupon_code,
            poster,
            image_path=getattr(row, "poster_file", None),
        )
        st.caption(event["at"].strftime("%d %b %Y"))


def render_my_offers(customer_id: str, name: str) -> None:
    saved, past = _offer_saved(customer_id)
    used = 0 if past.empty else len(past)
    if used:
        st.markdown(f"**{_inr(saved)} saved · {used} used**")
        campaigns = st.session_state.campaigns
        with st.expander("Past offers"):
            for row in past.itertuples(index=False):
                title = row.campaign_id
                if not campaigns.empty and "campaign_name" in campaigns.columns:
                    match = campaigns.loc[campaigns["campaign_id"] == row.campaign_id, "campaign_name"]
                    if not match.empty:
                        title = _label(match.iloc[0], row.campaign_id)
                when = str(row.redeemed_at)[:10]
                st.write(f"{title} · {_inr(row.discount_amount)} · {when}")

    st.subheader(f"Chat with {st.session_state.store_name}")
    _render_chat(customer_id)


def render_pay(customer_id: str, name: str) -> None:
    st.subheader(f"Pay {st.session_state.store_name}")
    st.caption(f"Signed in as {name}. Choose one live offer. The bill updates before you pay.")
    if st.session_state.get("last_payment"):
        st.success(st.session_state.last_payment)
    bill = st.number_input("Bill amount", min_value=0.0, value=500.0, step=10.0, key="bill_amount")
    as_of_date = st.date_input("Payment date", value=DEMO_TODAY.date(), key="pay_date")
    offers = list_customer_offers(
        customer_id,
        st.session_state.recipients,
        st.session_state.campaigns,
        bill,
        as_of=datetime.combine(as_of_date, datetime.min.time()),
    )
    offers = [offer for offer in offers if offer["reason"] != "Coupon already redeemed"]
    if not offers:
        st.info("No offers to use.")
        return

    selectable = [offer for offer in offers if offer["selectable"]]
    blocked = [offer for offer in offers if not offer["selectable"]]
    choice = None
    if selectable:
        labels = {
            offer["coupon_code"]: (
                f"{offer.get('campaign_name') or 'Offer'} · "
                f"{teaser_discount(offer['offer_type'], float(offer['discount_value']), offer.get('free_item_name') or None)} · "
                f"pay {_inr(offer['final_paid_amount'])}"
            )
            for offer in selectable
        }
        choice = st.radio("Offers you can use", list(labels), format_func=lambda code: labels[code], key="chosen_offer")
        picked = next(offer for offer in selectable if offer["coupon_code"] == choice)
        st.write(
            f"Bill {_inr(bill)} − discount {_inr(picked['discount_applied'])} = **{_inr(picked['final_paid_amount'])}**"
        )
    else:
        st.warning("None of the offers can be applied to this bill.")
    for offer in blocked:
        st.caption(f"{offer.get('campaign_name') or 'Offer'} · {offer['reason']}")

    if st.button("Pay", type="primary", disabled=choice is None, key="pay_button"):
        result = redeem(
            choice,
            customer_id,
            MERCHANT_ID,
            bill,
            st.session_state.recipients,
            st.session_state.campaigns,
            st.session_state.payments,
            st.session_state.redemptions,
            as_of=datetime.combine(as_of_date, datetime.min.time()),
        )
        if result["ok"]:
            st.session_state.recipients = result["recipients"]
            st.session_state.payments = result["payments"]
            st.session_state.redemptions = result["redemptions"]
            _persist()
            pay = result["payment"]
            st.session_state.last_payment = (
                f"Paid {_inr(pay['final_paid_amount'])} on bill {_inr(pay['gross_amount'])}. "
                f"Coupon {pay['coupon_code']} · campaign {result['redemption']['campaign_id']}."
            )
            st.rerun()
        else:
            st.error(result["reason"])


def _clean_number(value):
    if value is None or value == "":
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        return value
    return float(value)


def render_customer(people: pd.DataFrame) -> None:
    customer_id = st.session_state.account["id"]
    name = st.session_state.account.get("name")
    if not name:
        name = people.loc[people["customer_id"] == customer_id, "customer_name"].iloc[0]
        st.session_state.account["name"] = name
    _hero(name, f"{customer_id} · paying {st.session_state.store_name} · Demo clock {demo_today_label()}")
    page = st.sidebar.radio("Customer", ["Chat", "Pay"], key="customer_page")
    if st.sidebar.button("Log out", key="logout_customer"):
        _logout()
    if page == "Chat":
        render_my_offers(customer_id, name)
    else:
        render_pay(customer_id, name)


_boot()
scored = _customers()
account = st.session_state.account
if not account:
    render_login(scored)
elif account["role"] == "merchant":
    render_merchant(scored)
else:
    render_customer(scored)
