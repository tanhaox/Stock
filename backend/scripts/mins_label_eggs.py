"""标注脚本: 为 mins_train_samples 的蛋期样本标注孵化结果.

标注逻辑:
  - egg_phase_samples 中的股票如果最终出现在 goose_archive (涨幅>100%) → label='hatched'
  - 否则 → label='failed'

运行: python -m scripts.mins_label_eggs
"""
import asyncio, sys, logging
from sqlalchemy import text
sys.path.insert(0, '.')
from app.core.database import async_session_factory

logger = logging.getLogger("mins_label")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


async def main():
    logger.info("=== mins_train_samples 标注 ===")

    async with async_session_factory() as s:
        # 确保 label 列存在
        await s.execute(text("""
            ALTER TABLE mins_train_samples
            ADD COLUMN IF NOT EXISTS label VARCHAR(20) DEFAULT 'pre_goose'
        """))

        # 1. 获取所有蛋期样本
        r = await s.execute(text(
            "SELECT ts_code FROM mins_train_samples"
        ))
        egg_codes = [row[0] for row in r.fetchall()]
        logger.info(f"蛋期样本: {len(egg_codes)} 只")

        if not egg_codes:
            logger.info("无样本可标注")
            return

        # 2. 查 goose_archive 中涨幅>100%的股票 (孵化成功)
        r = await s.execute(text(
            "SELECT DISTINCT ts_code FROM goose_archive WHERE total_gain >= 100"
        ))
        goose_codes = set(row[0] for row in r.fetchall())
        logger.info(f"goose_archive (gain>=100%): {len(goose_codes)} 只")

        # 3. 查 alphaflow_pool 中仍在蛋期的 (gain_from_first_lock < 50%)
        r = await s.execute(text("""
            SELECT DISTINCT ts_code FROM alphaflow_pool
            WHERE strategy_group NOT IN ('老兵锁死')
        """))
        pool_codes = set(row[0] for row in r.fetchall())

        # 4. 标注
        hatched = 0
        failed = 0
        pending = 0
        for code in egg_codes:
            if code in goose_codes:
                label = "hatched"
                hatched += 1
            elif code not in pool_codes:
                # 不在池中且不在goose → 可能已破位/淘汰
                label = "failed"
                failed += 1
            else:
                # 仍在池中 → 暂不标注 (等结果出来)
                label = "pre_goose"
                pending += 1

            await s.execute(text("""
                UPDATE mins_train_samples SET label = :l WHERE ts_code = :c
            """), {"l": label, "c": code})

        await s.commit()

    logger.info(f"标注完成: hatched={hatched}, failed={failed}, pending={pending}")
    logger.info(f"可训练样本: {hatched + failed} (hatched正样本 + failed负样本)")


if __name__ == "__main__":
    asyncio.run(main())
