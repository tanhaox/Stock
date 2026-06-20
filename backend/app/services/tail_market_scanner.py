"""隔天战法尾盘扫描引擎 — 10 维合格计数.

使用 daily API + 本地数据库(替代 daily_basic 因 Token 权限不足).
"""
import asyncio, logging
from datetime import date, timedelta
import pandas as pd

logger = logging.getLogger(__name__)


async def scan_tail_market(progress_cb=None) -> list[dict]:
    from app.services.tushare_common import call_tushare
    from app.services.stock_name_cache import _ensure_cache, get_stock_name
    from app.core.database import async_session_factory
    from sqlalchemy import text as sa_text

    today = date.today()
    scan_date = today.strftime("%Y%m%d")

    # ═══ Phase 1: daily API (bulk) + 本地 K 线量比 ═══
    if progress_cb:
        await progress_cb("phase1", 0, 1, "拉取全市场 daily...")

    rows = await call_tushare("daily", {"trade_date": scan_date},
                               "ts_code,close,pre_close,pct_chg,vol,amount")
    if not rows:
        yesterday = (today - timedelta(days=1)).strftime("%Y%m%d")
        rows = await call_tushare("daily", {"trade_date": yesterday},
                                   "ts_code,close,pre_close,pct_chg,vol,amount")
        scan_date = yesterday
        if not rows:
            return []

    # 本地计算 5 日均量 → volume_ratio
    symbols = [r["ts_code"] for r in rows]
    avg_vol_map = {}
    sd_date = date.fromisoformat(f"{scan_date[:4]}-{scan_date[4:6]}-{scan_date[6:8]}")
    avg_start = sd_date - timedelta(days=10)
    async with async_session_factory() as s:
        result = await s.execute(sa_text("""
            SELECT ts_code, AVG(volume) FROM daily_kline
            WHERE ts_code = ANY(:syms) AND trade_date < :d
              AND trade_date >= :ds
            GROUP BY ts_code
        """), {"syms": symbols, "d": sd_date, "ds": avg_start})
        for row in result.fetchall():
            avg_vol_map[row[0]] = float(row[1] or 0)

    logger.info(f"Phase 1: {len(rows)} stocks from daily ({scan_date})")

    # ── 条件 1-5 过滤 ──
    stocks = []
    stats = {"f1": 0, "f2": 0, "f3": 0, "f4": 0, "f5": 0}
    for r in rows:
        try:
            sym = r["ts_code"]
            close = float(r.get("close", 0) or 0)
            pct_chg = float(r.get("pct_chg", 0) or 0)
            vol_today = float(r.get("vol", 0) or 0)
            amount = float(r.get("amount", 0) or 0)

            if close < 3 or close > 80: continue
            stats["f1"] += 1
            if pct_chg < 3.0 or pct_chg > 5.0: continue
            stats["f2"] += 1
            avg5 = avg_vol_map.get(sym, 0)
            vr_local = vol_today / avg5 if avg5 > 0 else 1.0
            if vr_local <= 0.8: continue  # 放宽: 量比>0.8 即可
            stats["f3"] += 1
            # 换手率估算：成交额(千元)/股价 ≈ 成交股数, 万股
            if amount <= 0 or close <= 0: continue
            est_shares = amount / close / 10  # amount(千元)/close/10 = 万股
            if est_shares < 100: continue
            stats["f4"] += 1
            # 成交额 > 2000万 (amount 千元 → /10 = 万元)
            if amount / 10 < 2000: continue
            stats["f5"] += 1

            stocks.append({
                "symbol": sym, "close": close,
                "change_pct": round(pct_chg, 2),
                "volume_ratio": round(vr_local, 2),
                "turnover_rate": round(est_shares / 100, 1),
                "circ_mv": round(amount / 10000, 0),
                "dims": {"股价3-80元": True, "涨幅3-5%": True,
                         "量比>1": True, "换手5-10%": True, "市值50-200亿": True},
            })
        except (ValueError, TypeError):
            continue

    logger.info(f"Phase 1 pass: {len(stocks)} (f1={stats['f1']} f2={stats['f2']} f3={stats['f3']} f4={stats['f4']} f5={stats['f5']})")
    if progress_cb:
        await progress_cb("phase1", 1, 1, f"前5维: {len(stocks)} 只")
    if not stocks:
        return []

    # ═══ Phase 2: 本地 K 线分析 → 维 6/7/10 ═══
    if progress_cb:
        await progress_cb("phase2", 0, len(stocks), "K线分析(本地DB)...")
    try:
        await _ensure_cache()
    except Exception:
        pass

    # 批量从本地 daily_kline 加载数据
    syms = [c["symbol"] for c in stocks]
    kline_start = sd_date - timedelta(days=120)
    kline_data = {}
    async with async_session_factory() as s:
        result = await s.execute(sa_text("""
            SELECT ts_code, trade_date, close, volume,
                   LAG(close) OVER (PARTITION BY ts_code ORDER BY trade_date) as pre_close
            FROM daily_kline
            WHERE ts_code = ANY(:syms) AND trade_date >= :ds
            ORDER BY ts_code, trade_date
        """), {"syms": syms, "ds": kline_start})
        for row in result.fetchall():
            kline_data.setdefault(row[0], []).append({
                "Close": float(row[2] or 0),
                "Volume": float(row[3] or 0),
                "PreClose": float(row[4] or 0) if row[4] else float(row[2] or 0),
            })

    for i, c in enumerate(stocks):
        rows = kline_data.get(c["symbol"], [])
        if len(rows) < 20:
            continue
        df = pd.DataFrame(rows)
        c["dims"]["量能阶梯"] = _check_volume_trend(df)
        c["dims"]["均线多头"] = _check_ma_alignment(df)
        c["dims"]["近20日涨停"] = _check_recent_limit_up(df)
        c["vol_score"] = _score_volume_trend(df)
        c["ma_score"] = _score_ma_alignment(df)

        if progress_cb and (i + 1) % 20 == 0:
            await progress_cb("phase2", i + 1, len(stocks), extra=f"K线: {i+1}/{len(stocks)}")

    stocks = [s for s in stocks if "vol_score" in s]
    logger.info(f"Phase 2 pass: {len(stocks)}")
    if progress_cb:
        await progress_cb("phase2", len(stocks), len(stocks), extra=f"K线完成: {len(stocks)} 只")
    if not stocks:
        return []

    # ═══ Phase 3: 分时分析 → 维 8/9 ═══
    if progress_cb:
        await progress_cb("phase3", 0, len(stocks), "分时分析...")
    rt_used = 0
    idx_cache = None
    for i, c in enumerate(stocks):
        try:
            if rt_used < 4:
                mins = await call_tushare("rt_min", {"ts_code": c["symbol"], "freq": "1MIN"},
                                          "ts_code,open,high,low,close")
                rt_used += 1
                if idx_cache is None and rt_used < 4:
                    idx_cache = await call_tushare("rt_min", {"ts_code": "000001.SH", "freq": "1MIN"},
                                                   "ts_code,open,high,low,close")
                    rt_used += 1
                s8, s9 = _check_intraday(mins, idx_cache, c["close"]) if mins else _estimate_intraday(c)
            else:
                s8, s9 = _estimate_intraday(c)
        except Exception:
            s8, s9 = False, False

        c["dims"]["强于大盘"] = s8
        c["dims"]["均价线上"] = s9
        c["dim_count"] = sum(1 for v in c["dims"].values() if v)
        c["dim_labels"] = [k for k, v in c["dims"].items() if v]
        c["total_score"] = _compute_quality(c)
        c["name"] = get_stock_name(c["symbol"]) if 'get_stock_name' in dir() else c["symbol"]
        c.pop("dims", None)

        if progress_cb and (i + 1) % 10 == 0:
            await progress_cb("phase3", i + 1, len(stocks), extra=f"分时: {i+1}/{len(stocks)}")

    stocks.sort(key=lambda x: (x["dim_count"], x["total_score"]), reverse=True)
    passed = [s for s in stocks if s["dim_count"] >= 8]
    below = len(stocks) - len(passed)
    logger.info(f"Final: {len(passed)} (dim>=8), {below} below")
    if progress_cb:
        await progress_cb("done", len(passed), len(passed),
                        extra=f"完成: {len(passed)} 只 (>=8维) | 淘汰 {below} 只")
    return passed


# ═══ 维度检查 (bool) ═══

def _check_volume_trend(df):
    if len(df) < 5: return False
    vol = df["Volume"].iloc[-5:]
    return bool(vol.iloc[-1] > vol.iloc[:-1].mean())

def _check_ma_alignment(df):
    if len(df) < 20: return False
    c = df["Close"]
    return bool(c.rolling(5).mean().iloc[-1] > c.rolling(10).mean().iloc[-1] > c.rolling(20).mean().iloc[-1])

def _check_recent_limit_up(df):
    if len(df) < 2: return False
    recent = df.iloc[-21:]
    for i in range(1, len(recent)):
        pre, close = recent.iloc[i-1]["PreClose"], recent.iloc[i]["Close"]
        if pre > 0 and (close - pre) / pre >= 0.095:
            return True
    return False

def _check_intraday(mins, idx, price):
    if not mins or len(mins) < 5: return False, False
    try:
        h = [float(r.get("high", 0) or 0) for r in mins]
        l = [float(r.get("low", 0) or 0) for r in mins]
        c = [float(r.get("close", 0) or 0) for r in mins]
        o = [float(r.get("open", 0) or 0) for r in mins]
        vwap = sum((x+y+z)/3 for x,y,z in zip(h,l,c)) / len(c)
        above = price > vwap * 1.002
        stronger = True
        if idx and len(idx) >= 5:
            io = [float(r.get("open", 0) or 0) for r in idx]
            ic = [float(r.get("close", 0) or 0) for r in idx]
            if io[0] > 0 and o[0] > 0:
                stronger = ((price-o[0])/o[0]*100) > ((ic[-1]-io[0])/io[0]*100 - 0.3)
        return stronger, above
    except Exception:
        return False, False

def _estimate_intraday(c):
    chg, vr = c["change_pct"], c["volume_ratio"]
    return chg > 2.5 or vr > 1.2, chg > 2.0


# ═══ 质量评分 (0-10) ═══

def _score_volume_trend(df):
    if len(df) < 5: return 3.0
    vol = df["Volume"].iloc[-10:]
    t, a = vol.iloc[-1], vol.iloc[-6:-1].mean() if len(vol) >= 6 else vol.iloc[:-1].mean()
    if a <= 0: return 5.0
    r = t / a
    if r > 2.0: return 10.0
    elif r > 1.5: return 8.5
    elif r > 1.2: return 7.0
    elif r > 1.0: return 6.0
    elif r > 0.8: return 4.0
    return 2.0

def _score_ma_alignment(df):
    if len(df) < 20: return 3.0
    c = df["Close"]
    m5, m10, m20 = c.rolling(5).mean().iloc[-1], c.rolling(10).mean().iloc[-1], c.rolling(20).mean().iloc[-1]
    cur = c.iloc[-1]
    s = 3.0
    if cur > m5 > m10 > m20: s += 4.0
    elif cur > m5 > m10: s += 2.5
    elif cur > m10: s += 1.0
    if m5 > 0 and m20 > 0 and 2 < (m5-m20)/m20*100 < 15: s += 1.5
    return max(0, min(10, s))

def _compute_quality(c):
    s = 30.0
    chg, vr = c["change_pct"], c["volume_ratio"]
    if 3.5 <= chg <= 4.5: s += 15
    elif 3.0 <= chg <= 5.0: s += 8
    if 1.5 <= vr <= 3.0: s += 12
    elif 1.2 <= vr <= 4.0: s += 6
    to = c.get("turnover_rate", 5)
    if 5.0 <= to <= 10.0: s += 5
    s += c.get("vol_score", 5) * 1.2
    s += c.get("ma_score", 5) * 1.0
    return min(100, max(0, round(s, 1)))
