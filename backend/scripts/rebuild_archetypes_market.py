"""Rebuild archetypes per market: 主板5原型 + 创业板4原型.
Separate K-means clustering for each market.
"""
import asyncio, json, sys, logging
from collections import Counter
sys.path.insert(0, r'C:\AI-Agent-Local\Stock\backend')
from app.core.database import async_session_factory
from app.services.archetype_classifier import (
    run_kmeans_clustering, save_centroids_to_db,
    euclidean_distance, N_ARCHETYPES, N_ARCHETYPES_CHINEXT,
)
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger("rebuild")


def _is_chinext(code: str) -> bool:
    c = code.replace('.SZ','').replace('.SH','').replace('.BJ','')
    return c.startswith('300') or c.startswith('301') or c.startswith('688')


async def rebuild():
    # Step 1: Load all fingerprints with market info
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT ts_code, fingerprint_vector FROM stock_fingerprints "
            "WHERE fingerprint_vector IS NOT NULL ORDER BY scan_date DESC"
        ))
        rows = r.fetchall()

    # Dedup by ts_code
    main_fp = {}
    chinext_fp = {}
    for row in rows:
        sym = row[0]
        if sym in main_fp or sym in chinext_fp:
            continue
        vec = row[1]
        if isinstance(vec, str): vec = json.loads(vec)
        if vec and len(vec) >= 11:
            v = [float(x) for x in vec[:15]]  # use all 15 dims
            if _is_chinext(sym):
                chinext_fp[sym] = v
            else:
                main_fp[sym] = v

    logger.info(f"Main board: {len(main_fp)} stocks, ChiNext: {len(chinext_fp)} stocks")

    # Step 2: Run K-means per market
    centroids_all = {}

    # 主板: 5 prototypes
    logger.info("Clustering main board (n=5)...")
    main_centroids = await run_kmeans_clustering(main_fp, N_ARCHETYPES, market="主板")
    centroids_all.update(main_centroids)
    for name in sorted(main_centroids.keys()):
        logger.info(f"  {name}: {len([s for s in main_fp if s])} stocks covered")

    # 创业板: 4 prototypes (fewer stocks)
    logger.info("Clustering ChiNext (n=4)...")
    chinext_centroids = await run_kmeans_clustering(chinext_fp, N_ARCHETYPES_CHINEXT, market="创业板")
    centroids_all.update(chinext_centroids)
    for name in sorted(chinext_centroids.keys()):
        logger.info(f"  {name}: {len([s for s in chinext_fp if s])} stocks covered")

    # Step 3: Classify all stocks
    assignments = {}
    for sym, vec in main_fp.items():
        best = min(main_centroids.keys(), key=lambda n: euclidean_distance(vec, main_centroids[n]))
        assignments[sym] = best
    for sym, vec in chinext_fp.items():
        best = min(chinext_centroids.keys(), key=lambda n: euclidean_distance(vec, chinext_centroids[n]))
        assignments[sym] = best

    # Step 4: Update stock_fingerprints
    async with async_session_factory() as s:
        for sym, arch in assignments.items():
            await s.execute(text(
                "UPDATE stock_fingerprints SET archetype=:a WHERE ts_code=:s"
            ), {"a": arch, "s": sym})
        await s.commit()

    # Step 5: Verify
    counts = Counter(assignments.values())
    logger.info("\nFinal archetype distribution:")
    for name in sorted(counts.keys()):
        logger.info(f"  {name}: {counts[name]} stocks")

    logger.info("\nDone! Run '训练全部原型' to train on separated archetypes.")


if __name__ == "__main__":
    asyncio.run(rebuild())
