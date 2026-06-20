"""影子训练引擎 v2.0 — Bayesian Optimization (EI+Matern) + 超额收益 + 牛熊拆分 + 过拟合检测.

Phase 2 核心模块。每个(原型, 策略)独立训练，按市场阶段评估.
"""
import asyncio, json, logging, random, time as _time
from datetime import date, timedelta
import numpy as np
from sqlalchemy import text
from app.core.database import async_session_factory
from app.core.market_data import compute_excess_return, get_benchmark_closes
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import Matern, WhiteKernel, ConstantKernel

logger = logging.getLogger(__name__)


async def sync_delisted_stocks() -> dict:
    """从 Tushare suspend_d 拉取退市股列表，补全历史K线，存入 delisted_stocks.

    用于幸存者偏差修复：S3 训练时注入退市股作为负样本.
    建议季度执行一次.
    """
    from app.services.tushare_common import call_tushare

    all_delisted = await call_tushare("suspend_d", {}, "ts_code,suspend_date,suspend_type")
    if not all_delisted:
        return {"status": "error", "detail": "suspend_d API 无返回"}

    delisted_codes = []
    for d in all_delisted:
        stype = d.get("suspend_type", "") or ""
        if "退市" in stype or "终止" in stype:
            delisted_codes.append(d["ts_code"])

    logger.info(f"suspend_d total {len(all_delisted)}, 真正退市 {len(delisted_codes)}")
    if not delisted_codes:
        return {"status": "empty", "detail": "无退市股"}

    inserted = 0
    batch_size = 80

    for i in range(0, len(delisted_codes), batch_size):
        batch = delisted_codes[i:i + batch_size]
        try:
            klines = await call_tushare("daily", {
                "ts_code": ",".join(batch),
                "start_date": "20150101",
                "end_date": date.today().strftime("%Y%m%d"),
            }, "ts_code")
        except Exception as e:
            logger.warning(f"Batch {i // batch_size} failed: {e}")
            continue

        if not klines:
            continue

        from collections import Counter
        cnt = Counter(k["ts_code"] for k in klines)

        async with async_session_factory() as s:
            for code in batch:
                kline_count = cnt.get(code, 0)
                if kline_count >= 60:
                    await s.execute(text("""
                        INSERT INTO delisted_stocks (ts_code, kline_count, synced_at)
                        VALUES (:c, :k, NOW())
                        ON CONFLICT (ts_code) DO UPDATE SET kline_count=:k, synced_at=NOW()
                    """), {"c": code, "k": kline_count})
                    inserted += 1
            await s.commit()

    logger.info(f"Synced {inserted} delisted stocks with K-line data")
    return {"status": "success", "inserted": inserted, "total_delisted": len(delisted_codes)}


from app.services.deep_scorer import DEFAULT_WEIGHTS

FORECAST_HORIZONS = {"S1": 2, "S2": 2, "S3": 5}  # Phase E: T+2为首个可卖日
N_CANDIDATES = 5
PERTURB_STD = 0.15
N_RANDOM_STARTS = 20
EI_STOP_THRESHOLD = 0.01

_ARCH_RULES = {
    "large_bluechip": ["银行","保险","证券","金融","信托","白酒","食品","饮料","家电"],
    "growth_tech": ["半导体","芯片","元器件","通信","计算机设备","机械","机器人","光刻","PCB","制药","生物","医疗","医药","中药","创新药","CRO","器械","软件","互联网","IT服务","电信","传媒","游戏","数据","AI"],
    "cyclical_resource": ["石油","煤炭","有色","钢铁","化工","化纤","造纸","稀土","锂"],
    "value_defensive": ["电力","水务","供气","路桥","港口","房产","建筑","建材","环保","家居","纺织","旅游","酒店","农林牧渔","农业","乳业","食品","饮料"],
    "small_speculative": ["综合"],
}

_SECTOR_INDEX_MAP = {
    "large_bluechip": "801780.SI",     # 食品饮料
    "small_speculative": "801010.SI",  # 综合
    "growth_tech": "801750.SI",        # 计算机
    "value_defensive": "801160.SI",    # 公用事业
    "cyclical_resource": "801050.SI",  # 有色金属
}

_concept_cache: dict[str, str] | None = None


async def _get_fingerprint_archetype_map() -> dict[str, str]:
    """从 analysis_scores 加载 ts_code -> archetype (归一化为5型英文名).

    DB 中 archetype 格式为 '前缀_archname' (如 行业_small_speculative),
    此函数提取后缀并校验是否为已知的5型名, 无效则回退到关键词分类.
    """
    VALID_ARCHES = {"large_bluechip", "growth_tech", "cyclical_resource", "value_defensive", "small_speculative"}
    async with async_session_factory() as s:
        result = {}
        r = await s.execute(text(
            "SELECT DISTINCT ON (symbol) symbol, archetype FROM analysis_scores "
            "WHERE archetype IS NOT NULL "
            "ORDER BY symbol, scan_date DESC"
        ))
        for row in r.fetchall():
            if row[1]:
                arch = row[1].split("_")[-1] if "_" in row[1] else row[1]
                if arch in VALID_ARCHES:
                    result[row[0]] = arch
                else:
                    result[row[0]] = "small_speculative"
    return result


ARCH_SQL_FILTER = "SPLIT_PART(archetype, '_', 2) = :a"


async def _get_concept_map() -> dict[str, str]:
    global _concept_cache
    if _concept_cache is not None: return _concept_cache
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT ts_code, STRING_AGG(ths_name,',') FROM ths_member WHERE out_date IS NULL GROUP BY ts_code"))
        _concept_cache = {row[0]: (row[1] or "") for row in r.fetchall()}
    return _concept_cache


def _classify_archetype(concepts: str) -> str:
    """关键词->原型（仅作 analysis_scores 缺失时的 fallback）. Phase A 统一5型命名."""
    if not concepts: return "large_bluechip"
    for arch, keywords in _ARCH_RULES.items():
        for kw in keywords:
            if kw in concepts: return arch
    return "large_bluechip"


async def _get_active_weights() -> dict[str, float]:
    """从维度注册表动态构建权重字典.

    核心 7 维 + 已接入数据源的扩展维度. 未接入的 probation 维度不加权重.
    """
    weights = dict(DEFAULT_WEIGHTS)
    # 已接入数据源的维度白名单 (对应 enhanced_scorer.py ENHANCED_RULES)
    WIRED_DIMS = {"real_fund"}  # moneyflow已接入deep_analyze
    try:
        async with async_session_factory() as s:
            r = await s.execute(text(
                "SELECT dim_key, status FROM learning_dimension_registry WHERE status IN ('active', 'probation')"
            ))
            for row in r.fetchall():
                dim_key = row[0]; key = dim_key + "_weight"
                if key in weights: continue
                if row[1] == 'active' or dim_key in WIRED_DIMS:
                    weights[key] = 1.0
                # probation 且未接入: 不加权重，避免优化空维度(方差=0污染GP)
    except Exception:
        pass
    return weights


async def _get_market_phases(dates: list[date]) -> dict[date, str]:
    """用 10日指数涨跌 判定市场阶段: 涨>1%→bull, 跌>1%→bear, 否则→range."""
    if not dates:
        return {}
    d1, d2 = min(dates) - timedelta(days=20), max(dates)
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT trade_date, close FROM daily_kline WHERE ts_code='700001.TI' AND trade_date BETWEEN :d1 AND :d2 ORDER BY trade_date"
        ), {"d1": d1, "d2": d2})
        prices = {row[0]: float(row[1]) for row in r.fetchall()}

    result = {}
    for d in dates:
        if d not in prices:
            result[d] = "range"  # 指数无数据, 默认震荡
            continue
        prev_dates = sorted([pd for pd in prices if pd < d], reverse=True)
        if len(prev_dates) < 11:
            result[d] = "range"
            continue
        # 10日前的收盘价（跳过今天，取前第10个交易日）
        d_10ago = prev_dates[min(9, len(prev_dates) - 1)]
        if d_10ago not in prices or prices[d_10ago] == 0:
            result[d] = "range"
            continue
        pct = (prices[d] - prices[d_10ago]) / prices[d_10ago] * 100
        if pct > 1.0:
            result[d] = "bull"
        elif pct < -1.0:
            result[d] = "bear"
        else:
            result[d] = "range"
    return result


def generate_candidates(current_weights, n=N_CANDIDATES):
    candidates = []
    keys = list(current_weights.keys())
    orig_total = sum(current_weights.values())
    for _ in range(n):
        cand = {}
        for k in keys:
            f = 1.0 + np.random.uniform(-0.25, 0.25)
            cand[k] = current_weights[k] * max(0.2, min(4.0, f))
        total = sum(cand.values())
        cand = {k: round(v / total * orig_total, 4) for k, v in cand.items()}
        # 硬钳制: 单权重不能超过默认值的5倍或低于0.1
        for k in keys:
            cand[k] = max(0.1, min(8.0, cand[k]))
        candidates.append(cand)
    return candidates


def _weights_to_vec(weights: dict) -> np.ndarray:
    return np.array([weights[k] for k in sorted(weights.keys())])


def _vec_to_weights(vec: np.ndarray, template: dict) -> dict:
    keys = sorted(template.keys())
    return {k: round(float(v), 4) for k, v in zip(keys, vec)}


def _expected_improvement(x: np.ndarray, gp: GaussianProcessRegressor, y_best: float, xi: float = 0.01) -> float:
    """Expected Improvement 采集函数."""
    x = x.reshape(1, -1)
    mu, sigma = gp.predict(x, return_std=True)
    mu, sigma = float(mu[0]), float(sigma[0])
    if sigma < 1e-6: return 0.0
    z = (mu - y_best - xi) / sigma
    from scipy.stats import norm
    return float(sigma * (z * norm.cdf(z) + norm.pdf(z)))


def _propose_next(gp: GaussianProcessRegressor, template: dict, y_best: float, bounds: list, n_restarts: int = 50) -> np.ndarray:
    """用 EI 采集函数选择下一组候选权重."""
    best_x, best_ei = None, -np.inf
    for _ in range(n_restarts):
        x = np.random.uniform([b[0] for b in bounds], [b[1] for b in bounds])
        ei = _expected_improvement(x, gp, y_best)
        if ei > best_ei: best_ei, best_x = ei, x
    return best_x


# ═══════════ M-4: 宏观上下文 + 三级评分 ═══════════

TIER1_INDICATORS = [
    "m2_yoy", "m1_m2_scissor", "shibor_spread", "shibor_3m_chg",
    "lpr_1y", "lpr_5y", "bond_3m_yield", "bond_10y_yield",
    "pmi", "pmi_new_order", "pmi_export_order",
    "cpi_yoy", "ppi_yoy", "gdp_yoy",
]

TIER2_INDICATORS = [
    "crude_oil", "copper", "aluminum", "rebar", "iron_ore", "coke_coal",
    "lithium", "silicon", "gold", "natural_rubber", "methanol", "pvc",
]

TIER3_INDICATORS = [
    "big_order_net", "margin_balance_chg", "north_hold_chg", "north_hold_ratio",
    "block_trade_premium", "pledge_ratio",
    "roe", "roe_yoy", "revenue_yoy", "gross_margin", "forecast_surprise",
]


async def build_macro_context(trade_date, session=None) -> dict:
    """构建训练日的宏观上下文 (M-4).

    从 macro_cache 读取已同步的指标, 组装 Tier1/Tier2 快照.
    """
    from app.services.macro_data import get_macro_snapshot

    snapshot = await get_macro_snapshot(session)

    # Tier 1: 大盘级 — 直接从 snapshot 取值
    tier1 = {}
    for name in TIER1_INDICATORS:
        val = snapshot.get(name, {}).get("value", 0)
        try:
            tier1[name] = float(val)
        except (ValueError, TypeError):
            tier1[name] = 0.0

    # Tier 2: commodity/sector/concept — 从 snapshot 读取
    tier2 = {}
    for name in TIER2_INDICATORS:
        key = f"commodity:{name}" if f"commodity:{name}" in snapshot else name
        val = snapshot.get(key, {}).get("value", 0)
        try:
            tier2[name] = float(val)
        except (ValueError, TypeError):
            tier2[name] = 0.0

    return {"tier1": tier1, "tier2": tier2, "date": trade_date}


def score_stock(row: dict, weights: dict, macro_context=None) -> float:
    """三级漏斗评分 (M-4).

    Tier 1: 现有 23 技术维度 (row 中已有 _score 后缀字段)
    Tier 2: 大盘级宏观 (value × weight)
    Tier 3: 板块级 (value × exposure × weight) + 个股级 (value × weight)

    Returns 0-100 scaled score.
    """
    from app.services.factor_exposure import get_sector_exposure

    total = 0.0
    total_w = 0.0

    # ── Tier 1: 技术面 (23维) ──
    tech_keys = [k for k in weights if not k.startswith("macro_") and k.endswith("_weight")]
    for wk in tech_keys:
        score_key = wk.replace("_weight", "_score")
        if score_key in row:
            s = float(row.get(score_key, 5))
            total += s * 10 * weights[wk]
            total_w += weights[wk]

    if macro_context:
        # ── Tier 2: 大盘级 ──
        for name, val in macro_context.get("tier1", {}).items():
            wk = f"macro_{name}"
            if wk in weights:
                total += float(val) * weights[wk]
                total_w += weights[wk]

        # ── Tier 3: 板块级 (乘以暴露系数) ──
        sector = str(row.get("sector", "") or row.get("industry", ""))
        exposure = get_sector_exposure(sector)
        for name, val in macro_context.get("tier2", {}).items():
            wk = f"macro_{name}"
            if wk in weights:
                exp = exposure.get(name, 0)
                total += float(val) * exp * weights[wk]
                total_w += weights[wk]

        # ── Tier 3 个股级 ──
        for name in TIER3_INDICATORS:
            wk = f"macro_{name}"
            row_key = wk
            if wk in weights and row_key in row:
                total += float(row.get(row_key, 0)) * weights[wk]
                total_w += weights[wk]

    return total / max(total_w, 0.01) * 10


# ── M-4: Tier3 个股级指标预加载 ──

async def _preload_tier3_features(symbols: list[str], trade_date, session=None) -> dict[str, dict]:
    """预加载个股级 Tier3 指标 — 从 stock_fundamental_snapshot + moneyflow 批量查.

    一次查询覆盖所有 symbol, 避免逐股 SQL.
    无数据的指标填 0 (如 north_hold_chg/hk_hold 表未建).
    """
    result = {sym: {f"macro_{n}": 0.0 for n in TIER3_INDICATORS} for sym in symbols}

    if not symbols:
        return result

    if session:
        s_ctx = session
    else:
        from app.core.database import async_session_factory as _sf
        s_ctx = _sf()
        own_s = True

    try:
        # 1. 基本面: roe, roe_yoy(用profit_yoy代理), revenue_yoy
        r = await s_ctx.execute(text(
            "SELECT symbol, roe, revenue_yoy, profit_yoy "
            "FROM stock_fundamental_snapshot WHERE symbol = ANY(:syms)"
        ), {"syms": symbols})
        for row in r.fetchall():
            sym = row[0]
            result[sym]["macro_roe"] = round(float(row[1] or 0), 2)
            result[sym]["macro_roe_yoy"] = round(float(row[3] or 0), 2)  # profit_yoy as proxy
            result[sym]["macro_revenue_yoy"] = round(float(row[2] or 0), 2)

        # 2. 资金流: big_order_net = 大单净买入 (buy_lg - sell_lg), 近20日均值
        r = await s_ctx.execute(text(
            "SELECT ts_code, AVG(buy_lg_amount - sell_lg_amount) / 1e8 as big_net "
            "FROM moneyflow "
            "WHERE ts_code = ANY(:syms) AND trade_date >= CURRENT_DATE - 20 "
            "GROUP BY ts_code"
        ), {"syms": symbols})
        for row in r.fetchall():
            sym = row[0]
            result[sym]["macro_big_order_net"] = round(float(row[1] or 0), 4)

        # 3. gross_margin: 从 stock_fundamental_snapshot 无法直接获取, 保持 0

    except Exception as e:
        logger.debug(f"Tier3 preload partial: {e}")

    if not session:
        await s_ctx.close()

    return result


def _market_adjustment(phase: str) -> float:
    """市场状态修正系数 — 数据驱动: diff(全周期)/diff(phase)."""
    return {"bull": 0.8, "bear": 1.2, "range": 1.0, "unknown": 1.0}.get(phase, 1.0)


async def _compute_data_driven_adjustment() -> dict[str, float]:
    """从 learning_predictions 计算各市场阶段的 diff 比值."""
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT phase, PERCENTILE_CONT(0.8) WITHIN GROUP (ORDER BY excess_return) -
                   PERCENTILE_CONT(0.2) WITHIN GROUP (ORDER BY excess_return) as diff
            FROM learning_predictions lp
            JOIN market_status_log ms ON lp.scan_date = ms.trade_date
            WHERE lp.excess_return IS NOT NULL
            GROUP BY phase
        """))
        phase_diffs = {row[0]: float(row[1] or 0) for row in r.fetchall()}

    all_diff = sum(phase_diffs.values()) / max(len(phase_diffs), 1)
    if all_diff <= 0:
        return {"bull": 0.8, "bear": 1.2, "range": 1.0}

    result = {}
    for p in ["bull", "bear", "range"]:
        pd = phase_diffs.get(p, all_diff)
        if pd > 0:
            ratio = all_diff / pd
            result[p] = round(max(0.5, min(2.0, ratio)), 2)
        else:
            result[p] = 1.0
    return result


# ── K线路径进度追踪 ───────────────────────────
_kline_progress: dict[str, dict] = {}
_commodity_context_cache: dict = {}  # {date_str: {stock_code: comm_change_pct}}, max 60 entries
_CACHE_MAXSIZE = 60


async def _load_commodity_context(trade_date) -> dict[str, float]:
    """加载指定日期的商品期货→股票联动背景.

    返回 {stock_code: commodity_change_pct}. 未映射的股票不在结果中.
    """
    td_str = trade_date.isoformat() if hasattr(trade_date, 'isoformat') else trade_date
    if td_str in _commodity_context_cache:
        return _commodity_context_cache[td_str]

    async with async_session_factory() as s:
        # 该日期 vs 前一交易日的商品涨跌幅
        r = await s.execute(text("""
            SELECT c.ts_code, (c.close - prev.close) / NULLIF(prev.close, 0) * 100
            FROM commodity_futures c
            JOIN commodity_futures prev ON prev.ts_code = c.ts_code
                AND prev.trade_date = (SELECT MAX(trade_date) FROM commodity_futures
                                       WHERE ts_code = c.ts_code AND trade_date < c.trade_date)
            WHERE c.trade_date = :d
        """), {"d": trade_date})
        comm_changes = {row[0]: float(row[1] or 0) for row in r.fetchall()}

        # 2. 映射到A股
        r = await s.execute(text("""
            SELECT stock_code, commodity_code FROM commodity_stock_map
        """))
        result = {}
        for row in r.fetchall():
            stock, comm = row[0], row[1]
            if comm in comm_changes:
                result[stock] = round(comm_changes[comm], 2)

    _commodity_context_cache[td_str] = result
    if len(_commodity_context_cache) > _CACHE_MAXSIZE:
        oldest = sorted(_commodity_context_cache.keys())[0]
        del _commodity_context_cache[oldest]
    return result


async def _load_margin_context(trade_date) -> float:
    """加载指定日期的全市场融资情绪: (当日融资买入 - 20日均值) / 20日均值.

    正值=杠杆资金激进, 负值=杠杆资金谨慎.
    """
    async with async_session_factory() as s:
        # 分两步: 先取20日均值, 再拿当日值
        r = await s.execute(text("""
            SELECT AVG(rzmre) FROM (
                SELECT rzmre FROM margin_trading
                WHERE ts_code='700001.TI' AND trade_date < :d
                ORDER BY trade_date DESC LIMIT 20
            ) sub
        """), {"d": trade_date})
        avg_20 = r.scalar()
        if not avg_20 or avg_20 == 0:
            return 0

        r = await s.execute(text(
            "SELECT rzmre FROM margin_trading WHERE ts_code='700001.TI' AND trade_date=:d"
        ), {"d": trade_date})
        today_val = r.scalar()

    if today_val and avg_20:
        return round(float((today_val - avg_20) / avg_20), 3)
    return 0


async def _load_dragon_tiger_context(trade_date) -> tuple[dict, dict]:
    """加载指定日期的龙虎榜背景 (9维标签聚合).

    Returns:
      stock_ctx: {stock_code: inst_net_ratio} — 个股机构净占比 (-1~1)
      sector_ctx: {sector_name: avg_inst_ratio} — D4行业联动信号
    """
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT t.ts_code, (t.l_buy - t.l_sell) / NULLIF(t.l_buy + t.l_sell, 0) as inst_ratio
            FROM toplist_daily t WHERE t.trade_date = :d
        """), {"d": trade_date})
        rows = r.fetchall()

    stock_ctx = {row[0]: round(float(row[1] or 0), 3) for row in rows}
    toplist_codes = list(stock_ctx.keys())

    # D4行业联动: 上榜股票的申万行业 → 取机构净占比均值
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT tag_value, AVG(sub.inst_ratio) FROM (
                SELECT t.ts_code, (t.l_buy - t.l_sell) / NULLIF(t.l_buy + t.l_sell, 0) as inst_ratio
                FROM toplist_daily t WHERE t.trade_date = :d
            ) sub
            JOIN stock_dimension_tags sdt ON sdt.ts_code = sub.ts_code AND sdt.dim_name = 'sector'
            GROUP BY sdt.tag_value HAVING COUNT(*) >= 2
        """), {"d": trade_date})
        sector_ctx = {row[0]: round(float(row[1] or 0), 3) for row in r.fetchall()}

    return stock_ctx, sector_ctx  # key: "archetype/strategy", value: {total, done, current_sym}


async def run_training_round_from_kline(archetype, strategy, weights, lookback_days=600, phase_filter=None):
    """从 daily_kline 直接重建历史评分——用真实技术指标.

    每只股票逐日计算: 动量、波动率、量比、MA趋势 → 映射 0-10 分.
    200+ 天 × 全市场股票 → 产生有意义的训练信号.
    """
    horizon = FORECAST_HORIZONS[strategy]
    sw_code = _SECTOR_INDEX_MAP.get(archetype, "801010.SI")
    today = date.today()
    warmup = 60
    start = today - timedelta(days=lookback_days + warmup)
    predictions = []

    # ═══ Step 0: 按原型过滤股票（analysis_scores + scan_results L1补全） ═══
    async with async_session_factory() as s:
        # L2/L3 从 analysis_scores 取 (有完整维度分数); L1 从 scan_results 取 (基础K线训练)
        r = await s.execute(text(
            "SELECT DISTINCT symbol FROM analysis_scores WHERE SPLIT_PART(archetype, chr(95), 2) = :a AND scan_date >= :sd"
        ), {"a": archetype, "sd": today - timedelta(days=90)})
        arch_symbols = set(row[0] for row in r.fetchall())
        # L1 补全: 从 scan_results 取未被 analysis_scores 覆盖的股票
        r2 = await s.execute(text(
            "SELECT DISTINCT symbol FROM scan_results WHERE level = 'L1' AND scan_date >= :sd"
        ), {"sd": today - timedelta(days=90)})
        for row in r2.fetchall():
            arch_symbols.add(row[0])
    if not arch_symbols:
        return {"sharpe": 0, "auc": 0.5, "recall": 0, "predictions": 0}

    # ═══ Step 1: 加载该原型的K线 ═══
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT ts_code, trade_date, open, high, low, close, volume
            FROM daily_kline WHERE trade_date BETWEEN :d1 AND :d2 AND ts_code = ANY(:syms)
            ORDER BY ts_code, trade_date
        """), {"d1": start, "d2": today, "syms": list(arch_symbols)})
        all_rows = r.fetchall()

    # 按股票分组 → 计算滚动指标
    stock_series: dict[str, list[dict]] = {}
    for row in all_rows:
        sym = row[0]; td = row[1]
        o = float(row[2] or 0); h = float(row[3] or 0)
        l = float(row[4] or 0); c = float(row[5] or 0)
        v = float(row[6] or 0)
        if c <= 0: continue
        stock_series.setdefault(sym, []).append({"date": td, "open": o, "high": h, "low": l, "close": c, "volume": v})

    # ═══ Step 2: 每只股票计算滚动技术指标 ═══
    daily_data: dict[date, list[dict]] = {}
    scored_stocks = 0
    stocks_processed = 0
    total_stocks = len(stock_series)
    job_key = f"{archetype}/{strategy}"
    _kline_progress[job_key] = {"total": total_stocks, "done": 0, "current_sym": "", "iteration": 1}
    for sym, rows in stock_series.items():
        stocks_processed += 1
        _kline_progress[job_key]["done"] = stocks_processed
        _kline_progress[job_key]["current_sym"] = sym
        if stocks_processed % 50 == 0:
            await asyncio.sleep(0)  # 每50只股票释放事件循环，防止堵死
        rows.sort(key=lambda x: x["date"])
        closes = np.array([r["close"] for r in rows])
        volumes = np.array([r["volume"] for r in rows])
        n = len(rows)
        if n < warmup + 5: continue

        for i in range(warmup, n):
            td = rows[i]["date"]
            if td < today - timedelta(days=lookback_days):
                continue

            close = closes[i]; vol = volumes[i]
            prev_close = closes[i-1]

            # 收益率
            ret_1d = (close - prev_close) / prev_close * 100 if prev_close > 0 else 0
            ret_5d = (close - closes[i-5]) / closes[i-5] * 100 if closes[i-5] > 0 else ret_1d

            # 均线（固定窗口：ma5=5元素, ma10=10元素, ma20=20元素）
            ma5 = float(np.mean(closes[i-4:i+1])) if i >= 4 else close
            ma10 = float(np.mean(closes[i-9:i+1])) if i >= 9 else ma5
            ma20 = float(np.mean(closes[i-19:i+1])) if i >= 19 else ma10

            # 波动率 (20日年化)
            rets_20 = np.diff(closes[max(0,i-20):i+1]) / closes[max(0,i-20):i] * 100
            vol_20 = float(np.std(rets_20)) if len(rets_20) > 1 else 1.0

            # 量比
            avg_vol_20 = float(np.mean(volumes[max(0,i-20):i+1]))
            vol_ratio = vol / avg_vol_20 if avg_vol_20 > 0 else 1.0

            # ── 真实技术评分 (0-10) ──

            # tech_score: 短期动量强度 (ret_5d / vol_20 归一化)
            raw_tech = ret_5d / max(vol_20, 0.5)
            tech = round(float(np.clip(5.0 + raw_tech * 2, 0, 10)), 1)

            # kline_score: 价格相对均线位置
            if close > ma5 > ma10 > ma20: kline = 7.5
            elif close > ma5: kline = 6.5
            elif close > ma20: kline = 5.0
            elif close < ma5 < ma10: kline = 3.0
            elif close < ma20: kline = 4.0
            else: kline = 5.0

            # ma_trend_score: 均线斜率
            if i >= 15:
                ma_slope = (ma5 - float(np.mean(closes[i-15:i-10]))) / max(float(np.mean(closes[i-15:i-10])), 0.01) * 100
                ma_trend = round(float(np.clip(5.0 + ma_slope * 3, 0, 10)), 1)
            else:
                ma_trend = 5.0

            # pattern_score: 量价配合
            if ret_1d > 1.5 and vol_ratio > 1.5: pattern = 8.0      # 放量突破
            elif ret_1d > 0.5 and vol_ratio > 1.0: pattern = 6.5    # 温和上涨
            elif ret_1d < -1.5 and vol_ratio > 1.5: pattern = 2.0   # 放量暴跌
            elif ret_1d < -0.5 and vol_ratio < 0.7: pattern = 5.5   # 缩量阴跌
            elif ret_1d > 0 and vol_ratio < 0.5: pattern = 4.5      # 无量上涨
            else: pattern = 5.0

            daily_data.setdefault(td, []).append({
                "symbol": sym, "close": close, "change_pct": ret_1d,
                "volume": vol, "momentum": abs(ret_5d) * vol_ratio,
                "tech_score": tech, "kline_score": round(kline, 1),
                "fund_score": 0.0, "composite_score": 0.0,
                "vol_ratio_score": round(float(np.clip((vol_ratio - 1.0) * 3, -10, 10)), 1),
                "arbr_score": 0.0,
                "sector_alpha_score": 0.0,
                "valuation_score": 0.0, "ma_trend_score": ma_trend,
                "pattern_score": round(pattern, 1),
                "trend_deviation_score": 0.0,
                "bbi_score": 0.0,
                "box_score": 0.0,
            })
            scored_stocks += 1

    if not daily_data:
        return {"sharpe": 0, "auc": 0.5, "recall": 0, "predictions": 0}

    sorted_dates = sorted(daily_data.keys())

    # ═══ Step 3: 预加载指数 ═══
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT trade_date,close FROM daily_kline WHERE ts_code='700001.TI' AND trade_date BETWEEN :d1 AND :d2 ORDER BY trade_date"),
                            {"d1": sorted_dates[0], "d2": today + timedelta(days=horizon + 5)})
        idx_prices = {row[0]: float(row[1]) for row in r.fetchall()}
        r = await s.execute(text("SELECT trade_date,close FROM sw_sector_index WHERE index_code=:c AND trade_date BETWEEN :d1 AND :d2 ORDER BY trade_date"),
                            {"c": sw_code, "d1": sorted_dates[0], "d2": today + timedelta(days=horizon + 5)})
        sw_prices = {row[0]: float(row[1]) for row in r.fetchall()}

    # ═══ Step 3b: 逐股计算缺失维度（在指数加载后，有sw_prices） ═══
    from app.services.deep_scorer import score_bbi, score_trend_deviation, score_multi_box
    from app.services.deep_scorer import score_arbr, score_fund_flow, score_sector_alpha
    import pandas as pd
    sw_dates = sorted(sw_prices.keys())
    sector_5d_pct = 0.0
    if len(sw_dates) >= 6:
        sector_5d_pct = (sw_prices[sw_dates[-1]] - sw_prices[sw_dates[-6]]) / max(sw_prices[sw_dates[-6]], 0.01) * 100
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT symbol, pb, pe_ttm FROM stock_fundamental_snapshot WHERE symbol = ANY(:syms)"),
                            {"syms": list(stock_series.keys())})
        val_map = {row[0]: (float(row[1]) if row[1] else None, float(row[2]) if row[2] else None) for row in r.fetchall()}

    sym_scores: dict[str, dict] = {}
    syms_3b = list(stock_series.keys())
    total_3b = len(syms_3b)
    done_3b = 0
    for sym in syms_3b:
        rows = stock_series[sym]
        if len(rows) < 30:
            done_3b += 1
            continue
        done_3b += 1
        if done_3b % 50 == 0:
            _kline_progress[job_key]["done"] = total_3b + done_3b  # 让前端知道在进展中
            _kline_progress[job_key]["current_sym"] = sym
            _kline_progress[job_key]["phase"] = "维度计算" if _kline_progress.get(job_key, {}).get("iteration", 1) == 1 else _kline_progress[job_key].get("phase", "维度计算")
            await asyncio.sleep(0)
        rows_sorted = sorted(rows, key=lambda x: x["date"])
        df = pd.DataFrame({
            "Close": [r["close"] for r in rows_sorted], "Open": [r["open"] for r in rows_sorted],
            "High": [r["high"] for r in rows_sorted], "Low": [r["low"] for r in rows_sorted],
            "Volume": [r["volume"] for r in rows_sorted],
        })
        dims = {}
        r = score_bbi(df);
        if r: dims["bbi_score"] = r["score"]
        r = score_trend_deviation(df)
        if r: dims["trend_deviation_score"] = r["score"]
        r = score_multi_box(df)
        if r: dims["box_score"] = r["score"]
        r = score_arbr(df)
        if r: dims["arbr_score"] = r["score"]
        r = score_fund_flow(df)
        if r: dims["fund_score"] = r["score"]
        r = score_sector_alpha(df, sector_5d_pct)
        if r: dims["sector_alpha_score"] = r["score"]
        pb, pe = val_map.get(sym, (None, None))
        if pb is not None or pe is not None:
            from app.services.deep_scorer import score_valuation
            r = score_valuation(pb, pe)
            if r: dims["valuation_score"] = r["score"]
        if dims: sym_scores[sym] = dims

    for td, stocks in daily_data.items():
        for st in stocks:
            dims = sym_scores.get(st["symbol"], {})
            for k, v in dims.items():
                if k in st: st[k] = v

    # 预加载基准指数 + 原型映射
    benchmark_closes = await get_benchmark_closes(session=s)
    archetype_map = await _get_fingerprint_archetype_map()

    # ═══ Step 4: 逐日评分 + 预测 ═══
    for sd in sorted_dates:
        stocks = daily_data.get(sd, [])
        if len(stocks) < 30: continue
        ir = compute_excess_return(100.0, 100.0, sd, horizon, benchmark_closes)  # placeholder: compute_excess works differently
        # The benchmark return alone is NOT what we need. We need the ABSOLUTE index return.
        # Use compute_excess_return backtracked:
        future_dates = sorted(d for d in benchmark_closes if d > sd)
        if len(future_dates) >= horizon:
            c0 = benchmark_closes[future_dates[0]]
            cN = benchmark_closes[future_dates[horizon - 1]]
            ir = (cN - c0) / c0 * 100 if c0 > 0 else 0
        else:
            ir = 0

        # 加载该日期的商品期货 + 龙虎榜 + 融资情绪背景
        commodity_ctx = {}
        dt_stock_ctx, dt_sector_ctx = {}, {}
        margin_sentiment = 0
        if archetype in ("cyclical_resource",):
            commodity_ctx = await _load_commodity_context(sd)
        dt_stock_ctx, dt_sector_ctx = await _load_dragon_tiger_context(sd)
        margin_sentiment = await _load_margin_context(sd)

        # 按原型过滤 + 加权打分
        filtered = [s for s in stocks if archetype_map.get(s["symbol"], "unknown") == archetype]
        macro_ctx = await build_macro_context(sd, session=s)
        tier3_features = await _preload_tier3_features(
            [s["symbol"] for s in filtered], sd, session=s
        )
        for st in filtered:
            st.update(tier3_features.get(st["symbol"], {}))
            st["shadow_score"] = score_stock(st, weights, macro_ctx)
            sym = st["symbol"]
            # 商品联动修正
            comm_chg = commodity_ctx.get(sym, 0)
            if comm_chg != 0:
                stock_chg = st.get("change_pct", 0)
                if (comm_chg > 0.5 and stock_chg > 0) or (comm_chg < -0.5 and stock_chg < 0):
                    st["shadow_score"] *= 1.05
                elif (comm_chg > 1.0 and stock_chg < -1.0) or (comm_chg < -1.0 and stock_chg > 1.0):
                    st["shadow_score"] *= 0.90
            # 融资情绪修正: 杠杆资金激进→整体偏乐观, 谨慎→偏谨慎
            if abs(margin_sentiment) > 0.1:
                st["shadow_score"] *= 1.0 + margin_sentiment * 0.15  # ±15%封顶
            inst_r = dt_stock_ctx.get(sym, 0)
            if inst_r > 0.2:
                st["shadow_score"] *= 1.08
            elif inst_r < -0.2:
                st["shadow_score"] *= 0.92
            # 行业联动: 同申万行业上榜→小幅联动
            if inst_r == 0 and dt_sector_ctx:
                stock_sectors = (archetype_map.get(sym, "") or "").split(",")
                for s_name, s_ratio in dt_sector_ctx.items():
                    if any(s_name in ss for ss in stock_sectors):
                        st["shadow_score"] *= 1.03 if s_ratio > 0.15 else (0.97 if s_ratio < -0.15 else 1.0)
                        break
        filtered.sort(key=lambda x: x["shadow_score"], reverse=True)
        top = filtered[:50]
        if len(top) < 8: continue

        # 查 T+N 实际收益
        top_syms = [s["symbol"] for s in top[:20]]
        async with async_session_factory() as s2:
            r2 = await s2.execute(text("""SELECT ts_code,close FROM daily_kline
                WHERE ts_code=ANY(:syms) AND trade_date BETWEEN :d1 AND :d2 ORDER BY ts_code,trade_date"""),
                {"syms": top_syms, "d1": sd, "d2": sd + timedelta(days=horizon + 5)})
            klines = {}; [klines.setdefault(row[0],[]).append(float(row[1])) for row in r2.fetchall()]

        for st in top[:20]:
            prices = klines.get(st["symbol"], [])
            if len(prices) >= 2:
                buy = prices[0]; sell = prices[min(horizon, len(prices) - 1)]
                if buy > 0:
                    st["actual_return"] = round((sell - buy) / buy * 100, 2)
                    st["excess_return"] = round(st["actual_return"] - ir, 2)
                    st["scan_date"] = sd
                    predictions.append(st)

        # S3: 注入退市股负样本
        if strategy == "S3" and len(sorted_dates) > 0:
            mid_idx = len(sorted_dates) // 2
            if sd == sorted_dates[mid_idx]:
                async with async_session_factory() as s3:
                    r3 = await s3.execute(text("SELECT ts_code FROM delisted_stocks WHERE kline_count >= 200 ORDER BY RANDOM() LIMIT 5"))
                    dl_syms = [row[0] for row in r3.fetchall()]
                    if dl_syms:
                        r4 = await s3.execute(text("SELECT ts_code,close FROM daily_kline WHERE ts_code=ANY(:s) AND trade_date BETWEEN :d1 AND :d2 ORDER BY ts_code,trade_date"),
                                              {"s": dl_syms, "d1": sd, "d2": sd + timedelta(days=horizon + 5)})
                        dl_kl = {}; [dl_kl.setdefault(row[0],[]).append(float(row[1])) for row in r4.fetchall()]
                        for ts, prices in dl_kl.items():
                            if len(prices) >= 2:
                                ret = (prices[min(horizon,len(prices)-1)] - prices[0]) / prices[0] * 100
                                predictions.append({"symbol": ts, "actual_return": round(ret,2), "excess_return": round(ret,2),
                                                    "shadow_score": 30.0, "is_delisted": True})

    if phase_filter and predictions:
        # 按市场阶段过滤预测
        phase_map = await _get_market_phases(list(set(p["scan_date"] for p in predictions if "scan_date" in p)))
        predictions = [p for p in predictions if phase_map.get(p.get("scan_date"), "range") == phase_filter]
        logger.info(f"  kline phase_filter={phase_filter}: {len(predictions)} predictions after filtering")

    if len(predictions) < 20: return {"sharpe": 0, "auc": 0.5, "recall": 0, "predictions": len(predictions)}

    rets = [p["actual_return"] for p in predictions]
    excess = [p.get("excess_return", p["actual_return"]) for p in predictions]
    y_score = [p["shadow_score"] for p in predictions]

    mu = np.mean(excess); sigma = np.std(excess) if len(excess) > 1 else 1.0
    sharpe = mu / max(sigma, 0.5)

    y_true = [1 if r > 0 else 0 for r in rets]
    n_pos = sum(y_true); n_neg = len(y_true) - n_pos
    auc = 0.5
    if n_pos > 0 and n_neg > 0:
        ps = sorted([y_score[i] for i in range(len(y_score)) if y_true[i] == 1])
        ns = sorted([y_score[i] for i in range(len(y_score)) if y_true[i] == 0])
        concordant = 0; j = 0
        for p in ps:
            while j < len(ns) and ns[j] < p: j += 1
            concordant += j
        auc = concordant / (n_pos * n_neg)

    # S3 "同类"三分位桶
    actual_danger = [1 if r < -5 else 0 for r in rets]
    scores_for_bucket = [p.get("composite_score", p.get("shadow_score", 30)) for p in predictions]

    if len(scores_for_bucket) >= 9:
        p33 = np.percentile(scores_for_bucket, 33)
        p66 = np.percentile(scores_for_bucket, 66)
        buckets = {"low": [], "mid": [], "high": []}
        for i, s in enumerate(scores_for_bucket):
            if s < p33: buckets["low"].append(i)
            elif s < p66: buckets["mid"].append(i)
            else: buckets["high"].append(i)

        recall_parts = []
        for bucket_idx in buckets.values():
            if not bucket_idx: continue
            ad_b = [actual_danger[i] for i in bucket_idx]
            pd_b = [1 if y_score[i] < np.percentile([y_score[j] for j in bucket_idx], 20) else 0 for i in bucket_idx]
            tp_b = sum(1 for a, p in zip(ad_b, pd_b) if a == 1 and p == 1)
            recall_b = tp_b / max(1, sum(ad_b))
            recall_parts.append(recall_b)
        recall = np.mean(recall_parts) if recall_parts else 0
    else:
        cutoff = np.percentile(y_score, 20) if len(y_score) >= 5 else 0
        pred_danger = [1 if s < cutoff else 0 for s in y_score]
        tp = sum(1 for a, p in zip(actual_danger, pred_danger) if a == 1 and p == 1)
        recall = tp / max(1, sum(actual_danger))

    # 写入 learning_predictions
    if predictions:
        async with async_session_factory() as s:
            for p in predictions[:50]:
                await s.execute(text("""INSERT INTO learning_predictions
                    (scan_date, symbol, archetype, strategy, model_version, predicted_score, actual_return, excess_return, was_correct)
                    VALUES (:sd, :sym, :a, :st, 'kline', :ps, :ar, :er, :wc)"""),
                    {"sd": sd, "sym": p.get("symbol","?"), "a": archetype, "st": strategy,
                     "ps": p.get("shadow_score",0), "ar": p.get("actual_return",0),
                     "er": p.get("excess_return",0), "wc": p.get("actual_return",0) > 0})
            await s.commit()

    # 区分度: top half avg return - bottom half avg return (Phase E 独立计算)
    sorted_idx = sorted(range(len(rets)), key=lambda i: y_score[i], reverse=True)
    mid = len(sorted_idx) // 2
    top_avg = sum(rets[sorted_idx[i]] for i in range(mid)) / mid if mid > 0 else 0
    bottom_avg = sum(rets[sorted_idx[i]] for i in range(mid, len(sorted_idx))) / max(len(sorted_idx)-mid, 1)
    discrimination = top_avg - bottom_avg

    return {"sharpe": round(sharpe, 4), "auc": round(auc, 4), "recall": round(recall, 4),
            "discrimination": round(discrimination, 4),
            "mean_return": round(mu, 2), "predictions": len(predictions), "weights": weights}


async def run_training_round(archetype, strategy, scan_dates, weights):
    horizon = FORECAST_HORIZONS[strategy]
    sw_code = _SECTOR_INDEX_MAP.get(archetype, "801010.SI")
    all_dates = sorted(scan_dates)
    d1, d2 = all_dates[0], all_dates[-1] + timedelta(days=horizon + 5)
    predictions = []

    async with async_session_factory() as s:
        r = await s.execute(text("SELECT trade_date,close FROM daily_kline WHERE ts_code='700001.TI' AND trade_date BETWEEN :d1 AND :d2 ORDER BY trade_date"), {"d1": d1, "d2": d2})
        idx_prices = {row[0]: float(row[1]) for row in r.fetchall()}
        r = await s.execute(text("SELECT trade_date,close FROM sw_sector_index WHERE index_code=:c AND trade_date BETWEEN :d1 AND :d2 ORDER BY trade_date"), {"c": sw_code, "d1": d1, "d2": d2})
        sw_prices = {row[0]: float(row[1]) for row in r.fetchall()}

    # 指数收益 (交易日计数, 修正日历日 bug)
    idx_dates = sorted(idx_prices.keys())
    def _index_ret(sd, h):
        """交易日计数: sd 之后的第 h 个交易日."""
        future = [d for d in idx_dates if d > sd]
        if len(future) >= h:
            c0 = idx_prices[future[0]]
            cN = idx_prices[future[h - 1]]
            return (cN - c0) / c0 * 100 if c0 > 0 else 0
    for sd in scan_dates:
        ir = _index_ret(sd, horizon)
        async with async_session_factory() as s:
            r = await s.execute(text("SELECT symbol,tech_score,kline_score,fund_score,composite_score,dimension_scores FROM analysis_scores WHERE scan_date=:d AND SPLIT_PART(archetype, '_', 2) = :a ORDER BY composite_score DESC LIMIT 100"), {"d": sd, "a": archetype})
            rows = r.fetchall()

        stocks = []
        for row in rows:
            sym = row[0]
            # 从 dimension_scores JSONB 加载完整维度分数（10维+增强维）
            dim_scores = row[5] if len(row) > 5 and row[5] else {}
            if isinstance(dim_scores, str):
                try: dim_scores = json.loads(dim_scores)
                except Exception: dim_scores = {}
            st = {"symbol": sym, "tech_score": float(row[1] or 0), "kline_score": float(row[2] or 0),
                  "fund_score": float(row[3] or 0), "composite_score": float(row[4] or 0) / 10}
            # 合并维度分数（覆盖同名键，使用DB中的精确值）
            st.update(dim_scores)
            stocks.append(st)
        if len(stocks) < 5: continue

        macro_ctx = await build_macro_context(sd, session=s)
        tier3_features = await _preload_tier3_features(
            [s["symbol"] for s in stocks], sd, session=s
        )
        for st in stocks:
            st.update(tier3_features.get(st["symbol"], {}))
            st["shadow_score"] = score_stock(st, weights, macro_ctx)
        stocks.sort(key=lambda x: x["shadow_score"], reverse=True)

        top_syms = [s["symbol"] for s in stocks[:20]]
        async with async_session_factory() as s2:
            r2 = await s2.execute(text("SELECT ts_code,close FROM daily_kline WHERE ts_code=ANY(:syms) AND trade_date BETWEEN :d1 AND :d2 ORDER BY ts_code,trade_date"),
                                  {"syms": top_syms, "d1": sd, "d2": sd + timedelta(days=horizon + 5)})
            klines = {}; [klines.setdefault(row[0], []).append(float(row[1])) for row in r2.fetchall()]

        for st in stocks[:20]:
            prices = klines.get(st["symbol"], [])
            if len(prices) >= 2:
                buy = prices[0]; sell = prices[min(horizon, len(prices) - 1)]
                if buy > 0:
                    st["actual_return"] = round((sell - buy) / buy * 100, 2)
                    st["excess_return"] = round(st["actual_return"] - ir, 2)
                    st["scan_date"] = sd
                    predictions.append(st)

        if strategy == "S3" and len(scan_dates) > 0 and sd == scan_dates[len(scan_dates)//2]:
            async with async_session_factory() as s3:
                r3 = await s3.execute(text("SELECT ts_code FROM delisted_stocks WHERE kline_count >= 200 ORDER BY RANDOM() LIMIT 5"))
                dl_syms = [row[0] for row in r3.fetchall()]
                if dl_syms:
                    r4 = await s3.execute(text("SELECT ts_code,close FROM daily_kline WHERE ts_code=ANY(:s) AND trade_date BETWEEN :d1 AND :d2 ORDER BY ts_code,trade_date"),
                                          {"s": dl_syms, "d1": sd, "d2": sd + timedelta(days=horizon + 5)})
                    dl_kl = {}; [dl_kl.setdefault(row[0],[]).append(float(row[1])) for row in r4.fetchall()]
                    for ts, prices in dl_kl.items():
                        if len(prices) >= 2:
                            ret = (prices[min(horizon,len(prices)-1)] - prices[0]) / prices[0] * 100
                            predictions.append({"symbol": ts, "actual_return": round(ret,2), "excess_return": round(ret,2),
                                                "shadow_score": 30.0, "is_delisted": True})

    if len(predictions) < 10: return {"sharpe": 0, "auc": 0.5, "recall": 0, "predictions": len(predictions)}

    rets = [p["actual_return"] for p in predictions]
    excess = [p.get("excess_return", p["actual_return"]) for p in predictions]
    y_score = [p["shadow_score"] for p in predictions]

    mu = np.mean(excess); sigma = np.std(excess) if len(excess) > 1 else 1.0
    sharpe = mu / max(sigma, 0.5)

    y_true = [1 if r > 0 else 0 for r in rets]
    n_pos = sum(y_true); n_neg = len(y_true) - n_pos
    auc = 0.5
    if n_pos > 0 and n_neg > 0:
        ps = sorted([y_score[i] for i in range(len(y_score)) if y_true[i] == 1])
        ns = sorted([y_score[i] for i in range(len(y_score)) if y_true[i] == 0])
        concordant = 0; j = 0
        for p in ps:
            while j < len(ns) and ns[j] < p: j += 1
            concordant += j
        auc = concordant / (n_pos * n_neg)

    actual_danger = [1 if r < -5 else 0 for r in rets]
    scores_for_bucket = [p.get("composite_score", p.get("shadow_score", 30)) for p in predictions]

    if len(scores_for_bucket) >= 9:
        p33 = np.percentile(scores_for_bucket, 33)
        p66 = np.percentile(scores_for_bucket, 66)
        buckets = {"low": [], "mid": [], "high": []}
        for i, s in enumerate(scores_for_bucket):
            if s < p33: buckets["low"].append(i)
            elif s < p66: buckets["mid"].append(i)
            else: buckets["high"].append(i)

        recall_parts = []
        for bucket_idx in buckets.values():
            if not bucket_idx: continue
            ad_b = [actual_danger[i] for i in bucket_idx]
            pd_b = [1 if y_score[i] < np.percentile([y_score[j] for j in bucket_idx], 20) else 0 for i in bucket_idx]
            tp_b = sum(1 for a, p in zip(ad_b, pd_b) if a == 1 and p == 1)
            recall_b = tp_b / max(1, sum(ad_b))
            recall_parts.append(recall_b)
        recall = np.mean(recall_parts) if recall_parts else 0
    else:
        cutoff = np.percentile(y_score, 20) if len(y_score) >= 5 else 0
        pred_danger = [1 if s < cutoff else 0 for s in y_score]
        tp = sum(1 for a, p in zip(actual_danger, pred_danger) if a == 1 and p == 1)
        recall = tp / max(1, sum(actual_danger))

    if predictions:
        async with async_session_factory() as s:
            for p in predictions[:50]:
                await s.execute(text("""INSERT INTO learning_predictions
                    (scan_date, symbol, archetype, strategy, model_version,
                     predicted_score, predicted_return, actual_return, excess_return, was_correct, risk_label)
                    VALUES (:sd, :sym, :a, :st, :v, :ps, :pr, :ar, :er, :wc, :rl)"""),
                    {"sd": scan_dates[-1] if scan_dates else date.today(), "sym": p.get("symbol","?"),
                     "a": archetype, "st": strategy, "v": "shadow",
                     "ps": p.get("shadow_score",0), "pr": p.get("shadow_score",0)/10,
                     "ar": p.get("actual_return",0), "er": p.get("excess_return",0),
                     "wc": p.get("actual_return",0) > 0, "rl": "danger" if p.get("actual_return",0) < -5 else ""})
            await s.commit()

    # 区分度: top half avg return - bottom half avg return (Phase E 独立计算)
    sorted_idx = sorted(range(len(rets)), key=lambda i: y_score[i], reverse=True)
    mid = len(sorted_idx) // 2
    top_avg = sum(rets[sorted_idx[i]] for i in range(mid)) / mid if mid > 0 else 0
    bottom_avg = sum(rets[sorted_idx[i]] for i in range(mid, len(sorted_idx))) / max(len(sorted_idx)-mid, 1)
    discrimination = top_avg - bottom_avg

    return {"sharpe": round(sharpe, 4), "auc": round(auc, 4), "recall": round(recall, 4),
            "discrimination": round(discrimination, 4),
            "mean_return": round(mu, 2), "predictions": len(predictions), "weights": weights}


async def _get_max_discrimination(session, archetype: str, strategy: str) -> float:
    """Phase 73: 查询 param_library 中该(archetype,strategy)的历史最大 discrimination 原始值."""
    r = await session.execute(text(
        "SELECT MAX(discrimination) FROM param_library "
        "WHERE archetype=:a AND strategy=:st AND is_shadow=true"
    ), {"a": archetype, "st": strategy})
    val = r.scalar()
    return float(val) if val else None


def _normalize_disc(raw_val: float, max_observed: float | None) -> float:
    """Phase 73: min-max 归一化到 [0,1]。max_observed=None 时直接返回原值不归一化."""
    if max_observed is None or max_observed <= 0:
        return round(raw_val, 4)  # 第一轮: 无历史参考, 保留原值
    # Clamp: 永远不超过历史最大, 保持跨轮稳定
    return round(max(0.0, min(1.0, raw_val / max_observed)), 4)


async def train_shadow(archetype, strategy="S2", n_iterations=20, progress_cb=None, validation_split=0.2, lookback_days=500):
    import time; _t0 = time.time()
    logger.info(f"Training {archetype}/{strategy} started (iterations={n_iterations})")
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT scoring_weights,discrimination FROM param_library WHERE archetype=:a AND strategy=:st AND is_shadow=true ORDER BY created_at DESC LIMIT 1"), {"a": archetype, "st": strategy})
        row = r.fetchone()
    current_weights = await _get_active_weights()
    best_score = -float('inf')
    if row and row[0]:
        saved = row[0] if isinstance(row[0], dict) else {}
        prev_disc = float(row[1] or 0) if row[1] else 0.0
        if saved and prev_disc is not None:
            current_weights = saved
            best_score = prev_disc

    concept_map = await _get_concept_map()
    horizon = FORECAST_HORIZONS[strategy]
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT DISTINCT scan_date FROM analysis_scores WHERE scan_date>=CURRENT_DATE-180 ORDER BY scan_date"))
        all_dates = [row[0] for row in r.fetchall()]

    today = date.today()
    verifiable_dates = [d for d in all_dates if d + timedelta(days=horizon) <= today]

    use_kline = True  # 默认走K线 (600天历史数据)
    # v4.5: 当有足够的可验证日期时，优先走 analysis_scores 丰富特征路径
    if len(verifiable_dates) >= 3:
        use_kline = False  # 用 rich scores from analysis_scores
        logger.info(f"Training {archetype}/{strategy} via SCORES path ({len(verifiable_dates)} verifiable dates)")
    if use_kline:
        async with async_session_factory() as s:
            r = await s.execute(text(
                "SELECT COUNT(*) FROM analysis_scores WHERE SPLIT_PART(archetype, chr(95), 2) = :a AND scan_date = ANY(:ds)"
            ), {"a": archetype, "ds": verifiable_dates})
            total = r.scalar() or 0
        if total < 30:
            logger.info(f"Archetype {archetype} has only {total} verifiable scores, using kline path")
            use_kline = True

    if use_kline:
        metric_key = {"S1": "auc", "S2": "sharpe", "S3": "recall"}.get(strategy, "sharpe")
        logger.info(f"Training {archetype}/{strategy} via kline path (600天, phase-split)")

        # K线路径也按阶段拆分
        all_kline_results = {}
        for train_phase in ["bull", "bear", "range"]:
            no_improve_streak = 0
            kline_weights = dict(current_weights)
            kline_best = -float('inf')
            logger.info(f"  kline/{train_phase}: starting optimization...")

            for it in range(n_iterations):
                job_key = f"{archetype}/{strategy}"
                if job_key in _kline_progress:
                    _kline_progress[job_key]["iteration"] = it + 1
                    _kline_progress[job_key]["phase"] = train_phase

                candidates = generate_candidates(kline_weights, 3) + [kline_weights]
                best_result = None
                for cand in candidates:
                    result = await run_training_round_from_kline(archetype, strategy, cand, lookback_days=lookback_days, phase_filter=train_phase)
                    if best_result is None or result.get(metric_key, 0) > best_result.get(metric_key, 0):
                        best_result = result

                improved = best_result and best_result.get(metric_key, 0) > kline_best
                if improved:
                    kline_best = best_result[metric_key]
                    kline_weights = best_result.get("weights", kline_weights)
                    no_improve_streak = 0
                else:
                    no_improve_streak += 1

                async with async_session_factory() as s:
                    # Phase 73: 归一化 discrimination
                    max_obs = await _get_max_discrimination(s, archetype, strategy)
                    disc_val = _normalize_disc(best_result.get("discrimination", 0) if best_result else 0, max_obs)
                    await s.execute(text("""INSERT INTO param_library (id,archetype,strategy,is_shadow,scoring_weights,backtest_accuracy,discrimination,converge_status,last_trained_at,n_selections,version,is_active,month,market_style,created_at,updated_at) VALUES (gen_random_uuid(),:a,:st,true,CAST(:w AS jsonb),:acc,:disc,:conv,NOW(),:n,:v,false,1,:ms,NOW(),NOW())"""),
                        {"a": archetype, "st": strategy, "w": json.dumps(kline_weights), "acc": round(kline_best,4), "disc": round(disc_val,4), "conv": "converged" if no_improve_streak >= 8 else "training", "n": best_result.get("predictions", 0) if best_result else 0, "v": f"k-{today.strftime('%y%m%d')}-{train_phase}-{it}-{int(_time.time()*1000)%100000}", "ms": train_phase})
                    await s.commit()

                if progress_cb:
                    await progress_cb(it + 1, n_iterations, kline_best, best_result.get("predictions", 0) if best_result else 0)
                if no_improve_streak >= 8:
                    break

            all_kline_results[train_phase] = {"best_score": round(kline_best, 4), "weights": kline_weights}
            logger.info(f"  kline/{train_phase} done: score={round(kline_best,4)}")

        logger.info(f"Training {archetype}/{strategy} done: {len(all_kline_results)} phases trained, mode=kline elapsed={round(time.time()-_t0,1)}s")
        return {"status": "success", "archetype": archetype, "strategy": strategy,
                "iterations": n_iterations, "metric": metric_key,
                "phases": all_kline_results,
                "mode": "kline", "elapsed_s": round(time.time()-_t0, 1),
                "algorithm": "bayesian_optimization_per_phase_kline"}

    logger.info(f"Training {archetype}/{strategy} on {len(verifiable_dates)} verifiable dates")

    phase_map = await _get_market_phases(verifiable_dates)
    phase_dist = {}
    for d in verifiable_dates:
        p = phase_map.get(d, "range")
        phase_dist[p] = phase_dist.get(p, 0) + 1
    logger.info(f"Phase distribution: {phase_dist}")

    metric_key = {"S1": "auc", "S2": "sharpe", "S3": "recall"}.get(strategy, "sharpe")
    all_phase_results = {}

    # ── 按市场阶段分别训练 ──
    for train_phase in ["bull", "bear", "range"]:
        phase_dates = sorted([d for d in verifiable_dates if phase_map.get(d, "range") == train_phase])
        if len(phase_dates) < 4:
            logger.info(f"  {train_phase}: {len(phase_dates)} dates, insufficient, skip")
            continue

        split_idx = int(len(phase_dates) * 0.8)
        train_dates, val_dates = phase_dates[:split_idx], phase_dates[split_idx:]
        if len(train_dates) < 3:
            continue

        logger.info(f"  {train_phase}: {len(train_dates)} train + {len(val_dates)} val dates, optimizing...")

        # 加载已有权重作为初始值（阶段特定 > 全阶段回退 > 默认）
        phase_weights = dict(DEFAULT_WEIGHTS)
        try:
            async with async_session_factory() as s:
                # 先找该阶段的已有权重
                r = await s.execute(text(
                    "SELECT scoring_weights FROM param_library WHERE archetype=:a AND strategy=:st"
                    " AND is_shadow=true AND market_style=:ms ORDER BY created_at DESC LIMIT 1"
                ), {"a": archetype, "st": strategy, "ms": train_phase})
                row = r.fetchone()
                if not row:
                    # 回退: 找任意阶段的最近权重
                    r = await s.execute(text(
                        "SELECT scoring_weights FROM param_library WHERE archetype=:a AND strategy=:st"
                        " AND is_shadow=true AND converge_status != 'upgraded' ORDER BY created_at DESC LIMIT 1"
                    ), {"a": archetype, "st": strategy})
                    row = r.fetchone()
                if row and row[0]:
                    loaded = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                    if loaded and isinstance(loaded, dict):
                        phase_weights.update(loaded)
        except Exception:
            pass
        best_score = -float('inf')
        no_improve_streak = 0
        overfit_warning = False
        phase_metrics = {}
        X_observed, y_observed = [], []
        ei_stop_streak = 0
        bounds = [(0.1, 5.0) for _ in DEFAULT_WEIGHTS]
        kernel = ConstantKernel(1.0) * Matern(length_scale=np.ones(len(DEFAULT_WEIGHTS)), nu=2.5) + WhiteKernel(noise_level=0.01)
        gp = GaussianProcessRegressor(kernel=kernel, n_restarts_optimizer=5, normalize_y=True)

        for it in range(n_iterations):
            if it < N_RANDOM_STARTS:
                candidates = generate_candidates(phase_weights, 3)
            else:
                if len(X_observed) < 5:
                    candidates = generate_candidates(phase_weights, 3)
                else:
                    X_arr = np.array(X_observed); y_arr = np.array(y_observed)
                    gp.fit(X_arr, y_arr)
                    x_next = _propose_next(gp, phase_weights, max(y_arr), bounds)
                    candidates = [_vec_to_weights(np.clip(x_next, *zip(*bounds)), phase_weights)]
                    candidates.append(phase_weights)
                    ei_val = _expected_improvement(_weights_to_vec(phase_weights), gp, max(y_arr))
                    if ei_val < max(y_arr) * EI_STOP_THRESHOLD:
                        ei_stop_streak += 1
                        if ei_stop_streak >= 15:
                            logger.info(f"  {train_phase} BO early stop at iter {it}")
                            break
                    else:
                        ei_stop_streak = 0

            best_result = None
            for cand in candidates:
                result = await run_training_round(archetype, strategy, train_dates, cand)
                if best_result is None or result.get(metric_key, 0) > best_result.get(metric_key, 0):
                    best_result = result

            improved = best_result and best_result.get(metric_key, 0) > best_score
            if improved:
                best_score = best_result[metric_key]
                phase_weights = best_result.get("weights", phase_weights)
                no_improve_streak = 0
                X_observed.append(_weights_to_vec(phase_weights))
                y_observed.append(best_score)
            else:
                no_improve_streak += 1

            if val_dates and it % 10 == 0:
                val_result = await run_training_round(archetype, strategy, val_dates, phase_weights)
                ts = best_result.get(metric_key, 0) if best_result else 0
                vs = val_result.get(metric_key, 0)
                if vs > 0 and ts > 0 and (ts - vs) / vs > 0.3:
                    overfit_warning = True

            async with async_session_factory() as s:
                # Phase 73: 归一化 discrimination
                max_obs = await _get_max_discrimination(s, archetype, strategy)
                disc_val = _normalize_disc(best_result.get("discrimination", 0) if best_result else 0, max_obs)
                await s.execute(text("""INSERT INTO param_library (id,archetype,strategy,is_shadow,scoring_weights,backtest_accuracy,discrimination,converge_status,last_trained_at,n_selections,version,is_active,month,market_style,created_at,updated_at) VALUES (gen_random_uuid(),:a,:st,true,CAST(:w AS jsonb),:acc,:disc,:conv,NOW(),:n,:v,false,1,:ms,NOW(),NOW())"""),
                    {"a": archetype, "st": strategy, "w": json.dumps(phase_weights),
                     "acc": round(best_score, 4), "disc": round(disc_val, 4),
                     "conv": "overfit" if overfit_warning else ("converged" if no_improve_streak >= 8 else "training"),
                     "n": best_result.get("predictions", 0) if best_result else 0,
                     "v": f"{today.strftime('%Y%m%d')}-{train_phase}-{it}-{int(_time.time()) % 10000}",
                     "ms": train_phase})  # bull/bear/range — 干净值，供面板查询
                await s.commit()

            if progress_cb:
                await progress_cb(it + 1, n_iterations, best_score, best_result.get("predictions", 0) if best_result else 0)
            if no_improve_streak >= 8:
                break

        all_phase_results[train_phase] = {
            "best_score": round(best_score, 4),
            "weights": phase_weights,
            "train_dates": len(train_dates),
            "val_dates": len(val_dates),
            "converged": no_improve_streak >= 8,
            "overfit": overfit_warning,
        }
        logger.info(f"  {train_phase} done: score={round(best_score,4)} converged={no_improve_streak>=15}")

    logger.info(f"Training {archetype}/{strategy} done: {len(all_phase_results)} phases trained, "
                f"mode=analysis_scores elapsed={round(time.time()-_t0,1)}s")
    return {"status": "success", "archetype": archetype, "strategy": strategy,
            "iterations": n_iterations, "metric": metric_key,
            "phases": all_phase_results,
            "mode": "analysis_scores",
            "elapsed_s": round(time.time()-_t0, 1),
            "algorithm": "bayesian_optimization_per_phase"}


async def evaluate_shadow_vs_main() -> dict:
    """Compare shadow model weights vs active main weights, auto-switch if shadow wins.

    Called weekly (Sunday) by scheduler/weekly_tasks.py.
    Returns verdict + list of archetypes auto-switched to shadow.
    """
    async with async_session_factory() as s:
        r_main = await s.execute(text("""
            SELECT archetype, strategy, discrimination
            FROM param_library
            WHERE is_shadow = false AND is_active = true
        """))
        mains = {}
        for row in r_main.fetchall():
            mains[f"{row[0]}/{row[1]}"] = float(row[2] or 0)

        r_shadow = await s.execute(text("""
            SELECT archetype, strategy, discrimination
            FROM param_library WHERE is_shadow = true
        """))
        shadows = {}
        for row in r_shadow.fetchall():
            key = f"{row[0]}/{row[1]}"
            val = float(row[2] or 0)
            if key not in shadows or val > shadows[key]:
                shadows[key] = val

    comparisons = {}
    auto_switched = []

    for key in set(list(mains.keys()) + list(shadows.keys())):
        main_disc = mains.get(key, 0)
        shadow_disc = shadows.get(key, 0)
        delta = shadow_disc - main_disc
        comparisons[key] = {
            "main_disc": main_disc,
            "shadow_disc": shadow_disc,
            "delta": round(delta, 4),
        }
        if delta > 0.05 and shadow_disc > 0.5:
            arch, st = key.split("/")
            try:
                async with async_session_factory() as s2:
                    r_ver = await s2.execute(text("""
                        SELECT version FROM param_library
                        WHERE archetype = :a AND strategy = :st AND is_shadow = true
                        ORDER BY discrimination DESC LIMIT 1
                    """), {"a": arch, "st": st})
                    best_ver = r_ver.scalar()
                    if best_ver:
                        await s2.execute(text(
                            "UPDATE param_library SET is_active = false "
                            "WHERE archetype = :a AND strategy = :st AND is_shadow = false"
                        ), {"a": arch, "st": st})
                        await s2.execute(text(
                            "UPDATE param_library SET is_active = true "
                            "WHERE archetype = :a AND strategy = :st AND version = :v"
                        ), {"a": arch, "st": st, "v": best_ver})
                        await s2.commit()
                        auto_switched.append(key)
                        logger.info(
                            f"Shadow auto-switch: {key} "
                            f"(main {main_disc:.3f} → shadow {shadow_disc:.3f})"
                        )
            except Exception as e:
                logger.warning(f"Auto-switch {key} failed: {e}")

    if auto_switched:
        verdict = "shadow_better"
    elif any(c["delta"] < -0.05 for c in comparisons.values()):
        verdict = "main_better"
    else:
        verdict = "no_change"

    logger.info(f"Shadow eval: {verdict}, switched={len(auto_switched)}, "
                f"archs_compared={len(comparisons)}")
    return {"verdict": verdict, "auto_switched": auto_switched, "comparisons": comparisons}