## Why
Wrap existing Hyperliquid + config-driven Binance/OANDA provider with ccxt for unified live data fetching across exchanges and full backtest/live trading support.

## What Changes
- Add ccxt Exchange adapter wrapper around provider layer
- Keep HyperliquidClient for Hyperliquid-specific logic
- Enable multi-exchange OHLCV via ccxt REST / websocket
- Full live + backtest pipeline: DataProvider.ohlcv -> ccxt -> strategy

## Non-Goals
- Removing HyperliquidClient entirely
- Changing position-sizing engine (f88369c stays)
- Rewriting telegram alert logic

## Scope
Full + live (backtest + live trading). New branch from `develop`.
