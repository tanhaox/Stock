"""12 维股票指纹构建器(含均线趋势质量).

从 stock_fundamental_snapshot + daily_kline + moneyflow 等表批量提取特征，
每维输出 0-10 分，构成 12 维浮点向量。

维度:
  1. 市值定位     — 对数市值归一化
  2. 流动性剖面   — 换手率 + 振幅
  3. 估值坐标     — PE/PB 分位
  4. 资金流向     — 大单净流入
  5. 杠杆资金     — 融资余额变化
  6. 基本面质量   — ROE + 营收增速 + 负债率 + 现金流
  7. 筹码结构     — 股东户数变化
  8. 筹码分布     — 成交量剖面(未实现)
  9. 特殊标签     — A+H、行业稀缺性
  10. 事件驱动    — 业绩跳变
  11. 跨市场影响  — 行业相对强度
  12. 趋势质量    — 8-21-55-144-250 EMA五线体系综合评分
"""
import logging
from datetime import date, timedelta
from typing import Optional
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger(__name__)

# 评分范围
FINGERPRINT_RANGE = (0.0, 10.0)
NEUTRAL = 5.0

# ── 批量预加载 ────────────────────────────────────

async def preload_snapshot_data(symbols: list[str]) -> dict[str, dict]:
    """批量加载基本面快照数据."""
    if not symbols:
        return {}
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT symbol, roe, revenue_yoy, profit_yoy, debt_to_assets,
                   current_ratio, ocflow_net, pb, pe_ttm
            FROM stock_fundamental_snapshot
            WHERE symbol = ANY(:syms)
        """), {"syms": symbols})
        return {row[0]: {
            "roe": float(row[1]) if row[1] is not None else None,
            "revenue_yoy": float(row[2]) if row[2] is not None else None,
            "profit_yoy": float(row[3]) if row[3] is not None else None,
            "debt_to_assets": float(row[4]) if row[4] is not None else None,
            "current_ratio": float(row[5]) if row[5] is not None else None,
            "ocflow_net": float(row[6]) if row[6] is not None else None,
            "pb": float(row[7]) if row[7] is not None else None,
            "pe_ttm": float(row[8]) if row[8] is not None else None,
        } for row in r.fetchall()}


async def preload_market_data(symbols: list[str]) -> dict[str, dict]:
    """批量加载市值、换手率等日频数据."""
    if not symbols:
        return {}
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT DISTINCT ON (ts_code)
                ts_code, total_mv, turnover_rate, pe_ttm, pb
            FROM daily_basic
            WHERE ts_code = ANY(:syms)
            ORDER BY ts_code, trade_date DESC
        """), {"syms": symbols})
        return {row[0]: {
            "total_mv": float(row[1]) if row[1] is not None else None,
            "turnover_rate": float(row[2]) if row[2] is not None else None,
            "pe_ttm": float(row[3]) if row[3] is not None else None,
            "pb": float(row[4]) if row[4] is not None else None,
        } for row in r.fetchall()}


async def preload_moneyflow(symbols: list[str], days: int = 10) -> dict[str, dict]:
    """批量加载近期资金流向(金额归一化)."""
    if not symbols:
        return {}
    cutoff = date.today() - timedelta(days=days)
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT ts_code,
                   AVG(buy_elg_amount - sell_elg_amount) as net_elg_amount,
                   AVG(buy_lg_amount - sell_lg_amount) as net_lg_amount
            FROM moneyflow
            WHERE ts_code = ANY(:syms) AND trade_date >= :cut
            GROUP BY ts_code
        """), {"syms": symbols, "cut": cutoff})
        return {row[0]: {
            "net_elg_amount": float(row[1]) if row[1] else 0.0,
            "net_lg_amount": float(row[2]) if row[2] else 0.0,
        } for row in r.fetchall()}


async def preload_market_cap_for_normalization(symbols: list[str]) -> dict[str, float]:
    """批量加载总市值(万元)用于资金流归一化."""
    if not symbols:
        return {}
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT DISTINCT ON (ts_code) ts_code, total_mv
            FROM daily_basic
            WHERE ts_code = ANY(:syms)
            ORDER BY ts_code, trade_date DESC
        """), {"syms": symbols})
        return {row[0]: float(row[1]) if row[1] is not None else 0.0 for row in r.fetchall()}


async def preload_margin_data(symbols: list[str], days: int = 20) -> dict[str, dict]:
    """批量加载融资融券数据."""
    if not symbols:
        return {}
    cutoff = date.today() - timedelta(days=days)
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT ts_code,
                   AVG(rzye) as avg_rz,
                   MAX(rzye) - MIN(rzye) as rz_change
            FROM margin_trading
            WHERE ts_code = ANY(:syms) AND trade_date >= :cut
            GROUP BY ts_code
        """), {"syms": symbols, "cut": cutoff})
        return {row[0]: {
            "avg_rz_balance": float(row[1]) if row[1] is not None else 0,
            "rz_change": float(row[2]) if row[2] is not None else 0,
        } for row in r.fetchall()}


async def preload_kline_metrics(symbols: list[str], days: int = 60) -> dict[str, dict]:
    """批量加载 K 线衍生指标(波动率、动量、振幅等)."""
    if not symbols:
        return {}
    cutoff = date.today() - timedelta(days=days)
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT ts_code,
                   AVG((high - low) / NULLIF(close, 0)) * 100 as avg_amplitude,
                   (MAX(close) - MIN(close)) / NULLIF(MIN(close), 0) * 100 as momentum,
                   AVG(volume) as avg_volume,
                   AVG(close) as avg_close,
                   STDDEV(daily_return) as volatility
            FROM (
                SELECT ts_code, trade_date, high, low, close, volume,
                       (close - LAG(close) OVER (PARTITION BY ts_code ORDER BY trade_date))
                       / NULLIF(LAG(close) OVER (PARTITION BY ts_code ORDER BY trade_date), 0) as daily_return
                FROM daily_kline
                WHERE ts_code = ANY(:syms) AND trade_date >= :cut
            ) sub
            GROUP BY ts_code
        """), {"syms": symbols, "cut": cutoff})
        return {row[0]: {
            "avg_amplitude": float(row[1]) if row[1] is not None else 0,
            "momentum": float(row[2]) if row[2] is not None else 0,
            "avg_volume": float(row[3]) if row[3] is not None else 0,
            "avg_close": float(row[4]) if row[4] is not None else 0,
            "volatility": float(row[5]) if row[5] is not None else 0,
        } for row in r.fetchall()}


async def preload_volatility_structure(symbols: list[str], days: int = 60) -> dict[str, float]:
    """批量加载波动率结构(HV5/HV20 比值)."""
    if not symbols:
        return {}
    cutoff = date.today() - timedelta(days=days)
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT ts_code,
                   STDDEV(ret) FILTER (WHERE rn >= 56) /
                   NULLIF(STDDEV(ret) FILTER (WHERE rn >= 41), 0) as hv_ratio
            FROM (
                SELECT ts_code, trade_date,
                       (close - LAG(close) OVER w) / NULLIF(LAG(close) OVER w, 0) as ret,
                       ROW_NUMBER() OVER w as rn
                FROM daily_kline
                WHERE ts_code = ANY(:syms) AND trade_date >= :cut
                WINDOW w AS (PARTITION BY ts_code ORDER BY trade_date)
            ) sub
            WHERE ret IS NOT NULL
            GROUP BY ts_code
            HAVING COUNT(*) >= 20
        """), {"syms": symbols, "cut": cutoff})
        return {row[0]: float(row[1]) if row[1] is not None else 1.0 for row in r.fetchall()}


def _score_volatility_structure(hv_ratio: float = 1.0) -> float:
    """波动率结构: HV5/HV20 比值。>1.2=波动放大(可能突破), <0.8=波动收缩(可能变盘).

    融入 D2 流动性维度作为调节因子。
    """
    if hv_ratio > 1.5:       # 波动率急剧放大 — 可能突破或恐慌
        return 7.0
    elif hv_ratio > 1.2:     # 短期波动高于长期 — 波动放大
        return 8.0
    elif hv_ratio > 0.8:     # 正常范围
        return NEUTRAL
    elif hv_ratio > 0.5:     # 波动率收缩 — 可能变盘
        return 3.0
    else:                    # 极度收缩 — 暴风雨前宁静
        return 2.0


# ── 单维评分函数 ─────────────────────────────────

def _clamp(v: float, lo: float = 0.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, v))


# 市值校准缓存
_market_cap_calibration: dict = {"log_min": 8.0, "log_range": 4.0}


async def _calibrate_market_cap_params():
    """从 daily_basic 动态计算市值对数分布参数."""
    global _market_cap_calibration
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT PERCENTILE_CONT(0.1) WITHIN GROUP (ORDER BY LOG(total_mv)),
                   PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY LOG(total_mv))
            FROM daily_basic
            WHERE total_mv > 0 AND trade_date >= CURRENT_DATE - 10
        """))
        row = r.fetchone()
        if row and row[0] and row[1]:
            p10 = float(row[0])   # P10 对数市值(小盘边界)
            p90 = float(row[1])   # P90 对数市值(大盘边界)
            _market_cap_calibration = {"log_min": p10, "log_range": max(1.0, p90 - p10)}


def _score_market_cap(total_mv: Optional[float]) -> float:
    """市值定位: 对数缩放, 用全市场 P10-P90 动态校准."""
    if total_mv is None or total_mv <= 0:
        return NEUTRAL
    import math
    log_mv = math.log10(total_mv)
    log_min = _market_cap_calibration["log_min"]
    log_range = _market_cap_calibration["log_range"]
    # (log_mv - P10) / (P90 - P10) * 10 → 映射到 0-10
    return _clamp((log_mv - log_min) / log_range * 10.0)


def _score_liquidity(turnover_rate: Optional[float], avg_amplitude: Optional[float]) -> float:
    """流动性: 换手率 1-5% 最佳(7-9分), 极端高/低扣分."""
    score = NEUTRAL
    if turnover_rate is not None:
        if 1.0 <= turnover_rate <= 5.0:
            score += 2.5
        elif 0.3 <= turnover_rate < 1.0:
            score += 1.0
        elif turnover_rate > 10.0:
            score -= 1.5
        else:
            score -= 0.5
    if avg_amplitude is not None:
        if 2.0 <= avg_amplitude <= 6.0:
            score += 1.5
        elif avg_amplitude > 10.0:
            score -= 1.0
    return _clamp(score)


def _score_valuation(pe_ttm: Optional[float], pb: Optional[float]) -> float:
    """估值: PE 10-30x / PB 0.5-3x 为合理区间."""
    score = NEUTRAL
    if pe_ttm is not None and pe_ttm > 0:
        if pe_ttm < 10:
            score += 2.0  # 深度价值
        elif 10 <= pe_ttm <= 30:
            score += 2.5  # 合理估值
        elif 30 < pe_ttm <= 60:
            score += 0.5
        elif pe_ttm > 100:
            score -= 2.0  # 高估值风险
    if pb is not None and pb > 0:
        if pb < 1.0:
            score += 1.0  # 破净折价
        elif 1.0 <= pb <= 3.0:
            score += 1.5
        elif pb > 8.0:
            score -= 1.5
    return _clamp(score)


def _score_moneyflow(net_elg_amount: float, net_lg_amount: float, total_mv: float = 0.0) -> float:
    """资金流向: 净流入金额 / 总市值 归一化，消除大小盘不可比偏差.

    net_elg_amount / net_lg_amount: 近10日均超大单/大单净买入金额(元)
    total_mv: 总市值(万元)
    比值单位: 万分之一 (bp)，例如 1 表示净买入占市值 0.01%
    """
    score = NEUTRAL
    if total_mv <= 0:
        return score  # 无法归一化，返回中性分
    total_net_amount = net_elg_amount + net_lg_amount
    # 转换为 bp (basis points): net_amount / (total_mv * 10000) * 10000 = net_amount / total_mv
    # total_mv 是万元，乘以 10000 转为元
    ratio_bp = total_net_amount / (total_mv * 10000) * 10000
    if ratio_bp > 5.0:        # >5bp 强流入
        score += 3.0
    elif ratio_bp > 1.5:      # >1.5bp 明显流入
        score += 1.5
    elif ratio_bp > 0.2:      # >0.2bp 微幅流入
        score += 0.5
    elif ratio_bp < -5.0:     # <-5bp 强流出
        score -= 2.5
    elif ratio_bp < -1.5:     # <-1.5bp 明显流出
        score -= 1.0
    return _clamp(score)


def _score_margin(margin_data: Optional[dict]) -> float:
    """杠杆资金: 融资余额上升=看多信号, 下降=看空."""
    if margin_data is None:
        return NEUTRAL
    score = NEUTRAL
    avg_rz = margin_data.get("avg_rz_balance", 0) or 0
    rz_change = margin_data.get("rz_change", 0) or 0
    if avg_rz > 0 and rz_change > 0:
        pct_change = rz_change / avg_rz * 100
        if pct_change > 10:
            score += 2.5
        elif pct_change > 3:
            score += 1.5
        elif pct_change < -10:
            score -= 2.0
        elif pct_change < -3:
            score -= 1.0
    return _clamp(score)


def _score_fundamentals(snapshot: Optional[dict]) -> float:
    """基本面质量: ROE + 营收增速 + 负债率 + 现金流."""
    if snapshot is None:
        return NEUTRAL
    score = NEUTRAL
    roe = snapshot.get("roe")
    if roe is not None:
        if roe > 20:
            score += 2.5
        elif roe > 10:
            score += 1.5
        elif roe < 0:
            score -= 2.0

    rev_yoy = snapshot.get("revenue_yoy")
    if rev_yoy is not None:
        if rev_yoy > 30:
            score += 1.5
        elif rev_yoy > 10:
            score += 1.0
        elif rev_yoy < -10:
            score -= 1.5

    debt = snapshot.get("debt_to_assets")
    if debt is not None:
        if debt < 30:
            score += 1.0
        elif debt > 70:
            score -= 2.0

    ocf = snapshot.get("ocflow_net")
    if ocf is not None and ocf > 0:
        score += 1.0
    elif ocf is not None and ocf < 0:
        score -= 1.5

    return _clamp(score)


async def preload_holder_data(symbols: list[str]) -> dict[str, dict]:
    """批量加载股东户数变化数据.

    取最近两期 holder_num 在 Python 中计算环比变化率。
    注意：LAG 在最新行的窗口函数值为 NULL，因此在 Python 侧计算。
    """
    if not symbols:
        return {}
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT ts_code, end_date, ann_date, holder_num
            FROM (
                SELECT ts_code, end_date, ann_date, holder_num,
                       ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY end_date DESC) as rn
                FROM stk_holdernumber
                WHERE ts_code = ANY(:syms) AND holder_num > 0
            ) sub
            WHERE rn <= 2
            ORDER BY ts_code, end_date DESC
        """), {"syms": symbols})
        rows = r.fetchall()

    # 按股票分组，取最近两期计算环比
    stock_periods: dict[str, list[dict]] = {}
    for row in rows:
        stock_periods.setdefault(row[0], []).append({
            "end_date": row[1], "ann_date": row[2], "holder_num": row[3],
        })

    result = {}
    for ts_code, periods in stock_periods.items():
        latest = periods[0]
        change = None
        if len(periods) >= 2:
            prev = periods[1]
            if prev["holder_num"] and prev["holder_num"] > 0:
                change = (latest["holder_num"] - prev["holder_num"]) / prev["holder_num"] * 100
        result[ts_code] = {
            "end_date": latest["end_date"],
            "ann_date": latest["ann_date"],
            "holder_num": latest["holder_num"],
            "holder_num_change": round(change, 2) if change is not None else None,
        }
    return result


def _score_shareholder_structure(holder_data: Optional[dict]) -> float:
    """筹码结构: 股东户数变化率。下降=集中(看多), 上升=分散(看空).

    阈值参考：变化率超过 ±20% 才有显著意义。
    使用 ann_date 校验数据新鲜度(季报滞后保护)。
    """
    if holder_data is None:
        return NEUTRAL
    score = NEUTRAL
    change = holder_data.get("holder_num_change")
    if change is None:
        return NEUTRAL

    # 数据新鲜度检查：ann_date 距今超过 120 天视为陈旧
    ann_date = holder_data.get("ann_date")
    if ann_date and isinstance(ann_date, date):
        days_since_ann = (date.today() - ann_date).days
        if days_since_ann > 180:
            return NEUTRAL  # 数据过于陈旧，不纳入评分

    if change < -30:        # 股东数下降>30%，筹码高度集中
        score += 2.5
    elif change < -20:      # 下降>20%，明显集中
        score += 1.5
    elif change < -10:      # 下降>10%，轻微集中
        score += 0.5
    elif change > 30:       # 上升>30%，筹码高度分散
        score -= 2.0
    elif change > 20:       # 上升>20%，明显分散
        score -= 1.5
    elif change > 10:       # 上升>10%，轻微分散
        score -= 0.5
    return _clamp(score)


def _score_chip_distribution(kline: Optional[dict]) -> float:
    """筹码分布: 未实现 — 成交量剖面算法复杂度高，待 Phase C.

    真正的筹码分布需要:
    1. 每日成交量按价格区间累加
    2. 形成"筹码峰"结构
    3. 计算获利盘比例、套牢盘压力位
    当前 chip_daily 表为空，降级返回中性分。
    """
    return None  # 未实现，不计入指纹向量 (dim_chip_distribution列当前存北向资金分，历史遗留)


def _score_price_range(close_price: Optional[float]) -> float:
    """价格区间: <5元(仙股风险)或>100元(高价难吸筹)→低分, 5-100元→合理."""
    if close_price is None or close_price <= 0:
        return NEUTRAL
    if close_price < 5:
        return 0.0     # 仙股/僵尸股，流动性差
    elif close_price > 100:
        return 1.0     # 高价股，散户难参与
    elif 10 <= close_price <= 30:
        return 10.0    # 最佳区间
    elif 5 <= close_price < 10:
        return 6.0     # 低价但尚可
    elif 30 < close_price <= 60:
        return 8.0     # 中高价，较活跃
    else:  # 60-100
        return 5.0     # 偏高但可接受


async def preload_name_industry(symbols: list[str]) -> dict[str, dict[str, str]]:
    """批量加载股票名称和行业."""
    if not symbols:
        return {}
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT DISTINCT ON (symbol) symbol, name, COALESCE(industry, '') as industry
            FROM scan_results
            WHERE symbol = ANY(:syms)
            ORDER BY symbol, scan_date DESC
        """), {"syms": symbols})
        return {row[0]: {"name": row[1] or "", "industry": row[2] or ""} for row in r.fetchall()}


async def preload_northbound_data(symbols: list[str]) -> dict[str, dict]:
    """批量加载北向资金持股比例及近期变化(动量感知)."""
    if not symbols:
        return {}
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT ts_code, ratio, trade_date,
                   ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) as rn
            FROM hk_hold
            WHERE ts_code = ANY(:syms)
        """), {"syms": symbols})
        rows = r.fetchall()

    # 按股票分组，取最近两期计算变化
    periods: dict[str, list] = {}
    for row in rows:
        periods.setdefault(row[0], []).append({"ratio": float(row[1]) if row[1] is not None else 0.0, "date": row[2]})

    result = {}
    for ts_code, pts in periods.items():
        latest = pts[0]
        change = None
        if len(pts) >= 2:
            prev = pts[1]
            if prev["ratio"] > 0:
                change = latest["ratio"] - prev["ratio"]  # 百分点变化(pp)
        result[ts_code] = {
            "ratio": latest["ratio"],
            "ratio_change": round(change, 3) if change is not None else None,
        }
    return result


def _score_northbound(nb_data: Optional[dict]) -> float:
    """北向资金动量: 持股比例 + 近期变化。外资增持=看多, 减持=看空."""
    if nb_data is None:
        return NEUTRAL
    score = NEUTRAL
    ratio = nb_data.get("ratio", 0) or 0
    if ratio <= 0:
        return NEUTRAL

    # 绝对比例 (0-3分)
    if ratio > 8.0:
        score += 3.0
    elif ratio > 5.0:
        score += 2.0
    elif ratio > 2.0:
        score += 1.0
    elif ratio < 0.1:
        score -= 0.5

    # 动量变化 (0-3分) — 如果有多期数据
    change = nb_data.get("ratio_change")
    if change is not None:
        if change > 1.0:        # 大幅增持 >1pp
            score += 3.0
        elif change > 0.3:      # 明显增持
            score += 2.0
        elif change > 0.05:     # 微幅增持
            score += 1.0
        elif change < -0.5:     # 减持
            score -= 2.0
        elif change < -0.1:
            score -= 1.0

    return _clamp(score)

def _score_special_tags(symbol: str, name: str = "", industry: str = "") -> float:
    """特殊标签: ST/*ST风险、市场分层、行业稀缺性."""
    score = NEUTRAL

    # 1. ST/*ST 风险检测(名称以 ST/*ST 开头)
    if name and (name.startswith("*ST") or name.startswith("ST")):
        score -= 4.0  # 退市风险，严重扣分

    # 2. 市场分层
    if symbol.endswith(".BJ"):
        score -= 1.0      # 北交所流动性折价
    elif symbol.startswith("688"):
        score += 0.5      # 科创板科技属性
    elif symbol.startswith("300") or symbol.startswith("301"):
        score += 0.3      # 创业板成长属性

    # 3. 行业稀缺性(A股独有赛道加分)
    RARE_INDUSTRIES = {"白酒", "中药", "稀土", "军工", "免税", "烟草", "殡葬", "核电"}
    if industry and any(ri in industry for ri in RARE_INDUSTRIES):
        score += 1.0      # 中国独有资产，无外部锚定价，稀缺性溢价

    return _clamp(score)


def _score_event_driven(snapshot: Optional[dict]) -> float:
    """事件驱动: 业绩跳变(profit_yoy 大幅变化)."""
    if snapshot is None:
        return NEUTRAL
    score = NEUTRAL
    profit_yoy = snapshot.get("profit_yoy")
    if profit_yoy is not None:
        if profit_yoy > 100:
            score += 3.0
        elif profit_yoy > 50:
            score += 2.0
        elif profit_yoy > 20:
            score += 1.0
        elif profit_yoy < -50:
            score -= 2.5
        elif profit_yoy < -20:
            score -= 1.0
    return _clamp(score)


async def preload_market_average_momentum() -> float:
    """计算全市场(沪深两市)60日平均动量，用于跨市场相对强度."""
    cutoff = date.today() - timedelta(days=60)
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT AVG(momentum) FROM (
                SELECT (MAX(close) - MIN(close)) / NULLIF(MIN(close), 0) * 100 as momentum
                FROM daily_kline
                WHERE trade_date >= :cut
                GROUP BY ts_code
                HAVING COUNT(*) >= 40
            ) sub
        """), {"cut": cutoff})
        row = r.fetchone()
        return float(row[0]) if row and row[0] else 0.0


def _score_cross_market(kline: Optional[dict], market_avg_momentum: float = 0.0) -> float:
    """跨市场影响: 个股相对全市场超额 Alpha(60日动量差).

    正值=跑赢市场(内在强), 负值=跑输市场(内在弱).
    市场动量作为基准线，差值反映个股独立于大盘的强度。
    """
    if kline is None:
        return NEUTRAL
    score = NEUTRAL
    momentum = kline.get("momentum", 0) or 0
    alpha = momentum - market_avg_momentum  # 超额收益
    if alpha > 30:
        score += 3.0
    elif alpha > 15:
        score += 2.0
    elif alpha > 5:
        score += 1.0
    elif alpha > -5:
        score += 0.0
    elif alpha > -15:
        score -= 0.5
    elif alpha > -30:
        score -= 1.5
    else:
        score -= 2.5
    return _clamp(score)


async def _score_ma_trend(ts_code: str, ma_cache: dict[str, Optional[dict]]) -> float:
    """趋势质量(第12维): 8-21-55-144-250 EMA五线体系综合评分.

    调用 ma_scorer.calc_ma_score()，将 0-100 分映射到 0-10。
    K线不足250根 → 返回 NEUTRAL (5.0)。
    """
    result = ma_cache.get(ts_code)
    if result is None:
        return NEUTRAL
    if result.get("insufficient", False):
        return NEUTRAL
    return _clamp(result["score"] / 10.0)


async def preload_ma_scores(symbols: list[str]) -> dict[str, Optional[dict]]:
    """批量预加载均线评分."""
    if not symbols:
        return {}
    from datetime import date as dt_date
    from app.services.ma_scorer import calc_ma_score

    today = dt_date.today()
    results = {}
    for sym in symbols:
        try:
            result = await calc_ma_score(sym, today)
            if result is None:
                results[sym] = {"score": 50.0, "insufficient": True}
            else:
                results[sym] = result
        except Exception:
            results[sym] = {"score": 50.0, "insufficient": True}
    return results


async def preload_pattern_scores(symbols: list[str]) -> dict[str, dict]:
    """批量加载形态评分(当日多头最高分+空头最高分)."""
    if not symbols:
        return {}
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT ts_code,
                   MAX(CASE WHEN pt.bullish THEN pattern_score ELSE 0 END) as bull_score,
                   MAX(CASE WHEN NOT pt.bullish THEN pattern_score ELSE 0 END) as bear_score
            FROM pattern_signals
            JOIN (VALUES
                ('three_red_soldiers', true), ('golden_spider', true), ('bullish_artillery', true),
                ('morning_star', true), ('double_firecracker', true), ('air_refueling', true),
                ('single_yang_unbroken', true), ('dawn_appearance', true), ('golden_needle_bottom', true),
                ('three_black_crows', false), ('evening_star', false), ('hanging_man', false),
                ('decapitation', false), ('dark_cloud_cover', false), ('pouring_rain', false)
            ) AS pt(pattern_type, bullish) ON pattern_signals.pattern_type = pt.pattern_type
            WHERE ts_code = ANY(:syms) AND trade_date = CURRENT_DATE
            GROUP BY ts_code
        """), {"syms": symbols})
        return {row[0]: {"bull": float(row[1]) if row[1] else 0.0,
                         "bear": float(row[2]) if row[2] else 0.0}
                for row in r.fetchall()}


def _score_pattern(pattern_data: dict | None = None) -> float:
    """形态识别: 多头形态加分, 空头形态重罚, 映射到0-10.

    空头形态惩罚权重大于多头加分——避坑优先。
    """
    if pattern_data is None:
        return NEUTRAL
    bull = pattern_data.get("bull", 0.0) if isinstance(pattern_data, dict) else 0.0
    bear = pattern_data.get("bear", 0.0) if isinstance(pattern_data, dict) else 0.0
    score = NEUTRAL + bull * 0.5 - bear * 1.5  # 空头惩罚3x于多头加分
    return _clamp(score)


# ── 主函数：批量构建指纹 ─────────────────────────

async def build_fingerprints(symbols: list[str]) -> dict[str, list[float]]:
    """批量构建 11 维指纹向量.

    Returns:
        {symbol: [dim1, dim2, ..., dim11]} 每个维度 0-10 分
    """
    if not symbols:
        return {}

    await _calibrate_market_cap_params()  # 动态校准市值参数

    snapshots = await preload_snapshot_data(symbols)
    name_ind_data = await preload_name_industry(symbols)  # 名称+行业(用于D9特殊标签)
    market_data = await preload_market_data(symbols)
    moneyflow_data = await preload_moneyflow(symbols)
    margin_data = await preload_margin_data(symbols)
    kline_data = await preload_kline_metrics(symbols)
    holder_data = await preload_holder_data(symbols)
    mv_data = await preload_market_cap_for_normalization(symbols)
    market_avg_momentum = await preload_market_average_momentum()
    ma_scores = await preload_ma_scores(symbols)  # 第12维：均线趋势质量

    northbound_data = await preload_northbound_data(symbols)  # D8: 北向资金
    hv_data = await preload_volatility_structure(symbols)     # D13: 波动率结构
    pattern_data = await preload_pattern_scores(symbols)      # D15: 形态识别

    fingerprints = {}
    for sym in symbols:
        snap = snapshots.get(sym, {})
        mkt = market_data.get(sym, {})
        mf = moneyflow_data.get(sym, {})
        mg = margin_data.get(sym, {})
        kl = kline_data.get(sym, {})
        hd = holder_data.get(sym, {})
        mv = mv_data.get(sym, 0.0)
        nb = northbound_data.get(sym, {})
        hv = hv_data.get(sym, 1.0)

        vec = [
            round(_score_market_cap(mkt.get("total_mv")), 2),
            round(_score_liquidity(mkt.get("turnover_rate"), kl.get("avg_amplitude")), 2),
            round(_score_valuation(snap.get("pe_ttm"), snap.get("pb")), 2),
            round(_score_moneyflow(mf.get("net_elg_amount", 0), mf.get("net_lg_amount", 0), mv), 2),
            round(_score_margin(mg if mg else None), 2),
            round(_score_fundamentals(snap if snap else None), 2),
            round(_score_shareholder_structure(hd if hd else None), 2),
            round(_score_northbound(nb if nb else None), 2),
            round(_score_special_tags(sym, name_ind_data.get(sym, {}).get("name", ""), name_ind_data.get(sym, {}).get("industry", "")), 2),
            round(_score_event_driven(snap if snap else None), 2),
            round(_score_cross_market(kl if kl else None, market_avg_momentum), 2),
            round(await _score_ma_trend(sym, ma_scores), 2),
            round(_score_volatility_structure(hv), 2),
            round(_score_price_range(kl.get("avg_close")), 2),
            round(_score_pattern(pattern_data.get(sym)), 2),
        ]
        fingerprints[sym] = vec

    return fingerprints


async def build_and_save_fingerprints(symbols: list[str]) -> int:
    """构建指纹并存入 stock_fingerprints 表(含原型分类)."""
    import json
    from app.services.archetype_classifier import classify_stocks

    fingerprints = await build_fingerprints(symbols)
    if not fingerprints:
        return 0

    # 对指纹进行原型分类
    try:
        archetypes = await classify_stocks(symbols, fingerprints)
    except Exception as e:
        logger.warning(f"Archetype classification failed in build_and_save: {e}")
        archetypes = {}

    today = date.today()
    async with async_session_factory() as s:
        for sym, vec in fingerprints.items():
            arch = archetypes.get(sym, "unknown")
            await s.execute(text("""
                INSERT INTO stock_fingerprints (ts_code, scan_date, fingerprint_vector,
                    dim_market_cap, dim_liquidity, dim_valuation,
                    dim_capital_flow_dir, dim_capital_flow_margin,
                    dim_fundamental_quality, dim_shareholder,
                    dim_chip_distribution, dim_special_labels,
                    dim_event_detection, dim_cross_market,
                    dim_ma_trend, dim_volatility_structure,
                    dim_price_range, dim_pattern,
                    archetype, calc_date)
                VALUES (:s, :d, CAST(:v AS jsonb),
                    :d1, :d2, :d3, :d4, :d5, :d6, :d7, :d8, :d9, :d10, :d11,
                    :d12, :d13, :d14, :d15,
                    :arch, :cd)
                ON CONFLICT (ts_code, scan_date)
                DO UPDATE SET fingerprint_vector=CAST(:v AS jsonb),
                    dim_market_cap=:d1, dim_liquidity=:d2, dim_valuation=:d3,
                    dim_capital_flow_dir=:d4, dim_capital_flow_margin=:d5,
                    dim_fundamental_quality=:d6, dim_shareholder=:d7,
                    dim_chip_distribution=:d8, dim_special_labels=:d9,
                    dim_event_detection=:d10, dim_cross_market=:d11,
                    dim_ma_trend=:d12, dim_volatility_structure=:d13,
                    dim_price_range=:d14, dim_pattern=:d15,
                    archetype=:arch, calc_date=:cd
            """), {
                "s": sym, "d": today, "v": json.dumps(vec),
                "d1": vec[0], "d2": vec[1], "d3": vec[2], "d4": vec[3],
                "d5": vec[4], "d6": vec[5], "d7": vec[6], "d8": vec[7],
                "d9": vec[8], "d10": vec[9], "d11": vec[10],
                "d12": vec[11] if len(vec) > 11 else None,
                "d13": vec[12] if len(vec) > 12 else None,
                "d14": vec[13] if len(vec) > 13 else None,
                "d15": vec[14] if len(vec) > 14 else None,
                "arch": arch, "cd": today,
            })
        await s.commit()
    return len(fingerprints)


# ── 单股查询 ─────────────────────────────────────

async def get_fingerprint(symbol: str) -> Optional[list[float]]:
    """获取单只股票的最新指纹."""
    import json
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT fingerprint_vector FROM stock_fingerprints
            WHERE ts_code=:s ORDER BY scan_date DESC LIMIT 1
        """), {"s": symbol})
        row = r.fetchone()
        if row and row[0]:
            return json.loads(row[0]) if isinstance(row[0], str) else row[0]
    return None
