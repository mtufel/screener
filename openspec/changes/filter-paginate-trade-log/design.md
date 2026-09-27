# OpenSpec Design: Filter & Paginate Live Daemon Tracked Trades Log

## 1. Architectural Approach
Following OOP encapsulation, all filtering, sorting, pagination, and dynamic subset metrics calculation are encapsulated inside `ExtremeTradeTracker` in `extreme_trade_tracker.py`. The FastAPI route in `main.py` serves strictly as an HTTP gateway that receives query parameters and delegates to the tracker.

## 2. Component Design

### A. Tracker Domain Model (`extreme_trade_tracker.py`)
Add `get_filtered_trades()` method to `ExtremeTradeTracker`:
```python
def get_filtered_trades(
    self,
    state: Optional[str] = None,
    symbol: Optional[str] = None,
    direction: Optional[str] = None,
    page: int = 1,
    per_page: int = 20,
) -> Dict[str, Any]:
```
- Aggregates active trades + history records (reverse chronological by entry timestamp / created time).
- Filters by `state`, `symbol` (case-insensitive), `direction`.
- Computes overall filtered counts, wins, losses, win rate, and total pages.
- Slices the paginated subset: `[(page-1)*per_page : page*per_page]`.
- Returns structured dictionary with `trades`, `pagination`, `metrics`, `summary`.

### B. API Route (`main.py`)
`/api/extreme/live-history`:
- Extracts query parameters `state`, `symbol`, `direction`, `page`, `per_page`.
- Calls `extreme_trade_tracker.get_filtered_trades(...)`.
- Returns JSONResponse with status 200.

### C. Web Dashboard UI (`templates/index.html`)
- Clean filter controls: State dropdown, Symbol dropdown (dynamically populated or comprehensive whitelist), and Refresh button.
- Pagination controls: Previous, Next buttons and `Page X / Y` status indicator.
- Automatically resets page to `1` when filters change.
- Correctly updates UI using `data.pagination.pages` and `data.pagination.total`.
