# Paytm Merchant Offer Loop

Mock merchant and customer app for the closed loop in `PRD.md`.

Sign in as the merchant (`M001`) or as a customer. The merchant sets one targeting rule (frequency, or amount as sum or average, then at least or at most a value), approves the offer, and sends it. The customer sees those offers and picks one at pseudo payment. The merchant dashboard then shows the coupon, campaign id, and adjusted amount.

Demo customers who both paid recently: **Aarav Mehta (`C001`)**, small bills, and **Chirag Mehta (`C061`)**, large bills. The demo clock is **1 Feb 2026**. Segment tags from the PRD stay on the customer list. A campaign uses the merchant's threshold, not those tags.

## Run

```bash
py -3.11 -m pip install -r requirements.txt
py -3.11 scripts/generate_mock_data.py
py -3.11 -m streamlit run app.py
```

## Test

```bash
py -3.11 -m pytest
```

Campaigns, chats, payments, and redemptions are written to `data/runtime/` and can be cleared from the sidebar.

Poster images use Gemini keys stored only on this machine. The first key is used until its quota returns 429, then the next key is tried. A successful key stops the search.

```toml
# .streamlit/secrets.toml
GEMINI_API_KEYS = ["your-key-1", "your-key-2"]
```

Do not paste that key into chat or commit the file. `.streamlit/secrets.toml` is gitignored.
