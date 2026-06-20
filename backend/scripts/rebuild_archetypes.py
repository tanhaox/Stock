"""Rebuild K-means centroids and reclassify all existing fingerprints.

Fixes the archetype naming mismatch: uses 5 strategy names consistently.
"""
import asyncio, json, sys, logging
sys.path.insert(0, r'C:\AI-Agent-Local\Stock\backend')
from app.core.database import async_session_factory
from app.services.archetype_classifier import (
    run_kmeans_clustering, save_centroids_to_db, load_centroids_from_db,
    euclidean_distance, N_ARCHETYPES,
)
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rebuild")

ARCHETYPE_NAMES = [
    "large_bluechip", "growth_tech", "cyclical_resource",
    "value_defensive", "small_speculative",
]


async def rebuild():
    # Step 1: Load all existing fingerprints
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT ts_code, fingerprint_vector FROM stock_fingerprints "
            "WHERE fingerprint_vector IS NOT NULL ORDER BY scan_date DESC"
        ))
        rows = r.fetchall()

    print(f"Loaded {len(rows)} fingerprint rows")

    # Dedup by ts_code (keep latest scan_date)
    fingerprints = {}
    for row in rows:
        sym = row[0]
        if sym in fingerprints:
            continue  # already got latest
        vec = row[1]
        if isinstance(vec, str):
            vec = json.loads(vec)
        if vec and len(vec) >= 11:
            fingerprints[sym] = [float(v) for v in vec[:11]]

    print(f"Unique stocks: {len(fingerprints)}")

    if len(fingerprints) < N_ARCHETYPES:
        print(f"ERROR: Need at least {N_ARCHETYPES} fingerprints")
        return

    # Step 2: Run K-means clustering
    print(f"Running K-means (n={N_ARCHETYPES})...")
    centroids = await run_kmeans_clustering(fingerprints, N_ARCHETYPES)
    print(f"Clusters: {list(centroids.keys())}")

    # Step 3: Reclassify all stocks
    assignments = {}
    for sym, vec in fingerprints.items():
        best_name = min(centroids.keys(),
                        key=lambda n: euclidean_distance(vec, centroids[n]))
        assignments[sym] = best_name

    # Count
    from collections import Counter
    counts = Counter(assignments.values())
    print(f"\nNew archetype distribution:")
    for name in ARCHETYPE_NAMES:
        print(f"  {name}: {counts.get(name, 0)}")

    # Step 4: Update stock_fingerprints.archetype
    async with async_session_factory() as s:
        for sym, arch in assignments.items():
            await s.execute(text(
                "UPDATE stock_fingerprints SET archetype=:a WHERE ts_code=:s"
            ), {"a": arch, "s": sym})
        await s.commit()
    print(f"\nUpdated {len(assignments)} stock_fingerprints.archetype records")

    # Step 5: Verify
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT archetype, COUNT(*) FROM stock_fingerprints GROUP BY archetype"
        ))
        print("\nFinal verification:")
        for row in r.fetchall():
            print(f"  {row[0]}: {row[1]} rows")

    print("\nDone! Archetype system rebuilt.")


asyncio.run(rebuild())
