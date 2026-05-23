# Findings: Support/Resistance Indicator

## Notes
- Backend already centralizes 4h K-line data in `core/market_data.py`.
- API indicator response is assembled in `server/api.py`.
- Existing indicators are implemented as standalone modules under `indicators/`.
- Frontend indicator cards are rendered in `web/js/indicators.js` from `/api/indicators`.
- The K-line chart already fetches OHLC data in `web/js/charts.js`; support/resistance chart lines can be computed from that data without a new endpoint.
- Global chart series variables live in `web/js/config.js`.
