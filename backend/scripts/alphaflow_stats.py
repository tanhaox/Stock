"""AlphaFlow Step 3: 统计检验 — 分钟线特征能否区分主升浪和普通股票.

对比两个群体在 5 个日内特征上的差异:
  A组: major_rally (赢) — 提取了分钟线特征的正样本
  B组: normal (随机负样本) — 相同数量的随机非赢样本

输出: 每个特征的 t-test p值 + Cohen's d 效应量
"""
import asyncio, sys, logging, json, numpy as np
from scipy import stats
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("alphaflow.stats")

FEATURE_NAMES = [
    "v_reversal_rate",
    "tail_ratio",
    "amp_trend",
    "vol_conc",
    "morning_spike_rate",
]

FEATURE_CN = {
    "v_reversal_rate": "V型反转频率",
    "tail_ratio": "尾盘异动率",
    "amp_trend": "振幅趋势",
    "vol_conc": "量能集中度",
    "morning_spike_rate": "开盘冲高率",
}


async def run_statistical_test():
    """对比 major_rally vs normal 的分钟线特征."""

    async with async_session_factory() as s:
        # 获取有分钟线特征的 major_rally 样本
        r = await s.execute(text("""
            SELECT ts_code, sample_date, v_reversal_rate, tail_ratio, amp_trend,
                   vol_conc, morning_spike_rate
            FROM trend_sample_features
        """))
        rally_rows = r.fetchall()

        # 获取等量 normal 样本 (暂时用日线特征替代, 因为还没下载分钟线)
        # 先检查已有多少条
        r2 = await s.execute(text("""
            SELECT COUNT(*) FROM trend_sample_features
        """))
        n_rally = r2.scalar()
        logger.info(f"Rally samples with minute features: {n_rally}")

    if n_rally < 10:
        logger.warning("Not enough minute feature samples yet. Run Step 2 first.")
        return {"status": "insufficient_data", "n_rally": n_rally}

    # 提取特征矩阵
    rally_feats = {name: [] for name in FEATURE_NAMES}
    for row in rally_rows:
        for i, name in enumerate(FEATURE_NAMES):
            val = float(row[i + 2]) if row[i + 2] is not None else 0.0
            rally_feats[name].append(val)

    # 对比日线级别的趋势样本 (用 trend_samples 的 label 做对照)
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT ts_code, sample_date FROM trend_samples
            WHERE label = 'normal'
            ORDER BY RANDOM() LIMIT :n
        """), {"n": n_rally * 3})
        normal_samples = [(row[0], row[1]) for row in r.fetchall()]

    # 对 normal 样本下载分钟线
    from scripts.alphaflow_mins import _call_tushare_mins, _extract_features
    from datetime import timedelta
    from collections import defaultdict

    normal_feats = {name: [] for name in FEATURE_NAMES}
    extracted_normal = 0

    for ts_code, sample_date in normal_samples:
        end_dt = str(sample_date)
        start_dt = str(sample_date - timedelta(days=90))

        raw = await _call_tushare_mins(ts_code, start_dt, end_dt)
        if not raw:
            continue

        mins_by_day = defaultdict(list)
        for bar in raw:
            mins_by_day[bar["trade_time"][:10]].append(bar)

        feats = _extract_features(mins_by_day)
        for name in FEATURE_NAMES:
            normal_feats[name].append(feats[name])
        extracted_normal += 1

        if extracted_normal >= n_rally:
            break

    logger.info(f"Extracted minute features for {extracted_normal} normal samples")

    # ── 统计检验 ──
    print(f"\n{'特征':<16} {'Rally均值':<12} {'Normal均值':<12} {'差值':<10} {'p值':<12} {'Cohen d':<10} {'结论'}")
    print("-" * 82)

    results = []
    for name in FEATURE_NAMES:
        r_vals = np.array(rally_feats[name])
        n_vals = np.array(normal_feats[name])

        if len(n_vals) < 10:
            continue

        r_mean = float(np.mean(r_vals))
        n_mean = float(np.mean(n_vals))
        diff = r_mean - n_mean

        # t-test
        t_stat, p_val = stats.ttest_ind(r_vals, n_vals, equal_var=False)
        # Cohen's d
        pooled_std = np.sqrt((np.var(r_vals) + np.var(n_vals)) / 2)
        cohens_d = diff / pooled_std if pooled_std > 0 else 0

        # 判断
        if p_val < 0.01 and abs(cohens_d) > 0.5:
            verdict = "⭐⭐⭐ 强区分"
        elif p_val < 0.05 and abs(cohens_d) > 0.3:
            verdict = "⭐⭐ 有区分"
        elif p_val < 0.10:
            verdict = "⭐ 弱区分"
        else:
            verdict = "无区分"

        print(f"{FEATURE_CN.get(name, name):<16} {r_mean:<12.4f} {n_mean:<12.4f} "
              f"{diff:>+10.4f} {p_val:<12.4f} {cohens_d:>+10.3f} {verdict}")

        results.append({
            "feature": name,
            "rally_mean": round(r_mean, 4),
            "normal_mean": round(n_mean, 4),
            "diff": round(diff, 4),
            "p_value": round(float(p_val), 6),
            "cohens_d": round(float(cohens_d), 3),
            "verdict": verdict,
        })

    n_significant = sum(1 for r in results if "区分" in r["verdict"])
    print(f"\n结论: {n_significant}/{len(results)} 个特征有区分度")
    return {"status": "success", "n_rally": n_rally, "n_normal": extracted_normal,
            "n_significant": n_significant, "features": results}


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    result = await run_statistical_test()
    if result["status"] == "success" and result["n_significant"] >= 2:
        print("\n✅ 分钟线特征可以有效区分主升浪 — 值得投入 LSTM 训练")
    elif result["status"] == "success":
        print("\n⚠️ 分钟线特征区分度有限 — 建议先看少量样本的日线+资金流向能否区分")
    else:
        print(f"\n⏳ 数据不足: {result}")


if __name__ == "__main__":
    asyncio.run(main())
