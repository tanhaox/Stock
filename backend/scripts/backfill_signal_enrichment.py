"""Phase 56: SQL bulk backfill signal_history enrichment from analysis_scores.details JSONB."""
import asyncio, logging, sys, io, time
from sqlalchemy import text
from app.core.database import async_session_factory

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
logging.getLogger('sqlalchemy.engine').setLevel(logging.ERROR)
logger = logging.getLogger("backfill_enrichment")


async def main():
    t0 = time.time()
    async with async_session_factory() as s:
        # ── Count before ──
        r = await s.execute(text(
            "SELECT COUNT(*), COUNT(CASE WHEN relative_position IS NOT NULL THEN 1 END) "
            "FROM signal_history"))
        total_before, enriched_before = r.fetchone()
        logger.info(f"Before: {total_before} total, {enriched_before} enriched ({enriched_before/max(total_before,1)*100:.0f}%)")

        # ── SQL UPDATE: JOIN analysis_scores, extract JSONB fields ──
        r = await s.execute(text("""
            UPDATE signal_history sh
            SET
                relative_position  = det.relative_position,
                sector_direction   = det.sector_direction,
                sector_lifecycle   = det.sector_lifecycle,
                sector_rank_5d     = (det.sector_rank_5d)::int,
                market_5d          = (det.market_5d)::decimal,
                predicted_return   = (det.predicted_return)::decimal,
                predicted_win_prob = (det.predicted_win_prob)::decimal
            FROM (
                SELECT
                    a.symbol,
                    a.scan_date,
                    a.details->>'relative_position'   AS relative_position,
                    a.details->>'sector_direction'    AS sector_direction,
                    a.details->>'sector_lifecycle'    AS sector_lifecycle,
                    a.details->>'sector_rank_5d'      AS sector_rank_5d,
                    a.details->>'market_5d'           AS market_5d,
                    a.details->>'predicted_return'    AS predicted_return,
                    a.details->>'predicted_win_prob'  AS predicted_win_prob
                FROM analysis_scores a
                WHERE a.details IS NOT NULL
                  AND a.details::text LIKE '%relative_position%'
            ) det
            WHERE sh.symbol = det.symbol
              AND sh.scan_date = det.scan_date
              AND sh.relative_position IS NULL
        """))
        await s.commit()

        # ── Count after ──
        r = await s.execute(text(
            "SELECT COUNT(*), COUNT(CASE WHEN relative_position IS NOT NULL THEN 1 END) "
            "FROM signal_history"))
        total_after, enriched_after = r.fetchone()
        delta = enriched_after - enriched_before
        logger.info(f"After:  {total_after} total, {enriched_after} enriched ({enriched_after/max(total_after,1)*100:.0f}%)")
        logger.info(f"SQL updated {delta} rows")

        # ── Also backfill predicted_return from inner dimension_scores (older format) ──
        r = await s.execute(text("""
            UPDATE signal_history sh
            SET
                predicted_return  = (a.details->>'predicted_return')::decimal,
                predicted_win_prob = (a.details->>'predicted_win_prob')::decimal
            FROM analysis_scores a
            WHERE sh.symbol = a.symbol
              AND sh.scan_date = a.scan_date
              AND sh.predicted_return IS NULL
              AND a.details IS NOT NULL
              AND a.details::text LIKE '%predicted_return%'
        """))
        await s.commit()

        r = await s.execute(text(
            "SELECT COUNT(CASE WHEN predicted_return IS NOT NULL THEN 1 END) FROM signal_history"))
        pred_ret_final = r.scalar()
        logger.info(f"predicted_return enriched: {pred_ret_final}/{total_after} ({pred_ret_final/max(total_after,1)*100:.0f}%)")

    elapsed = time.time() - t0
    logger.info(f"Phase 56 complete in {elapsed:.0f}s")
    return {"before": enriched_before, "after": enriched_after, "delta": delta,
            "pred_ret": pred_ret_final}


if __name__ == "__main__":
    result = asyncio.run(main())
    print(f"\n=== Phase 56 complete ===")
    print(f"relative_position: {result['before']} → {result['after']} (+{result['delta']})")
    print(f"predicted_return: {result['pred_ret']}")
