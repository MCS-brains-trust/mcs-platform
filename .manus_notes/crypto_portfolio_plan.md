# Crypto Portfolio Integration Notes

## Uploaded CSV schema
The uploaded Bybit CSV contains these columns:
- Spot Pairs
- Order Type
- Direction
- feeCoin
- ExecFeeV2
- Filled Value
- Filled Price
- Filled Quantity
- Fees
- Transaction ID
- Order No.
- Timestamp (UTC)

Initial sample indicates BTCUSDT spot trades with BUY rows charging fees in BTC and SELL rows charging fees in USDT.

## Repository integration points
- Django monolith in `core` app.
- Global sidebar navigation in `templates/base.html`.
- Dashboard view in `core/views.py` and template `templates/core/dashboard.html`.
- URL routing in `core/urls.py` and project include in `config/urls.py`.
- Existing conventions favour adding models/forms/views/templates within `core`.
- Existing import-oriented and tracking patterns use `FileField`, timestamp metadata, and `ActivityLog` entries.

## Proposed implementation direction
- Add persistent models for portfolio, imported trade batches, individual trades, and cached performance snapshots.
- Add CSV upload/import view to store the provided Bybit history under a new portfolio section.
- Add a portfolio dashboard page showing holdings, cost basis, realised P&L, current price, unrealised P&L, and total return.
- Fetch live market prices from a public API on page load or via a lightweight backend helper.
- Link the portfolio page from the main sidebar and keep the uploaded CSV in media-backed storage for reprocessing/audit.
