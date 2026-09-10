# Specification: Market Data Providers

## Requirement 1: Provider Selection & Configuration
1. The system SHALL support selecting a market data provider via `DATA_PROVIDER` environment variable (options: `"binance"`, `"oanda"`, `"hyperliquid"`, default: `"binance"`).
2. The runtime configuration endpoint `/api/extreme/config` SHALL allow viewing and switching the active data provider.

## Requirement 2: Binance Provider (`BinanceProvider`)
1. The `BinanceProvider` SHALL support Binance USDT-M Futures (`https://fapi.binance.com`) and Binance Spot (`https://api.binance.com`).
2. `get_all_mids()` SHALL fetch all market ticker prices in a single bulk request (`/fapi/v1/ticker/price`).
3. `get_candles()` SHALL fetch historical klines (`/fapi/v1/klines`) with pagination support for deep queries (>1000 candles).
4. Symbols SHALL map cleanly (e.g. `"BTC"` -> `"BTCUSDT"`, `"ETH"` -> `"ETHUSDT"`, `"SOL"` -> `"SOLUSDT"`, `"PAXG"` -> `"PAXGUSDT"`, `"GOLD"` -> `"PAXGUSDT"`, `"SILVER"` -> `"XAGUSDT"`).

## Requirement 3: OANDA Provider (`OandaProvider`)
1. The `OandaProvider` SHALL communicate with OANDA v20 REST API (`https://api-fxtrade.oanda.com/v3` or `https://api-fxpractice.oanda.com/v3`).
2. Authentication SHALL use Bearer token from `OANDA_API_KEY` (or `OANDA_ACCESS_TOKEN`) and `OANDA_ACCOUNT_ID`.
3. If OANDA credentials are not set, requests SHALL fail gracefully with clear log warnings.
4. Timeframe mapping:
   - `"1m"` -> `"M1"`, `"5m"` -> `"M5"`, `"15m"` -> `"M15"`, `"1h"` -> `"H1"`, `"4h"` -> `"H4"`, `"1d"` -> `"D"`.
5. Symbol mapping:
   - `"XAU"` / `"GOLD"` -> `"XAU_USD"`
   - `"XAG"` / `"SILVER"` -> `"XAG_USD"`
   - `"OIL"` / `"WTIOIL"` -> `"WTICO_USD"`
   - `"BRENTOIL"` -> `"BCO_USD"`
   - Standard Forex: `"EURUSD"` -> `"EUR_USD"`, `"GBPUSD"` -> `"GBP_USD"`, etc.

## Requirement 4: Unified Invariant
1. All candle objects consumed by Strategy 1 (Standard 4H+LTF), Strategy 2 (Extreme LTF), `HTFFVGCache`, and `ExtremeTradeTracker` SHALL remain invariant regardless of the underlying data provider.
