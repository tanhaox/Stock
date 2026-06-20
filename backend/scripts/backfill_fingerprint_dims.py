"""Backfill stock_fingerprints with 15-dim vectors (currently 11-dim in DB).

Rebuilds fingerprints for all stocks and updates dim_12 through dim_15.
"""
import asyncio, sys, json, logging
sys.path.insert(0, r'C:\AI-Agent-Local\Stock\backend')
from app.core.database import async_session_factory
from app.services.fingerprint_builder import build_fingerprints
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger("backfill")

BATCH_SIZE = 200


async def backfill():
    # Get all unique symbols from stock_fingerprints
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT DISTINCT ts_code FROM stock_fingerprints ORDER BY ts_code"
        ))
        all_symbols = [row[0] for row in r.fetchall()]

    logger.info(f"Total symbols to backfill: {len(all_symbols)}")

    updated = 0
    for i in range(0, len(all_symbols), BATCH_SIZE):
        batch = all_symbols[i:i + BATCH_SIZE]
        logger.info(f"Batch {i//BATCH_SIZE + 1}: {len(batch)} symbols ({i+1}-{min(i+BATCH_SIZE, len(all_symbols))})")

        try:
            fingerprints = await build_fingerprints(batch)
        except Exception as e:
            logger.error(f"build_fingerprints failed for batch: {e}")
            continue

        async with async_session_factory() as s:
            for sym, vec in fingerprints.items():
                if len(vec) < 15:
                    continue
                await s.execute(text("""
                    UPDATE stock_fingerprints SET
                        fingerprint_vector = CAST(:v AS jsonb),
                        dim_ma_trend = :d12,
                        dim_volatility_structure = :d13,
                        dim_price_range = :d14,
                        dim_pattern = :d15
                    WHERE ts_code = :sym
                """), {
                    "sym": sym,
                    "v": json.dumps(vec),
                    "d12": vec[11], "d13": vec[12],
                    "d14": vec[13], "d15": vec[14],
                })
                updated += 1
            await s.commit()

        logger.info(f"  Updated {len(fingerprints)} in this batch, total: {updated}")

    logger.info(f"Backfill complete: {updated} fingerprints updated")


if __name__ == "__main__":
    asyncio.run(backfill())
