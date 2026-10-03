from __future__ import annotations

import html


def teaser_card(campaign_name: str, teaser: str) -> str:
    title = html.escape(str(campaign_name))
    discount = html.escape(str(teaser))
    return f"""
    <div style="max-width:340px;border-radius:18px;overflow:hidden;
        background:linear-gradient(160deg,#002970 0%,#00baf2 100%);
        color:#fff;padding:18px 18px 20px;box-shadow:0 10px 24px rgba(0,41,112,.18);">
      <div style="font-size:12px;letter-spacing:.08em;opacity:.9;"><strong>PAYTM</strong></div>
      <div style="margin-top:16px;font-size:22px;font-weight:700;line-height:1.2;">{title}</div>
      <div style="margin-top:10px;font-size:32px;font-weight:800;line-height:1.1;">{discount}</div>
    </div>
    """


def offer_card(store_name: str, offer_text: str, min_bill: float, expiry: str, coupon_code: str) -> str:
    store = html.escape(str(store_name))
    offer = html.escape(str(offer_text))
    expiry_label = html.escape(str(expiry)[:10])
    code = html.escape(str(coupon_code))
    bill = html.escape(f"{float(min_bill):.0f}" if float(min_bill).is_integer() else f"{float(min_bill):.2f}")
    return f"""
    <div style="max-width:340px;border-radius:18px;overflow:hidden;
        background:linear-gradient(160deg,#002970 0%,#00baf2 100%);
        color:#fff;padding:18px 18px 16px;box-shadow:0 10px 24px rgba(0,41,112,.18);">
      <div style="display:flex;justify-content:space-between;align-items:center;font-size:12px;letter-spacing:.08em;opacity:.9;">
        <strong>PAYTM</strong><span>OFFER</span>
      </div>
      <div style="margin-top:14px;font-size:18px;font-weight:700;">{store}</div>
      <div style="margin-top:8px;font-size:22px;font-weight:800;line-height:1.25;">{offer}</div>
      <div style="margin-top:12px;font-size:13px;opacity:.95;">Minimum bill ₹{bill} · Valid till {expiry_label}</div>
      <div style="margin-top:14px;background:#fff;color:#002970;border-radius:10px;padding:10px 12px;font-weight:800;letter-spacing:.04em;">
        {code}
      </div>
    </div>
    """


def chat_bubble(text: str) -> str:
    safe = html.escape(str(text)).replace("\n", "<br>")
    return f"""
    <div style="max-width:460px;background:#ffffff;border:1px solid #e6eef5;border-radius:16px;
        padding:12px 14px;color:#10233f;line-height:1.45;box-shadow:0 4px 12px rgba(16,35,63,.04);">
      {safe}
    </div>
    """
