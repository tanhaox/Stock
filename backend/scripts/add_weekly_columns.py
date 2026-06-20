"""方案 B 迁移脚本 — 为 scan_results 表添加周线信号字段.

用法:
    cd C:\AI-Agent-Local\Stock\backend
    python scripts\add_weekly_columns.py
"""
import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from sqlalchemy import text
from app.core.database import async_session_factory


async def migrate():
    """为 scan_results 表添加 resonance_type / weekly_has_buy / weekly_tg_momentum 列."""
    columns = [
        ("resonance_type", "VARCHAR(20)"),
        ("weekly_has_buy", "BOOLEAN"),
        ("weekly_tg_momentum", "DOUBLE PRECISION"),
    ]

    async with async_session_factory() as s:
        for col_name, col_type in columns:
            added = False
            try:
                await s.execute(text(
                    f"ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
                ))
                await s.commit()
                added = True
            except Exception:
                await s.rollback()
                # PostgreSQL < 9.6 doesn't support IF NOT EXISTS for ADD COLUMN
                try:
                    await s.execute(text(f"""
                        DO $$
                        BEGIN
                            IF NOT EXISTS (
                                SELECT 1 FROM information_schema.columns
                                WHERE table_name='scan_results' AND column_name='{col_name}'
                            ) THEN
                                ALTER TABLE scan_results ADD COLUMN {col_name} {col_type};
                            END IF;
                        END $$;
                    """))
                    await s.commit()
                    added = True
                except Exception as e2:
                    await s.rollback()
                    print(f"  [SKIP] {col_name}: {e2}")

            if added:
                print(f"  [OK] {col_name} {col_type}")

    # Verify
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name='scan_results'
              AND column_name IN ('resonance_type','weekly_has_buy','weekly_tg_momentum')
            ORDER BY column_name
        """))
        existing = {row[0]: row[1] for row in r.fetchall()}
        print(f"\n  Verified: {len(existing)}/3 weekly columns exist")
        for col_name, _ in columns:
            status = "[OK]" if col_name in existing else "[MISSING]"
            print(f"    {status} {col_name}: {existing.get(col_name, 'N/A')}")

    if len(existing) == 3:
        print("\n  All columns present. Weekly scan ready.")
    else:
        print(f"\n  WARNING: Only {len(existing)}/3 columns exist. Check DB permissions.")


if __name__ == "__main__":
    asyncio.run(migrate())
    print("Migration complete.")
