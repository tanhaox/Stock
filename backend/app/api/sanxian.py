"""三线对比 API — 个股/板块/大盘相对强弱可视化."""
from datetime import date, timedelta
from fastapi import APIRouter, Query
from sqlalchemy import text
from app.core.database import async_session_factory
import logging

logger = logging.getLogger("sanxian")
router = APIRouter(prefix="/sanxian", tags=["sanxian"])

# SW 行业代码 → THS 概念名 映射（用于匹配 ths_member）
SECTOR_MAP = {
    "801010.SI": "农林牧渔", "801030.SI": "化工", "801040.SI": "钢铁",
    "801050.SI": "有色金属", "801080.SI": "电子", "801110.SI": "家用电器",
    "801120.SI": "食品饮料", "801130.SI": "纺织服饰", "801140.SI": "轻工制造",
    "801150.SI": "医药生物", "801160.SI": "公用事业", "801170.SI": "交通运输",
    "801180.SI": "房地产", "801200.SI": "商贸零售", "801210.SI": "社会服务",
    "801230.SI": "综合", "801710.SI": "建筑材料", "801720.SI": "建筑装饰",
    "801730.SI": "电力设备", "801740.SI": "国防军工", "801750.SI": "计算机",
    "801760.SI": "传媒", "801770.SI": "通信", "801780.SI": "银行",
    "801790.SI": "非银金融", "801880.SI": "汽车", "801890.SI": "机械设备",
    "801950.SI": "煤炭", "801960.SI": "石油石化", "801970.SI": "环保",
    "801980.SI": "美容护理",
}


@router.get("/intraday")
async def get_intraday(
    symbol: str = Query(default="002594.SZ", description="股票代码"),
    lookback: int = Query(default=5, ge=1, le=10, description="回看交易日数")
):
    """返回个股/大盘/板块5分钟线数据，用于分时叠加对比.

    个股: stk_mins API (Tushare实时拉取)
    大盘: sector_min_kline (000001.SH, 本地)
    板块: sector_min_kline (对应 SSE 行业代码, 本地)
    """
    from datetime import timedelta
    from app.services.tushare_common import call_tushare

    async with async_session_factory() as s:
        # ── 确定日期范围 (以 sector_min_kline 最近日为准, 确保个股/板块同天) ──
        r_sync = await s.execute(text(
            "SELECT MAX(trade_time::date) FROM sector_min_kline WHERE sector_code='000001.SH'"))
        latest_sync = r_sync.scalar()
        if latest_sync:
            cutoff = latest_sync - timedelta(days=lookback + 3)
            r = await s.execute(text(
                "SELECT DISTINCT trade_date FROM daily_kline WHERE trade_date >= :cut AND trade_date <= :ed ORDER BY trade_date DESC"
            ), {"cut": cutoff, "ed": latest_sync})
        else:
            cutoff = date.today() - timedelta(days=lookback + 5)
            r = await s.execute(text(
                "SELECT DISTINCT trade_date FROM daily_kline WHERE trade_date >= :cut ORDER BY trade_date DESC"
            ), {"cut": cutoff})
        tdays = [row[0] for row in r.fetchall()]
        if len(tdays) < 2:
            return {"status": "error", "detail": "交易日数据不足"}
        # 拉取 lookback 天，从倒数第 lookback 天到最近一天
        end_d = tdays[0]
        start_d = tdays[min(lookback - 1, len(tdays) - 1)]

        # ── 1. 个股 (min_kline 本地缓存优先, 缺则 stk_mins API) ──
        stock_bars = []
        for td in tdays[:lookback]:
            # Phase 59: 优先从本地 min_kline 读取
            r_local = await s.execute(text("""
                SELECT trade_time, close, vol FROM min_kline
                WHERE ts_code = :sym AND trade_time::date = :td
                ORDER BY trade_time
            """), {"sym": symbol, "td": td})
            local_rows = r_local.fetchall()
            if local_rows and len(local_rows) >= 8:  # ≥8根bar = 有足够数据
                stock_bars.extend([{
                    "time": str(row[0]), "close": float(row[1]),
                    "vol": float(row[2] or 0)
                } for row in local_rows])
                continue
            # Fallback: Tushare API
            try:
                r2 = await call_tushare('stk_mins', {
                    'ts_code': symbol, 'freq': '5min',
                    'start_date': f'{td} 09:00:00', 'end_date': f'{td} 19:00:00'
                }, 'trade_time,open,high,low,close,vol')
                if r2:
                    stock_bars.extend([{
                        "time": b["trade_time"], "close": float(b["close"]),
                        "vol": float(b.get("vol", 0))
                    } for b in r2])
            except Exception:
                pass

        # ── 2. 大盘 (sector_min_kline) ──
        end_dt = end_d + timedelta(days=1)
        r3 = await s.execute(text("""
            SELECT trade_time, close FROM sector_min_kline
            WHERE sector_code = '000001.SH' AND trade_time >= :st AND trade_time < :ed
            ORDER BY trade_time
        """), {"st": start_d, "ed": end_dt})
        market_bars = [{"time": str(row[0]), "close": float(row[1])} for row in r3.fetchall()]

        # ── 3. 板块 ──
        sector_code = await _find_sector(s, symbol)
        # SW → SSE 映射 (28 行业全覆盖, Phase 28)
        SW_TO_SSE = {
            "801010.SI": "000034.SH", "801020.SI": "000034.SH",
            "801030.SI": "000034.SH", "801040.SI": "000033.SH",
            "801050.SI": "000033.SH", "801080.SI": "000039.SH",
            "801110.SI": "000035.SH", "801120.SI": "000036.SH",
            "801130.SI": "000035.SH", "801140.SI": "000034.SH",
            "801150.SI": "000037.SH", "801160.SI": "000041.SH",
            "801170.SI": "000034.SH", "801180.SI": "000006.SH",
            "801200.SI": "000005.SH", "801210.SI": "000035.SH",
            "801230.SI": "000008.SH", "801710.SI": "000034.SH",
            "801720.SI": "000034.SH", "801730.SI": "000034.SH",
            "801740.SI": "000034.SH", "801750.SI": "000039.SH",
            "801760.SI": "000040.SH", "801770.SI": "000040.SH",
            "801780.SI": "000038.SH", "801790.SI": "000038.SH",
            "801880.SI": "000035.SH", "801890.SI": "000034.SH",
        }
        sse_code = SW_TO_SSE.get(sector_code or "", "000034.SH")

        r4 = await s.execute(text("""
            SELECT trade_time, close FROM sector_min_kline
            WHERE sector_code = :sc AND trade_time >= :st AND trade_time < :ed
            ORDER BY trade_time
        """), {"sc": sse_code, "st": start_d, "ed": end_dt})
        sector_bars = [{"time": str(row[0]), "close": float(row[1])} for row in r4.fetchall()]

    # ── 4. 按时间正序排列 + 切到同一交易日范围 ──
    stock_bars.sort(key=lambda b: b["time"])
    market_bars.sort(key=lambda b: b["time"])
    sector_bars.sort(key=lambda b: b["time"])

    # 确定共同的交易日范围
    def _get_dates(bars):
        return sorted(set(b["time"][:10] for b in bars))
    stock_dates = _get_dates(stock_bars)
    sector_dates = _get_dates(sector_bars) if sector_bars else stock_dates
    market_dates = _get_dates(market_bars) if market_bars else stock_dates

    # 用已有的全部日期（不过滤——让前端处理时间轴插值）
    all_dates = sorted(set(stock_dates + sector_dates + market_dates))[:lookback]

    # 转为百分比 (以第一个 bar 为基准)
    def to_pct(bars):
        if len(bars) < 2: return [], []
        base = bars[0]["close"] if bars[0]["close"] else 1
        times = [b["time"] for b in bars]
        vals = [round((b["close"] - base) / base * 100, 3) for b in bars]
        return times, vals

    s_t, s_v = to_pct(stock_bars)
    m_t, m_v = to_pct(market_bars)
    sc_t, sc_v = to_pct(sector_bars)

    # ── 基本信息 ──
    try:
        from app.services.stock_name_cache import get_stock_name
        stock_name = get_stock_name(symbol) or symbol
    except Exception:
        stock_name = symbol

    return {
        "status": "success", "symbol": symbol, "name": stock_name,
        "lookback": lookback, "date_range": [str(start_d), str(end_d)],
        "stock": {"times": s_t, "vals": s_v},
        "market": {"times": m_t, "vals": m_v},
        "sector": {"times": sc_t, "vals": sc_v} if sc_v else None,
    }


@router.get("")
async def get_sanxian(
    symbol: str = Query(default="002594.SZ", description="股票代码"),
    days: int = Query(default=60, ge=10, le=250, description="回看天数")
):
    """返回个股/板块/大盘三条线的累计收益率序列.

    三线定义:
      个股线 = stock daily_kline close 的累计收益率（除权日自动校正）
      板块线 = 该股所属 SW 行业指数 close 的累计收益率
      大盘线 = 000001.SH 上证指数 close 的累计收益率

    除权处理: daily_kline 存的是不复权价格。在计算收益率前，
    先对原始 close 做前复权校正——从最新日期向前逐日乘积累积复权因子。
    """
    async with async_session_factory() as s:
        cutoff = date.today() - timedelta(days=days + 20)

        # ── 1. 个股日线 + 除权校正 ──
        r = await s.execute(text("""
            SELECT trade_date, close FROM daily_kline
            WHERE ts_code = :sym AND trade_date >= :cut
            ORDER BY trade_date
        """), {"sym": symbol, "cut": cutoff})
        raw_rows = [(row[0], float(row[1])) for row in r.fetchall()]

        if len(raw_rows) < 10:
            return {"status": "error", "detail": f"{symbol} K线数据不足"}

        # 系统已全局前复权, 不需要除权校正
        stock_rows = raw_rows

        # ── 2. 确定板块代码 ──
        sector_code = await _find_sector(s, symbol)
        sector_name = SECTOR_MAP.get(sector_code, sector_code.replace(".SI", "")) if sector_code else "未知"

        # ── 3. 板块日线 (sw_sector_index) ──
        sector_rows = []
        if sector_code:
            r2 = await s.execute(text("""
                SELECT trade_date, close FROM sw_sector_index
                WHERE index_code = :sc AND trade_date >= :cut
                ORDER BY trade_date
            """), {"sc": sector_code, "cut": cutoff})
            sector_rows = [(row[0], float(row[1])) for row in r2.fetchall()]

        # ── 4. 大盘日线 ──
        r3 = await s.execute(text("""
            SELECT trade_date, close FROM daily_kline
            WHERE ts_code = '700001.TI' AND trade_date >= :cut
            ORDER BY trade_date
        """), {"cut": cutoff})
        market_rows = [(row[0], float(row[1])) for row in r3.fetchall()]

    # ── 5. 按日历日对齐（取三个序列的交集日期） ──
    stock_dates = {d for d, _ in stock_rows[-days:]}
    sector_dates = {d for d, _ in sector_rows} if sector_rows else set()
    market_dates = {d for d, _ in market_rows}
    common = sorted(stock_dates & market_dates)

    if len(common) < 10:
        return {"status": "error", "detail": "日期对齐后有效交易日不足10天"}

    # 如果板块数据充足，也加入交集
    if sector_dates:
        common = sorted(set(common) & sector_dates)

    def _to_series(rows, dates):
        """将 trade_date → close 映射转为累计收益率序列."""
        price_map = {d: c for d, c in rows}
        base = price_map.get(dates[0])
        if not base or base == 0:
            return None
        return [round((price_map.get(d, 0) - base) / base * 100, 2) for d in dates]

    def _calc_ma(prices: list[float], period: int) -> list[float | None]:
        """简单移动均线，前 period-1 个值为 None."""
        ma = [None] * len(prices)
        for i in range(period - 1, len(prices)):
            ma[i] = round(sum(prices[i - period + 1:i + 1]) / period, 2)
        return ma

    stock_ret = _to_series(stock_rows, common)
    market_ret = _to_series(market_rows, common)
    sector_ret = _to_series(sector_rows, common) if sector_rows else None

    if stock_ret is None or market_ret is None:
        return {"status": "error", "detail": "无法计算基准收益率"}

    # ★ 价格+均线序列（前复权后的收盘价）
    price_map = {d: c for d, c in stock_rows}
    stock_prices = [price_map.get(d, 0) for d in common]
    ma5 = _calc_ma(stock_prices, 5)
    ma10 = _calc_ma(stock_prices, 10)
    ma20 = _calc_ma(stock_prices, 20)
    ma60 = _calc_ma(stock_prices, 60)

    # ── 6. 股票基本信息 ──
    try:
        from app.services.stock_name_cache import get_stock_name
        stock_name = get_stock_name(symbol) if symbol else symbol
    except Exception:
        stock_name = symbol

    # ── 7. 当前标签 ──
    tags = {}
    try:
        r4 = await s.execute(text("SELECT board, market_cap_tier, risk_status FROM stock_tags WHERE ts_code = :sym"), {"sym": symbol})
        row = r4.fetchone()
        if row:
            tags = {"board": row[0], "market_cap": row[1], "risk": row[2]}
    except Exception:
        pass

    # ── 8. 综合分析指标 ──
    analysis = _build_analysis(stock_prices, stock_ret, market_ret, sector_ret,
                               ma5, ma10, ma20, ma60, sector_name, sector_code)
    if sector_code:
        try:
            r5 = await s.execute(text("""
                SELECT direction, lifecycle, pct_5d, pct_10d, pct_20d, rank_5d, vol_ratio
                FROM sector_trend WHERE sector_code = :sc
                AND trade_date = (SELECT MAX(trade_date) FROM sector_trend WHERE sector_code = :sc)
            """), {"sc": sector_code})
            row = r5.fetchone()
            if row:
                analysis["sector_trend"] = {
                    "direction": row[0], "lifecycle": row[1],
                    "pct_5d": float(row[2] or 0), "pct_10d": float(row[3] or 0),
                    "pct_20d": float(row[4] or 0), "rank_5d": row[5], "vol_ratio": float(row[6] or 0),
                }
        except Exception:
            pass

    return {
        "status": "success",
        "symbol": symbol, "name": stock_name, "tags": tags,
        "sector_code": sector_code, "sector_name": sector_name,
        "dates": [str(d) for d in common],
        "stock": stock_ret, "sector": sector_ret, "market": market_ret,
        "prices": stock_prices, "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
        "analysis": analysis,
    }


def _build_analysis(prices, stock, market, sector, ma5, ma10, ma20, ma60, sector_name, sector_code):
    """构建综合强弱分析字典."""
    now = prices[-1] if prices else 0
    n = len(prices)

    # ── MA多空矩阵 ──
    def _ma_pos(arr, label):
        vals = [v for v in arr if v is not None]
        if not vals: return {"label": label, "above": None, "val": None, "dist": None}
        v = vals[-1]
        return {"label": label, "above": now > v, "val": round(v, 2), "dist": round((now - v) / v * 100, 2) if v else None}

    ma_matrix = [_ma_pos(ma5, "MA5"), _ma_pos(ma10, "MA10"), _ma_pos(ma20, "MA20"), _ma_pos(ma60, "MA60")]
    for p, lbl in [(30, "MA30"), (120, "MA120"), (250, "MA250")]:
        if n >= p:
            _ma = sum(prices[-min(p, n):]) / min(p, n)
            ma_matrix.append({"label": lbl, "above": now > _ma, "val": round(_ma, 2),
                              "dist": round((now - _ma) / _ma * 100, 2) if _ma else None})

    # ── 多头/空头排列 ──
    valid_mas = [m for m in ma_matrix if m["above"] is not None]
    bullish = sum(1 for m in valid_mas if m["above"])
    mult_score = round(bullish / len(valid_mas) * 100, 0) if valid_mas else 50

    # ── 8 种相对位置 ──
    stock_end = stock[-1] if stock else 0
    market_end = market[-1] if market else 0
    sector_end = sector[-1] if sector else market_end
    sector_up = sector_end > 1
    market_up = market_end > 0.5
    beats_sector = stock_end > sector_end + 1
    beats_market = stock_end > market_end + 1

    if sector_up and market_up and beats_sector:    position = "领涨龙头"
    elif sector_up and market_up and not beats_sector: position = "跟涨"
    elif sector_up and market_up and stock_end < -1:   position = "主力出货"
    elif sector_up and not market_up and beats_sector:  position = "独立走强"
    elif not sector_up and market_up and beats_sector:  position = "逆势抗跌"
    elif not sector_up and not market_up and stock_end < sector_end - 1: position = "领跌"
    elif not sector_up and not market_up and beats_sector: position = "逆势拉升"
    else: position = "抗跌"

    # ── 价格位置 ──
    period_high = max(prices); period_low = min(prices)
    price_pos = round((now - period_low) / (period_high - period_low) * 100, 0) if period_high > period_low else 50

    # ── 趋势强度 ──
    if n >= 5: trend_5d = round((prices[-1] - prices[-5]) / prices[-5] * 100, 2) if prices[-5] else 0
    else: trend_5d = 0

    return {
        "ma_matrix": ma_matrix, "mult_head_score": mult_score,
        "position": position, "beats_sector": beats_sector, "beats_market": beats_market,
        "price_position": price_pos, "trend_5d": trend_5d,
        "stock_ret": round(stock_end, 1), "sector_ret": round(sector_end, 1), "market_ret": round(market_end, 1),
        "alpha": round(stock_end - sector_end, 1), "beta": round(stock_end - market_end, 1),
        "sector_name": sector_name, "dates": n,
    }


async def _find_sector(session, symbol: str) -> str | None:
    """找到股票所属的 SW 行业代码 (v4.9 Phase 28: 优先查固话表)."""

    # ── 方法0: stock_sector_map 固话表 (最快) ──
    try:
        r = await session.execute(text(
            "SELECT sw_code FROM stock_sector_map WHERE ts_code = :sym"
        ), {"sym": symbol})
        row = r.fetchone()
        if row and row[0]:
            return row[0]
    except Exception:
        pass

    # ── 方法1: ths_member ──
    ths_names = []

    # 方法1: ths_member
    try:
        r = await session.execute(text(
            "SELECT ths_name FROM ths_member WHERE ts_code = :sym AND out_date IS NULL"
        ), {"sym": symbol})
        ths_names = [row[0] for row in r.fetchall() if row[0]]
    except Exception:
        pass

    # 方法2: stock_name_cache 或 scan_results 中的名称
    if not ths_names:
        for tbl in ["stock_name_cache", "scan_results"]:
            try:
                col = "name" if tbl == "scan_results" else "name"
                r2 = await session.execute(text(
                    f"SELECT {col} FROM {tbl} WHERE symbol = :sym ORDER BY {col} LIMIT 1"
                ) if tbl != "scan_results" else text(
                    "SELECT name FROM scan_results WHERE symbol = :sym ORDER BY scan_date DESC LIMIT 1"
                ), {"sym": symbol})
                row = r2.fetchone()
                if row and row[0]:
                    ths_names = [row[0]]
                    break
            except Exception:
                continue

    # 关键词→SW映射(兜底)
    KEYWORDS = {
        "汽车": "801880.SI", "比亚迪": "801880.SI", "新能源": "801730.SI",
        "电池": "801730.SI", "宁德": "801730.SI", "半导体": "801080.SI",
        "芯片": "801080.SI", "医药": "801150.SI", "医疗": "801150.SI",
        "银行": "801780.SI", "证券": "801790.SI", "保险": "801790.SI",
        "钢铁": "801040.SI", "煤炭": "801950.SI", "有色": "801050.SI",
        "化工": "801030.SI", "地产": "801180.SI", "建筑": "801720.SI",
        "建材": "801710.SI", "食品": "801120.SI", "饮料": "801120.SI",
        "酒": "801120.SI", "家电": "801110.SI", "电力": "801160.SI",
        "交通": "801170.SI", "运输": "801170.SI", "航空": "801170.SI",
        "军工": "801740.SI", "船舶": "801740.SI", "软件": "801750.SI",
        "计算机": "801750.SI", "传媒": "801760.SI", "通信": "801770.SI",
        "5G": "801770.SI", "环保": "801970.SI", "机械": "801890.SI",
        "设备": "801890.SI", "石油": "801960.SI", "农业": "801010.SI",
        "牧渔": "801010.SI", "旅游": "801210.SI", "零售": "801200.SI",
        "商贸": "801200.SI", "服装": "801130.SI", "纺织": "801130.SI",
    }
    for name in ths_names:
        for kw, code in KEYWORDS.items():
            if kw in (name or ""):
                await _cache_sector(session, symbol, code)  # write-back
                return code

    # 方法3: SECTOR_MAP 精确匹配
    for ths_name in ths_names:
        for sw_code, sw_name in SECTOR_MAP.items():
            if sw_name in str(ths_name or ""):
                await _cache_sector(session, symbol, sw_code)
                return sw_code

    # 方法4: 前2字模糊
    import re
    for ths_name in ths_names:
        short = re.sub(r'[（(].*|Ⅲ|Ⅱ|Ⅰ', '', str(ths_name or ''))[:2]
        for sw_code, sw_name in SECTOR_MAP.items():
            if short and short in sw_name:
                await _cache_sector(session, symbol, sw_code)
                return sw_code

    return None


async def _cache_sector(session, symbol: str, sw_code: str):
    """将查到的 SW 代码写入 stock_sector_map 缓存表."""
    try:
        sse_code = {
            "801880.SI": "000035.SH", "801730.SI": "000034.SH",
            "801080.SI": "000039.SH", "801750.SI": "000039.SH",
            "801150.SI": "000037.SH", "801780.SI": "000038.SH",
            "801050.SI": "000033.SH", "801120.SI": "000036.SH",
            "801180.SI": "000006.SH", "801010.SI": "000034.SH",
            "801020.SI": "000034.SH", "801030.SI": "000034.SH",
            "801040.SI": "000033.SH", "801110.SI": "000035.SH",
            "801130.SI": "000035.SH", "801140.SI": "000034.SH",
            "801160.SI": "000041.SH", "801170.SI": "000034.SH",
            "801200.SI": "000005.SH", "801210.SI": "000035.SH",
            "801230.SI": "000008.SH", "801710.SI": "000034.SH",
            "801720.SI": "000034.SH", "801740.SI": "000034.SH",
            "801760.SI": "000040.SH", "801770.SI": "000040.SH",
            "801790.SI": "000038.SH", "801890.SI": "000034.SH",
            "801950.SI": "000034.SH", "801960.SI": "000034.SH",
            "801970.SI": "000034.SH", "801980.SI": "000035.SH",
        }.get(sw_code, "000034.SH")
        sw_name = SECTOR_MAP.get(sw_code, "")
        await session.execute(text("""
            INSERT INTO stock_sector_map (ts_code, sw_code, sw_name, sse_code, source, updated_at)
            VALUES (:c, :sw, :n, :sse, 'keyword_cache', NOW())
            ON CONFLICT (ts_code) DO NOTHING
        """), {"c": symbol, "sw": sw_code, "n": sw_name, "sse": sse_code})
        await session.commit()
    except Exception:
        pass

