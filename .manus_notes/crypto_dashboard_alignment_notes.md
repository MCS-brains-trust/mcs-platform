# Crypto Dashboard Alignment Notes

## User request
Make the website match the live script running daily on the droplet, keep capital fixed at **71,000 USD**, and keep all trade history on the droplet.

## Findings
- The existing crypto page is built around **Bybit CSV import**, normalized trade rows, and holdings snapshots.
- The attached live script writes strategy state to droplet-local files such as:
  - `/root/crypto_dashboard/optimized_trading_state.json`
  - `/root/crypto_dashboard/optimized_strategy_data.json`
  - `/root/crypto_dashboard/optimized_signal_history.json`
  - `/root/crypto_dashboard/paleologo_analytics.json`
- The dashboard should therefore be refocused from exchange-import centric views to a **strategy state / signal history / analytics** view sourced from droplet files.
- Capital should be presented as a fixed **71,000 USD** regardless of the script's historical backtest start value.
- Trade/signal history should remain on the droplet by reading existing JSON/log files rather than requiring new uploads.

## Likely implementation direction
- Add settings for droplet strategy file paths and fixed capital.
- Add file-reading helpers in `core/views.py` for the strategy JSON outputs.
- Replace or substantially redesign `templates/core/crypto_portfolio_dashboard.html` to display:
  - live portfolio value and return versus 71,000 USD
  - current positions / target positions
  - latest signal and rule fired
  - BTC health / all-negative / confidence / re-entry data
  - strategy parameters and performance counters
  - recent signal history from droplet JSON
- Preserve backward compatibility by handling missing keys gracefully.
