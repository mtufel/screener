"""
Live verification script for Hyperliquid API and screener execution.
"""

import asyncio
import logging
from hyperliquid_client import HyperliquidClient
from strategy import run_screener

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("live-verify")


async def main():
    client = HyperliquidClient()
    try:
        logger.info("1. Testing Hyperliquid get_universe()...")
        universe = await client.get_universe()
        logger.info("Found %d coins in Hyperliquid universe: %s...", len(universe), universe[:5])
        assert len(universe) > 0, "Universe should not be empty"

        logger.info("2. Testing Hyperliquid get_all_mids()...")
        all_mids = await client.get_all_mids()
        logger.info("Fetched %d mid prices. Sample (BTC): %s", len(all_mids), all_mids.get("BTC"))
        assert "BTC" in all_mids, "BTC should be in allMids"

        logger.info("3. Testing Hyperliquid get_last_n_candles('BTC', '4h', 10)...")
        candles_4h = await client.get_last_n_candles("BTC", "4h", 10)
        logger.info("Fetched %d 4h candles for BTC. Latest close: %s", len(candles_4h), candles_4h[-1]["c"])
        assert len(candles_4h) > 0, "4h candles should not be empty"

        logger.info("4. Testing Hyperliquid get_last_n_candles('BTC', '15m', 10)...")
        candles_15m = await client.get_last_n_candles("BTC", "15m", 10)
        logger.info("Fetched %d 15m candles for BTC. Latest close: %s", len(candles_15m), candles_15m[-1]["c"])
        assert len(candles_15m) > 0, "15m candles should not be empty"

        logger.info("5. Testing run_screener() on top 10 universe coins...")
        # Test on a small subset of 10 coins for fast live test
        test_universe = universe[:15]
        logger.info("Scanning subset of universe: %s", test_universe)
        setups = await run_screener(top_n=5, client=client)
        logger.info("Screener execution complete. Qualified setups found: %d", len(setups))
        for s in setups:
            logger.info("-> Setup: %s [%s] Price=%.2f SL=%.2f Score=%.4f", s.symbol, s.direction, s.current_price, s.sl_ref, s.score)

        logger.info("ALL LIVE VERIFICATION TESTS PASSED SUCCESSFULLY!")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
