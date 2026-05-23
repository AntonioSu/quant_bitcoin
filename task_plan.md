# Task Plan: Support/Resistance Indicator

## Goal
Add recent BTC support/resistance levels to the indicator panel and draw multiple levels on the K-line chart.

## Phases
- [complete] Inspect API/frontend chart wiring.
- [complete] Add backend support/resistance calculation and expose API fields.
- [complete] Add indicator panel display.
- [complete] Draw support/resistance lines on chart.
- [complete] Run focused checks.

## Decisions
- Start with analysis/display only; do not use levels to trigger trades.
- Reuse existing 4h K-line data and current frontend structure.
- Backend panel values use the shared 4h market data; chart lines are recalculated from the currently loaded chart interval.

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
