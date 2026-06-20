"""v1 vs v2 模型 PK 脚本.

测试日期: 2024-03-08, 2024-03-18, 2024-12-02
对比: 3 组推荐 (前 5 / 5-10 / 10-15 名), T+N 收益
回测规则:
  - 买入价: T+1 均价 (high + low) / 2
  - 卖出价: T+N 均价 (high + low) / 2
  - 收益 = (卖 - 买) / 买 * 100%

v1 模型: 用 bayesian_beliefs 默认权重 + 简单加权打分
v2 模型: 用 param_library_v2 LR 系数 + logistic sigmoid
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
import asyncio
import json
import math
import logging
from datetime import date, datetime, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory
from app.core.config import settings
import asyncpg

DSN = settings.DATABASE_URL.replace('postgresql+asyncpg://', 'postgresql://')

# 关闭 SQL 日志
for name in ['sqlalchemy.engine', 'sqlalchemy.engine.Engine']:
    logging.getLogger(name).disabled = True
logging.getLogger().setLevel(logging.WARNING)


# ============================================================
#  v1 评分逻辑
# ============================================================

V1_DIM_TO_WEIGHT = {
    # dim_key: weight_name
    'technical': 'tech_weight',
    'kline_game': 'kline_weight',
    'fundamentals': 'fundamentals_weight',
    'fund_flow': 'fund_weight',
    'ma_trend': 'trend_weight',
    'multi_box': 'pattern_weight',
    'market_relative': 'momentum_weight',
    'sector_alpha': 'sector_weight',
    'valuation': 'valuation_weight',
    'vol_ratio': 'volume_weight',
    'downside_risk': 'volatility_weight',
    'weekly_resonance': 'quality_weight',
    'toplist_sector': 'event_weight',
    'ambush': 'news_weight',
    'chip_winner': 'chip_weight',
    'bbi': 'macro_weight',
    'tg_momentum': 'momentum_weight',
    'dist_low': 'volatility_weight',
    'j_value': 'sentiment_weight',
    'arbr': 'volume_weight',
    'trend_deviation': 'volatility_weight',
}

V1_DEFAULT_WEIGHTS = {
    'tech_weight': 3.5, 'kline_weight': 3.0, 'fundamentals_weight': 1.5,
    'fund_weight': 2.5, 'trend_weight': 1.0, 'pattern_weight': 1.0,
    'momentum_weight': 1.0, 'sector_weight': 1.0, 'valuation_weight': 1.0,
    'volume_weight': 1.0, 'volatility_weight': 1.0, 'quality_weight': 1.0,
    'event_weight': 0.5, 'news_weight': 0.5, 'chip_weight': 1.0,
    'macro_weight': 0.5, 'sentiment_weight': 1.0,
}


def _extract_v1_score(dims: dict, dim_key: str) -> float:
    """从 dimension_scores 提取 dim_key 的分数 (兼容 v1/v2 schema)."""
    if not dims:
        return 5.0
    v = dims.get(dim_key)
    if isinstance(v, dict) and 'score' in v:
        return float(v['score'])
    if isinstance(v, (int, float)):
        return float(v)
    legacy = dims.get(f"{dim_key}_score")
    if isinstance(legacy, (int, float)):
        return float(legacy)
    if isinstance(legacy, dict) and 'score' in legacy:
        return float(legacy['score'])
    return 5.0


def score_v1(dims: dict, v1_weights: dict) -> float:
    """v1 加权打分: Σ(w*s) / Σw × 10, 输出 0~100."""
    weighted_sum = 0.0
    weight_total = 0.0
    for dim_key, w_name in V1_DIM_TO_WEIGHT.items():
        if dim_key in dims or f"{dim_key}_score" in dims:
            w = v1_weights.get(w_name, V1_DEFAULT_WEIGHTS.get(w_name, 1.0))
            s = _extract_v1_score(dims, dim_key)
            weighted_sum += w * s
            weight_total += w
    return (weighted_sum / weight_total * 10) if weight_total > 0 else 50.0


# ============================================================
#  v2 评分逻辑
# ============================================================

V2_DIM_KEYS = [
    'tg_momentum', 'dist_low', 'j_value', 'technical', 'kline_game',
    'vol_ratio', 'arbr', 'bbi', 'trend_deviation', 'downside_risk',
    'ma_trend', 'multi_box', 'market_relative', 'fund_flow', 'sector_alpha',
    'fundamentals', 'valuation', 'weekly_resonance', 'toplist_sector',
    'ambush', 'chip_winner', 'chip_cost',
    'macd', 'kdj', 'boll', 'cci', 'chip_winner_rate',
]


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def score_v2(dims: dict, weights: dict) -> float:
    """v2 LR 打分: P(y=1) = sigmoid(intercept + Σ w*s)."""
    intercept = weights.get("_intercept_", 0.0)
    z = intercept
    for feat in V2_DIM_KEYS:
        if feat in weights:
            v = _extract_v1_score(dims, feat)  # 同 extraction 逻辑
            z += weights[feat] * v
    return _sigmoid(z)


# ============================================================
#  T+N 收益计算
# ============================================================

async def fetch_prices(conn, ts_code: str, scan_date: date, n_days: int) -> list:
    """获取 scan_date + 1 到 +N 的 K 线."""
    end = scan_date + timedelta(days=n_days + 10)  # 多取几天防周末/节假日
    rows = await conn.fetch("""
        SELECT trade_date, open, close, high, low
        FROM daily_kline
        WHERE ts_code = $1 AND trade_date > $2 AND trade_date <= $3
        ORDER BY trade_date
    """, ts_code, scan_date, end)
    return [(r['trade_date'], float(r['open']), float(r['close']),
             float(r['high']), float(r['low'])) for r in rows]


def get_t1_avg_price(bars: list) -> float | None:
    """T+1 均价 = (high + low) / 2."""
    if not bars:
        return None
    d, o, c, h, l = bars[0]
    return (h + l) / 2


def get_tn_avg_price(bars: list, n: int) -> float | None:
    """T+N 均价 = 第 N 个交易日的 (high + low) / 2."""
    if len(bars) < n:
        return None
    d, o, c, h, l = bars[n - 1]
    return (h + l) / 2


# ============================================================
#  权重加载
# ============================================================

async def load_v1_weights(conn) -> dict:
    """v1 权重: 从 bayesian_beliefs 加载 (archetype=__global__)."""
    rows = await conn.fetch("""
        SELECT param_name, mu FROM bayesian_beliefs
        WHERE archetype = '__global__'
          AND NOT param_name LIKE '\\_\\_%' ESCAPE '\\'
    """)
    weights = {r['param_name']: float(r['mu']) for r in rows}
    # 合并默认 (避免缺失)
    return {**V1_DEFAULT_WEIGHTS, **weights}


async def load_v2_weights(conn, market_style: str) -> dict:
    """v2 权重: 从 param_library_v2 加载 (按 market_style)."""
    rows = await conn.fetch(f"""
        SELECT horizon_days, model_type, scoring_weights
        FROM param_library_v2
        WHERE is_active = true AND archetype = '__global__'
          AND market_style = '{market_style}'
        ORDER BY horizon_days, model_type
    """)
    weights_map = {}
    for row in rows:
        w = row['scoring_weights']
        if isinstance(w, str):
            w = json.loads(w)
        weights_map[(row['horizon_days'], row['model_type'])] = w
    return weights_map


# ============================================================
#  推荐打分 (per scan_date)
# ============================================================

async def score_scan_date(conn, scan_date: date, v1_weights: dict, v2_weights: dict):
    """为某个 scan_date 的所有股票打 v1/v2 分数."""
    rows = await conn.fetch("""
        SELECT symbol, dimension_scores, composite_score
        FROM analysis_scores
        WHERE scan_date = $1
    """, scan_date)

    results = []
    for row in rows:
        sym = row['symbol']
        dims_raw = row['dimension_scores']
        dims = dims_raw if isinstance(dims_raw, dict) else (json.loads(dims_raw) if dims_raw else {})
        v1_score = score_v1(dims, v1_weights)
        # v2 用 net (p_win - p_loss) on T+5
        w_win = v2_weights.get((5, 'win'), {})
        w_loss = v2_weights.get((5, 'loss'), {})
        p_win = score_v2(dims, w_win)
        p_loss = score_v2(dims, w_loss)
        v2_net = p_win - p_loss
        results.append({
            'symbol': sym,
            'v1_score': v1_score,
            'v2_net': v2_net,
            'orig_composite': float(row['composite_score'] or 50),
        })
    return results


# ============================================================
#  PK 单天
# ============================================================

async def pk_one_date(conn, scan_date: date, market_style: str = 'all'):
    """PK 一个扫描日."""
    print(f"\n{'='*80}")
    print(f"  扫描日: {scan_date}  (使用 v2 market_style={market_style})")
    print(f"{'='*80}")

    # 加载权重
    v1_w = await load_v1_weights(conn)
    v2_w = await load_v2_weights(conn, market_style)
    print(f"\n  v1 权重: {len(v1_w)} params (bayesian_beliefs + 默认)")
    print(f"  v2 权重: {len(v2_w)} 套 (param_library_v2)")

    # 打分
    results = await score_scan_date(conn, scan_date, v1_w, v2_w)
    print(f"  共 {len(results)} 只票打分")

    # 按 v1 和 v2 分别排序
    v1_sorted = sorted(results, key=lambda x: x['v1_score'], reverse=True)
    v2_sorted = sorted(results, key=lambda x: x['v2_net'], reverse=True)

    # 三组: 前 5 / 5-10 / 10-15
    groups = [
        ('Top 5', 0, 5),
        ('Rank 5-10', 5, 10),
        ('Rank 10-15', 10, 15),
    ]

    summary = []
    for group_name, start, end in groups:
        v1_picks = v1_sorted[start:end]
        v2_picks = v2_sorted[start:end]
        print(f"\n  --- {group_name} ---")

        for label, picks in [('v1', v1_picks), ('v2', v2_picks)]:
            print(f"    {label}: " + ", ".join(f"{r['symbol']}({r['v1_score']:.1f}/{r['v2_net']:.3f})"
                                                 for r in picks))

        # 算 T+N 收益
        for label, picks in [('v1', v1_picks), ('v2', v2_picks)]:
            returns_t5 = []
            returns_t10 = []
            for pick in picks:
                sym = pick['symbol']
                bars = await fetch_prices(conn, sym, scan_date, 12)
                t1_price = get_t1_avg_price(bars)
                t5_price = get_tn_avg_price(bars, 5)
                t10_price = get_tn_avg_price(bars, 10)
                if t1_price and t5_price:
                    ret5 = (t5_price - t1_price) / t1_price * 100
                    returns_t5.append(ret5)
                if t1_price and t10_price:
                    ret10 = (t10_price - t1_price) / t1_price * 100
                    returns_t10.append(ret10)

            avg_t5 = sum(returns_t5) / len(returns_t5) if returns_t5 else 0
            avg_t10 = sum(returns_t10) / len(returns_t10) if returns_t10 else 0
            win_t5 = sum(1 for r in returns_t5 if r > 0)
            win_t10 = sum(1 for r in returns_t10 if r > 0)

            print(f"    {label} 收益: T+5 avg={avg_t5:+.2f}% ({win_t5}/{len(returns_t5)} 胜), T+10 avg={avg_t10:+.2f}% ({win_t10}/{len(returns_t10)} 胜)")
            summary.append({
                'scan_date': str(scan_date),
                'group': group_name,
                'model': label,
                'n': len(picks),
                'avg_t5': avg_t5,
                'avg_t10': avg_t10,
                'win_t5': win_t5,
                'win_t10': win_t10,
                'picks': [p['symbol'] for p in picks],
            })

    return summary


# ============================================================
#  Main
# ============================================================

async def main():
    test_dates = [date(2024, 3, 8), date(2024, 3, 18), date(2024, 12, 2)]
    conn = await asyncpg.connect(DSN)
    try:
        all_results = []
        for sd in test_dates:
            r = await pk_one_date(conn, sd, market_style='all')
            all_results.extend(r)

        # 最终汇总
        print(f"\n\n{'='*80}")
        print(f"  最终汇总 ({len(test_dates)} 天 × 3 组 × 2 模型)")
        print(f"{'='*80}")
        for grp in ['Top 5', 'Rank 5-10', 'Rank 10-15']:
            print(f"\n  {grp}:")
            v1_t5 = [r['avg_t5'] for r in all_results if r['group'] == grp and r['model'] == 'v1']
            v2_t5 = [r['avg_t5'] for r in all_results if r['group'] == grp and r['model'] == 'v2']
            v1_t10 = [r['avg_t10'] for r in all_results if r['group'] == grp and r['model'] == 'v1']
            v2_t10 = [r['avg_t10'] for r in all_results if r['group'] == grp and r['model'] == 'v2']
            print(f"    v1 T+5 avg={sum(v1_t5)/len(v1_t5):+.2f}%, T+10 avg={sum(v1_t10)/len(v1_t10):+.2f}%")
            print(f"    v2 T+5 avg={sum(v2_t5)/len(v2_t5):+.2f}%, T+10 avg={sum(v2_t10)/len(v2_t10):+.2f}%")
    finally:
        await conn.close()


if __name__ == '__main__':
    asyncio.run(main())