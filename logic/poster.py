"""Build a locked offer brief and ask an image model for poster options."""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

import requests

from logic.campaign import teaser_discount
from logic.data_loader import RUNTIME_DIR

POSTER_DIR = RUNTIME_DIR / "posters"


def headline_for(offer_type: str, discount_value: float, free_item_name: str | None, max_discount: float | None) -> str:
    if offer_type == "STOCK_CLEARANCE":
        return "BUY 1 GET 1 FREE"
    if offer_type == "PERCENT_DISCOUNT":
        cap = f" UP TO ₹{int(max_discount)}" if max_discount else ""
        number = int(discount_value) if float(discount_value).is_integer() else discount_value
        return f"{number}% OFF{cap}"
    if offer_type == "FLAT_CASHBACK":
        number = int(discount_value) if float(discount_value).is_integer() else discount_value
        return f"FLAT ₹{number} CASHBACK"
    return teaser_discount(offer_type, discount_value, free_item_name).replace("*", "").upper()


def build_poster_brief(
    store_name: str,
    campaign_name: str,
    offer_type: str,
    discount_value: float,
    min_bill: float,
    expiry_iso: str,
    free_item_name: str | None = None,
    max_discount: float | None = None,
    category: str = "Food & Snacks",
    applicable_on: str = "",
    occasion: str = "",
    mood: str = "Festive & Traditional",
    language: str = "English",
    call_to_action: str = "Scan QR to Pay via Paytm",
    merchant_note: str = "",
    phone: str = "",
    address: str = "",
) -> dict:
    """Offer numbers come only from the campaign form. The note cannot replace them."""
    headline = headline_for(offer_type, discount_value, free_item_name, max_discount)
    item = (free_item_name or "").strip()
    if offer_type == "STOCK_CLEARANCE" and item:
        applicable = applicable_on.strip() or item
    else:
        applicable = applicable_on.strip() or "This store"
    bill = int(min_bill) if float(min_bill).is_integer() else min_bill
    terms = f"Minimum bill ₹{bill}. Valid till {expiry_iso[:10]}. T&C apply."
    if item and offer_type == "STOCK_CLEARANCE":
        terms = f"Buy 1 get 1 free on {item}. " + terms
    return {
        "merchant_profile": {
            "store_name": store_name,
            "business_category": category,
            "co_branding_partner": "Paytm",
            "logo_url": "",
            "contact_details": {
                "phone": phone,
                "address": address,
                "website": "",
                "social_handle": "",
            },
        },
        "offer_details": {
            "headline_offer": headline,
            "applicable_on": applicable,
            "validity": {
                "end_date": expiry_iso[:10],
                "display_text": f"Valid till {expiry_iso[:10]}",
            },
            "promo_code": "",
            "terms": terms,
        },
        "theme_and_context": {
            "occasion_or_campaign": occasion.strip() or campaign_name,
            "primary_industry_elements": _props(category),
            "brand_colors": {"primary_color": "#002970", "secondary_color": "#00BAF2"},
            "mood_and_style": mood,
            "merchant_note": merchant_note.strip(),
        },
        "ad_layout_and_output": {
            "aspect_ratio": "1:1",
            "target_platform": "WhatsApp Status",
            "language": language,
            "call_to_action": call_to_action,
        },
    }


def poster_prompt(brief: dict) -> str:
    offer = brief["offer_details"]
    return (
        "Design a square in-store offer poster. Render the headline, store name, validity, "
        "and terms exactly as written in offer_details. Do not invent a different discount, "
        "date, or minimum bill.\n"
        + json.dumps(brief, ensure_ascii=False)
        + f"\nExact headline: {offer['headline_offer']}\nExact terms: {offer['terms']}"
    )


def image_api_key() -> str:
    keys = image_api_keys()
    return keys[0] if keys else ""


def image_api_keys() -> list[str]:
    found: list[str] = []
    for name in ("GEMINI_API_KEYS", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        raw = os.environ.get(name, "").strip()
        found.extend(_split_keys(raw))
    secret_path = Path(__file__).resolve().parents[1] / ".streamlit" / "secrets.toml"
    if secret_path.is_file():
        try:
            import tomllib

            data = tomllib.loads(secret_path.read_text(encoding="utf-8"))
            for name in ("GEMINI_API_KEYS", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
                found.extend(_split_keys(data.get(name, "")))
        except Exception:
            pass
    deduped = []
    for key in found:
        if key and key not in deduped:
            deduped.append(key)
    return deduped


def ollama_settings() -> tuple[str, str]:
    host = os.environ.get("OLLAMA_HOST", "").strip().rstrip("/")
    model = os.environ.get("OLLAMA_IMAGE_MODEL", "").strip()
    secret_path = Path(__file__).resolve().parents[1] / ".streamlit" / "secrets.toml"
    if secret_path.is_file():
        try:
            import tomllib

            data = tomllib.loads(secret_path.read_text(encoding="utf-8"))
            host = host or str(data.get("OLLAMA_HOST", "")).strip().rstrip("/")
            model = model or str(data.get("OLLAMA_IMAGE_MODEL", "")).strip()
        except Exception:
            pass
    return host, model or "x/flux2-klein:4b"


def pollinations_api_key() -> str:
    key = os.environ.get("POLLINATIONS_API_KEY", "").strip().strip('"').strip("'")
    if key:
        return key
    secret_path = Path(__file__).resolve().parents[1] / ".streamlit" / "secrets.toml"
    if secret_path.is_file():
        try:
            import tomllib

            data = tomllib.loads(secret_path.read_text(encoding="utf-8"))
            return str(data.get("POLLINATIONS_API_KEY", "")).strip().strip('"').strip("'")
        except Exception:
            return ""
    return ""


def generate_poster_images(brief: dict, count: int = 1) -> list[Path]:
    keys = image_api_keys()
    backup = pollinations_api_key()
    if not keys and not backup:
        raise RuntimeError("Add GEMINI_API_KEYS or POLLINATIONS_API_KEY in .streamlit/secrets.toml.")
    POSTER_DIR.mkdir(parents=True, exist_ok=True)
    prompt = poster_prompt(brief)
    paths = []
    for _ in range(count):
        encoded, notes = _gemini_image(prompt, keys)
        if not encoded and backup:
            encoded = _pollinations_image(prompt, backup)
        if not encoded:
            detail = "\n".join(notes) if notes else "No image was returned."
            raise RuntimeError("Gemini and Pollinations both failed.\n" + detail)
        path = POSTER_DIR / f"option-{len(list(POSTER_DIR.glob('option-*'))) + 1}.png"
        path.write_bytes(base64.b64decode(encoded))
        paths.append(path)
    return paths


def _gemini_image(prompt: str, keys: list[str]) -> tuple[str, list[str]]:
    notes = []
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent"
    for index, key in enumerate(keys, start=1):
        response = requests.post(
            url,
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseModalities": ["IMAGE"]},
            },
            timeout=120,
        )
        if response.status_code == 429:
            notes.append(f"Gemini key {index}: {_cooldown_text(response)}")
            continue
        if response.status_code >= 400:
            detail = response.text.replace(key, "[key]")[:180]
            notes.append(f"Gemini key {index}: HTTP {response.status_code} {detail}")
            continue
        encoded = _image_bytes(response.json())
        if encoded:
            return encoded, notes
        notes.append(f"Gemini key {index}: no image in the response.")
    return "", notes


def _pollinations_image(prompt: str, key: str) -> str:
    response = requests.post(
        "https://gen.pollinations.ai/v1/images/generations",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": "black-forest-labs/flux.2-klein-4b",
            "prompt": prompt,
            "size": "1024x1024",
            "response_format": "b64_json",
            "n": 1,
        },
        timeout=180,
    )
    if response.status_code >= 400:
        detail = response.text.replace(key, "[key]")[:300]
        raise RuntimeError(f"Pollinations returned {response.status_code}: {detail}")
    payload = response.json()
    data = (payload.get("data") or [{}])[0]
    encoded = data.get("b64_json") or ""
    if encoded:
        return encoded
    url = data.get("url") or ""
    if not url:
        raise RuntimeError("Pollinations did not return an image.")
    image = requests.get(url, timeout=60)
    image.raise_for_status()
    return base64.b64encode(image.content).decode("ascii")


def _ollama_posters(brief: dict, count: int) -> list[Path]:
    host, model = ollama_settings()
    POSTER_DIR.mkdir(parents=True, exist_ok=True)
    prompt = poster_prompt(brief)
    paths = []
    for _ in range(count):
        try:
            response = requests.post(
                f"{host}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False},
                timeout=180,
            )
        except requests.RequestException as exc:
            raise RuntimeError(
                "Could not reach the Ollama machine. It must be running and reachable from this computer at OLLAMA_HOST."
            ) from exc
        if response.status_code >= 400:
            raise RuntimeError(f"Ollama returned {response.status_code}: {response.text[:300]}")
        encoded = ""
        try:
            encoded = str(response.json().get("image") or "")
        except Exception:
            encoded = ""
        if not encoded:
            raise RuntimeError("Ollama did not return an image. Check that OLLAMA_IMAGE_MODEL is an image model your friend has pulled.")
        path = POSTER_DIR / f"option-{len(list(POSTER_DIR.glob('option-*'))) + 1}.png"
        path.write_bytes(base64.b64decode(encoded))
        paths.append(path)
    return paths


def _split_keys(raw) -> list[str]:
    if isinstance(raw, (list, tuple)):
        return [str(item).strip().strip('"').strip("'") for item in raw if str(item).strip()]
    text = str(raw or "").strip()
    if not text:
        return []
    return [part.strip().strip('"').strip("'") for part in re.split(r"[\n,]", text) if part.strip()]


def _cooldown_text(response: requests.Response) -> str:
    quota = _quota_note(response)
    seconds = _retry_seconds(response)
    if seconds is None:
        wait = "Google did not send a cooldown time"
    else:
        wait = f"retry in {_format_wait(seconds)}"
    if quota:
        return f"{quota}. {wait}."
    return f"quota refused. {wait}."


def _quota_note(response: requests.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        return ""
    details = (payload.get("error") or {}).get("details") or []
    notes = []
    for detail in details:
        for violation in detail.get("violations") or []:
            metric = str(violation.get("quotaId") or violation.get("quotaMetric") or "image quota")
            limit = violation.get("quotaValue")
            if limit is None:
                notes.append(metric)
            else:
                notes.append(f"{metric} limit {limit}")
    return "; ".join(notes)


def _retry_seconds(response: requests.Response) -> float | None:
    header = response.headers.get("Retry-After", "").strip()
    if header.isdigit():
        return float(header)
    try:
        payload = response.json()
    except Exception:
        return None
    details = (payload.get("error") or {}).get("details") or []
    for detail in details:
        delay = str(detail.get("retryDelay") or "")
        match = re.fullmatch(r"(\d+(?:\.\d+)?)s", delay.strip())
        if match:
            return float(match.group(1))
    return None


def _format_wait(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    minutes, secs = divmod(total, 60)
    if minutes and secs:
        return f"{minutes} min {secs} sec"
    if minutes:
        return f"{minutes} min"
    return f"{secs} sec"


def _image_bytes(payload: dict) -> str:
    for candidate in payload.get("candidates") or []:
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data") or {}
            encoded = inline.get("data")
            if encoded:
                return encoded
    return ""


def _props(category: str) -> list[str]:
    text = category.lower()
    if "sweet" in text or "snack" in text or "food" in text:
        return ["Diyas", "Snacks counter", "Festive packaging"]
    if "salon" in text or "beauty" in text:
        return ["Hair dryer", "Scissors", "Styling chair"]
    if "gym" in text or "fitness" in text:
        return ["Dumbbells", "Training floor", "Water bottle"]
    return ["Storefront", "Counter", "Shopping bag"]
