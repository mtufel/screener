# OpenSpec Specification: Filter & Paginate Live Daemon Tracked Trades Log

## Invariants & Testable Requirements

### Requirement 1: State, Symbol, and Direction Filtering
- Filtering by `state="COMPLETED_TP"` MUST return only trades with state equal to `COMPLETED_TP`.
- Filtering by `symbol="BTC"` MUST return only BTC trades, regardless of letter case.
- Filtering by `direction="Bullish"` MUST return only Bullish trades.
- When no filters are provided, all active and historical trades MUST be included.

### Requirement 2: Strict Pagination Invariants
- For `total` matching trades and `per_page` items:
  $$\text{pages} = \max(1, \lceil \text{total} / \text{per\_page} \rceil)$$
- Page 1 MUST return items from index `0` to `per_page - 1`.
- Page 2 MUST return items from index `per_page` to `2 * per_page - 1`.
- Requesting a page beyond `pages` MUST return an empty list for `trades` without raising exceptions.

### Requirement 3: Metrics Calculation Over Full Filtered Subset
- In the response payload, `metrics.winrate` and `metrics.trades` MUST be calculated across all matching trades in the filtered dataset, NOT just the current page slice.

### Requirement 4: UI Resilience & State Reset
- Changing any filter dropdown in the dashboard UI MUST reset the pagination page to 1.
- Pagination buttons MUST disable `Prev` on page 1 and disable `Next` on the last page.
