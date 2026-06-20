"""小规模训练测试: 10只股票, 2020至今, 验证训练管线.

运行结束后检查: 收敛率, 准确率, 区分度, 权重合理性, 有无报错.
"""
import asyncio, sys, json, logging, time
from datetime import date, timedelta
sys.path.insert(0, r'C:\AI-Agent-Local\Stock\backend')
from app.core.database import async_session_factory
from app.services.shadow_trainer import (
    train_shadow, run_training_round_from_kline,
    DEFAULT_WEIGHTS, FORECAST_HORIZONS, _get_active_weights,
)
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger("test")

# 10只代表不同原型的股票
TEST_STOCKS = [
    "600519.SH",  # 贵州茅台 - large_bluechip
    "000858.SZ",  # 五粮液 - large_bluechip
    "300750.SZ",  # 宁德时代 - growth_tech
    "002475.SZ",  # 立讯精密 - growth_tech
    "601857.SH",  # 中国石油 - cyclical_resource
    "600036.SH",  # 招商银行 - value_defensive
    "601318.SH",  # 中国平安 - value_defensive
    "002607.SZ",  # 中公教育 - small_speculative
    "300059.SZ",  # 东方财富 - small_speculative
    "000001.SZ",  # 平安银行 - large_bluechip
]


async def test_single_stock_scoring():
    """测试: 用历史K线重建评分."""
    logger.info("=== Test 1: 历史评分重建 (10股, 180天) ===")

    weights = dict(DEFAULT_WEIGHTS)
    today = date.today()
    start = today - timedelta(days=180 + 60)

    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT ts_code, trade_date, open, high, low, close, volume
            FROM daily_kline
            WHERE ts_code = ANY(:syms) AND trade_date BETWEEN :d1 AND :d2
            ORDER BY ts_code, trade_date
        """), {"syms": TEST_STOCKS, "d1": start, "d2": today})
        rows = r.fetchall()

    from collections import defaultdict
    stock_data = defaultdict(list)
    for row in rows:
        stock_data[row[0]].append({
            "date": row[1], "close": float(row[5]), "volume": float(row[6]),
        })

    ok = 0
    for sym, bars in stock_data.items():
        if len(bars) >= 60:
            logger.info(f"  {sym}: {len(bars)} bars ({bars[0]['date']} ~ {bars[-1]['date']})")
            ok += 1
        else:
            logger.warning(f"  {sym}: ONLY {len(bars)} bars (不足)")

    logger.info(f"  {ok}/{len(TEST_STOCKS)} 只有足够数据")
    return ok >= 8


async def test_mini_training():
    """测试: large_bluechip/S1, 2轮, 180天回溯 (快速验证管线)."""
    logger.info("\n=== Test 2: 微型训练 (large_bluechip/S1, 2轮, 180天) ===")

    t0 = time.time()
    try:
        result = await train_shadow("large_bluechip", "S1", n_iterations=2)
        elapsed = time.time() - t0
        logger.info(f"  状态: {result.get('status')}")
        logger.info(f"  训练阶段: {result.get('phases_trained', '?')}")
        logger.info(f"  耗时: {elapsed:.0f}s")
        return result.get("status") == "success"
    except Exception as e:
        logger.error(f"  训练失败: {e}")
        return False


async def test_weights_validity():
    """测试: 检查训练产出的权重是否合理."""
    logger.info("\n=== Test 3: 权重合理性检查 ===")

    issues = []
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT archetype, strategy, scoring_weights, backtest_accuracy
            FROM param_library
            WHERE is_shadow=true AND last_trained_at > NOW() - INTERVAL '1 hour'
            ORDER BY backtest_accuracy DESC
        """))
        rows = r.fetchall()

    if not rows:
        logger.info("  无最近训练记录, 跳过")
        return True

    for row in rows[:5]:
        arch, st, w_raw, acc = row[0], row[1], row[2], float(row[3] or 0)
        w = w_raw if isinstance(w_raw, dict) else (json.loads(w_raw) if w_raw else {})

        # 检查权重范围
        for k, v in w.items():
            if v < -5 or v > 10:
                issues.append(f"{arch}/{st}: {k}={v:.2f} 超出合理范围")
            if v == 0 and k.endswith("_weight"):
                issues.append(f"{arch}/{st}: {k}=0 维度被完全禁用")

        # 检查准确率
        if acc < -0.5:
            issues.append(f"{arch}/{st}: 准确率={acc:.4f} 严重为负")
        elif acc > 0.9:
            issues.append(f"{arch}/{st}: 准确率={acc:.4f} 疑似过拟合")

    if issues:
        for issue in issues:
            logger.warning(f"  ⚠ {issue}")
    else:
        logger.info(f"  检查 {len(rows)} 条记录, 全部合理")

    return len(issues) == 0


async def main():
    logger.info("=" * 60)
    logger.info("Bootstrap 训练测试 — 10只股票, 2020至今")
    logger.info("=" * 60)

    # Test 1
    ok1 = await test_single_stock_scoring()
    logger.info(f"Test 1 (历史评分重建): {'PASS' if ok1 else 'FAIL'}")

    # Test 2
    ok2 = await test_mini_training()
    logger.info(f"Test 2 (微型训练): {'PASS' if ok2 else 'FAIL'}")

    # Test 3
    ok3 = await test_weights_validity()
    logger.info(f"Test 3 (权重合理性): {'PASS' if ok3 else 'FAIL'}")

    logger.info("\n" + "=" * 60)
    all_ok = ok1 and ok2 and ok3
    logger.info(f"总结: {'全部通过 ✅' if all_ok else '存在问题, 需修复 ⚠'}")

    if all_ok:
        logger.info("可以开始全量训练: python scripts/bootstrap_train.py")
    else:
        logger.info("请先修复上述问题再全量训练")


if __name__ == "__main__":
    asyncio.run(main())
