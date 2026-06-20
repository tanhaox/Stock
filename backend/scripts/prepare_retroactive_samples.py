"""Phase 71 — 历史日线采样: 从 daily_kline 构建 2022-2025 训练样本.

每天随机抽 20 只股票, 计算 77 维特征 + 真实 T+5 超额收益标签.
特征标记 (_is_historical/_has_toplists/_has_news) 告知模型部分特征缺失.
"""
import asyncio, logging, sys, os, random
import numpy as np
from app.utils.numpy_utils import sanitize_array, sanitize_label_array
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.database import async_session_factory
from app.services.predictive_features import (
    FEAT_NAMES, FEAT_NAMES_HIST,
    _build_features_from_arrays, _preload_klines_batch, _KLINES_BATCH,
    _preload_sector_features, _preload_toplist, _toplist_cache,
    _preload_market_closes, _market_closes, _excess_return,
    _preload_news, _news_cache, _preload_weekly_features, _weekly_cache,
)

logger = logging.getLogger("retroactive_samples")


async def sample_historical(session_or_none=None, start: str = '2022-01-01',
                            end: str = '2025-12-31', per_day: int = 50) -> tuple:
    """从 daily_kline 构建历史训练样本.

    Returns:
        X: np.ndarray (n_samples, 80)
        y: np.ndarray (n_samples,)
        sd_list: list[date]
    """
    start_d = date.fromisoformat(start) if isinstance(start, str) else start
    end_d = date.fromisoformat(end) if isinstance(end, str) else end

    async with async_session_factory() as s:
        # ── 1. 获取交易日列表 ──
        r = await s.execute(text(
            "SELECT DISTINCT trade_date FROM daily_kline "
            "WHERE trade_date BETWEEN :lo AND :hi ORDER BY trade_date"
        ), {"lo": start_d, "hi": end_d + timedelta(days=10)})
        all_dates = [row[0] for row in r.fetchall()]

        # 切分: 每个 scan_date 只能从 ≤scan_date 的 K 线采样，且需要 ≥5 个未来交易日做标签
        valid_dates = [d for d in all_dates if d <= end_d and d >= start_d]
        if not valid_dates:
            logger.warning("No valid trading dates in range")
            return np.empty((0, len(FEAT_NAMES_HIST))), np.empty(0), []

        # ── 2. 选股池: 取范围中点附近活跃股票 ──
        mid = start_d + (end_d - start_d) // 2
        r = await s.execute(text(
            "SELECT DISTINCT ts_code FROM daily_kline "
            "WHERE trade_date BETWEEN :lo AND :hi LIMIT 2000"
        ), {"lo": mid - timedelta(days=5), "hi": mid})
        eligible_stocks = [row[0] for row in r.fetchall()]
        random.shuffle(eligible_stocks)
        logger.info(f"Stock pool: {len(eligible_stocks)} from 2025-06-30 snapshot")

    # ── 预加载大盘基准 ──
    async with async_session_factory() as s:
        await _preload_market_closes(s)

    # ── 3. 逐日采样 ──
    X_parts, y_parts, sd_parts = [], [], []
    # Phase 71a: 每天采样 (step=1), 上限 500 天 × 50 股 ≈ 25,000 样本
    sampled_dates = valid_dates
    if len(sampled_dates) > 500:
        sampled_dates = sampled_dates[:500]
    logger.info(f"Sampling from {len(sampled_dates)} dates, {per_day} stocks/day...")

    for di, scan_d in enumerate(sampled_dates):
        if (di + 1) % 50 == 0:
            logger.info(f"  day {di+1}/{len(sampled_dates)}, samples so far: {sum(len(p) for p in X_parts)}")

        # 随机选 per_day 只
        if len(eligible_stocks) < per_day:
            selected = eligible_stocks
        else:
            selected = random.sample(eligible_stocks, per_day)

        if not selected:
            continue

        try:
            async with async_session_factory() as s:
                # 批量预加载 K 线
                await _preload_klines_batch(s, selected, scan_d)
                # 预加载板块特征
                sec_cache = await _preload_sector_features(s, selected, scan_d)

                for sym in selected:
                    arr = _KLINES_BATCH.get(sym)
                    if not arr:
                        continue
                    closes, opens, highs, lows, volumes, amounts = arr
                    if len(closes) < 60:
                        continue

                    feats = _build_features_from_arrays(closes, opens, highs, lows, volumes, amounts, sym, scan_d)
                    sc = sec_cache.get(sym, {})
                    feats.update(sc)
                    if sc:
                        feats["x_real_alpha"] = round(feats.get("chg_5d", 0) - sc.get("x_real_sector_5d", 0), 2)

                    # ── 标签: T+5 超额收益 ──
                    # 找到 scan_d 在 K 线数组中的位置
                    trade_dates = [scan_d - timedelta(days=i) for i in range(len(closes), 0, -1)]  # rough
                    # 用数据库查更精确: 拿 scan_d + 5 个交易日的 close
                    r2 = await s.execute(text(
                        "SELECT trade_date, close FROM daily_kline "
                        "WHERE ts_code = :sym AND trade_date > :d "
                        "ORDER BY trade_date LIMIT 6"
                    ), {"sym": sym, "d": scan_d})
                    future_rows = r2.fetchall()
                    if len(future_rows) < 6:
                        continue  # 不足 5 个未来交易日

                    c0 = float(future_rows[0][1] or 0)
                    c5 = float(future_rows[5][1] or 0)  # index 5 = T+5
                    if c0 <= 0 or c5 <= 0:
                        continue
                    stock_ret = round((c5 - c0) / c0 * 100, 4)
                    label = _excess_return(_market_closes, scan_d, stock_ret, 5)

                    # ── 历史缺失维度填 0 ──
                    # toplist: 2026-04 前无数据, 全 0
                    for tl_key in ["tl_on_toplist", "tl_net_buy_ratio", "tl_inst_ratio",
                                   "tl_buy_concentration", "tl_appearances_5d", "tl_appearances_20d",
                                   "tl_avg_net_5d", "tl_net_trend", "tl_turnover_signal",
                                   "tl_oversold", "tl_breakout",
                                   "tl_inst_continuous", "tl_seat_quality",
                                   "tl_net_trend_10d", "tl_consecutive_days",
                                   "tl_avg_amount_ratio", "tl_inst_net_streak"]:
                        feats.setdefault(tl_key, 0.0)
                    # news: 历史不可回溯
                    for nk in ["news_commodity_bear", "news_commodity_bull",
                               "news_policy_bear", "news_policy_bull"]:
                        feats.setdefault(nk, 0.0)
                    # weekly: 无 scan_results
                    feats.setdefault("weekly_tg_momentum", 0.0)
                    feats.setdefault("weekly_daily_divergence", 0.0)
                    # 信号历史
                    feats.setdefault("push_count_30d", 0.0)
                    feats.setdefault("days_since_last_signal", 30.0)
                    feats.setdefault("same_stock_win_rate", 0.5)
                    feats.setdefault("same_sector_signal_count", 0.0)

                    # ── Phase 71: 历史标记 ──
                    feats["_is_historical"] = 1.0
                    feats["_has_toplists"] = 0.0
                    feats["_has_news"] = 0.0

                    vec = [feats.get(f, 0.0) for f in FEAT_NAMES_HIST]
                    X_parts.append(vec)
                    y_parts.append(label)
                    sd_parts.append(scan_d)
        except Exception as e:
            logger.debug(f"Day {scan_d} failed: {e}")
            continue

    if not X_parts:
        logger.warning("No historical samples generated")
        return np.empty((0, len(FEAT_NAMES_HIST))), np.empty(0), []

    X = np.array(X_parts, dtype=np.float32)
    y = np.array(y_parts, dtype=np.float32)
    X = sanitize_array(X, fill=0.0)
    y = sanitize_label_array(y)

    logger.info(f"Historical samples: {len(X)} from {len(sampled_dates)} trading days, "
                f"label mean={y.mean():.2f}%")
    return X, y, sd_parts


async def main():
    """CLI: 独立运行采样并保存到文件."""
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    t0 = __import__('time').time()
    X, y, sd_list = await sample_historical(per_day=int(sys.argv[1]) if len(sys.argv) > 1 else 20)
    elapsed = __import__('time').time() - t0

    print(f"\nSampled {len(X)} stocks over {len(set(sd_list))} trading days, total {len(X)} samples")
    print(f"Time: {elapsed:.0f}s")
    print(f"Label: mean={y.mean():.2f}% median={np.median(y):.2f}%")
    print(f"  >0: {(y>0).mean()*100:.1f}%  >5%: {(y>5).mean()*100:.1f}%")

    if len(X) > 0:
        os.makedirs('data', exist_ok=True)
        np.savez_compressed('data/historical_samples.npz', X=X, y=y)
        with open('data/historical_dates.txt', 'w') as f:
            f.write(','.join(str(d) for d in sd_list))
        print("Saved to data/historical_samples.npz")


if __name__ == "__main__":
    asyncio.run(main())
