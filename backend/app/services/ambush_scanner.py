"""潜伏猎手扫描模块 — 四段式过滤."""
import logging, numpy as np, pandas as pd
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger(__name__)
LOOKBACK_DAYS, MAX_ADJUST_DAYS, MAX_DRAWDOWN, VOL_SHRINK_RATIO, MIN_SCORE = 20, 15, 8.0, 0.6, 60
# 创业板参数 (20%涨跌幅, 回撤容忍度更高)
CHINEXT_MAX_DRAWDOWN, CHINEXT_VOL_SHRINK, CHINEXT_MIN_SCORE = 12.0, 0.7, 55

def _is_limit_up(gain_pct: float, symbol: str) -> bool:
    board = symbol[:3] if len(symbol) > 3 else "000"
    return gain_pct >= 19.5 if board in ("300", "688") else gain_pct >= 9.8

def _is_chinext(symbol: str) -> bool:
    code = symbol.replace('.SZ','').replace('.SH','').replace('.BJ','')
    return code.startswith('300') or code.startswith('301') or code.startswith('688')

async def run_ambush_scan(session=None, base_date: date = None, scan_date: date = None) -> dict:
    """潜伏猎手扫描.

    Args:
        base_date: 回看截止日 (默认今天, 从此往前查60天内的涨停)
        scan_date: DB写入的scan_date (默认同base_date, 用于会话去重)
    """
    today = date.today()
    if base_date is None:
        base_date = today
    if scan_date is None:
        scan_date = base_date
    if not session:
        async with async_session_factory() as s:
            return await _do_scan(s, base_date, scan_date)
    return await _do_scan(session, base_date, scan_date)

async def _do_scan(session, base_date: date, scan_date: date) -> dict:
    start = base_date - timedelta(days=LOOKBACK_DAYS * 3)
    result = await session.execute(text("""SELECT ts_code,trade_date,open,close,volume,high,low FROM daily_kline WHERE trade_date>=:s AND trade_date<=:e ORDER BY ts_code,trade_date"""), {"s": start, "e": base_date})
    rows = result.fetchall()
    if not rows: return {"status": "empty", "signals": 0}

    # 预加载股票名称映射
    name_map = {}
    symbols = list(set(r[0] for r in rows))
    r_name = await session.execute(text(
        "SELECT symbol, name FROM stock_name_cache WHERE symbol = ANY(:syms)"
    ), {"syms": symbols})
    for row_n in r_name.fetchall():
        name_map[row_n[0]] = row_n[1]

    df = pd.DataFrame(rows, columns=["ts_code","date","open","close","volume","high","low"])
    df["gain"] = (df["close"] - df["open"]) / df["open"] * 100
    signals = []
    for ts_code, group in df.groupby("ts_code"):
        if len(group) < 20: continue
        group = group.sort_values("date").reset_index(drop=True)
        for i in range(5, len(group)):
            row = group.iloc[i]
            if not _is_limit_up(float(row["gain"]), ts_code): continue
            pre_avg = float(group["volume"].iloc[i-5:i].mean())
            if pre_avg <= 0: continue
            if float(row["volume"]) / pre_avg < 1.8: continue
            adj_rows = group.iloc[i+1:]
            if len(adj_rows) > MAX_ADJUST_DAYS or len(adj_rows) < 3: continue
            adj_high = adj_rows["close"].max()
            adj_low = adj_rows["close"].min()
            lu_close = float(row["close"])
            drawdown = (lu_close - adj_low) / lu_close * 100
            avg_vol = float(adj_rows["volume"].mean())  # 调整期日均量
            # 必须真的回调过: drawdown<2% 说明没回调或创新高, 不是"潜伏"
            if drawdown < 2.0: continue
            # 板块感知上限
            max_dd = CHINEXT_MAX_DRAWDOWN if _is_chinext(ts_code) else MAX_DRAWDOWN
            vol_shrink = CHINEXT_VOL_SHRINK if _is_chinext(ts_code) else VOL_SHRINK_RATIO
            if drawdown > max_dd: continue
            if avg_vol > float(row["volume"]) * vol_shrink: continue
            last_close = float(adj_rows.iloc[-1]["close"])
            if last_close <= 0: continue
            launch_vol = float(adj_rows.iloc[-1]["volume"])
            pre3_avg = float(adj_rows["volume"].iloc[-4:-1].mean()) if len(adj_rows) >= 4 else avg_vol
            if pre3_avg <= 0: continue
            launch_ratio = launch_vol / pre3_avg
            composite = min(100, max(0, 30 - drawdown * 2 + launch_ratio * 15 + (1 - avg_vol/float(row["volume"])) * 30))
            if composite < MIN_SCORE: continue
            signals.append({"symbol": ts_code, "name": name_map.get(ts_code, ts_code), "scan_date": scan_date,
                "limit_up_date": row["date"].date() if hasattr(row["date"],"date") else row["date"],
                "limit_up_gain": round(float(row["gain"]), 2), "max_drawdown": round(drawdown, 2),
                "vol_shrink_ratio": round(avg_vol/float(row["volume"]), 2),
                "launch_vol_ratio": round(launch_ratio, 2), "composite_score": round(composite, 1)})
            break
    if signals:
        try:
            # 先删同 session 旧数据, 再批量插入 (避免依赖 ON CONFLICT 唯一约束)
            await session.execute(text(
                "DELETE FROM ambush_signals WHERE scan_date = :sd"
            ), {"sd": scan_date})
            for sig in signals:
                await session.execute(text(
                    "INSERT INTO ambush_signals (symbol,name,scan_date,limit_up_date,limit_up_gain,max_drawdown,vol_shrink_ratio,launch_vol_ratio,composite_score) "
                    "VALUES(:s,:n,:sd,:ld,:g,:d,:v,:l,:c)"
                ), {
                    "s": sig["symbol"], "n": sig["name"], "sd": sig["scan_date"], "ld": sig["limit_up_date"],
                    "g": sig["limit_up_gain"], "d": sig["max_drawdown"], "v": sig["vol_shrink_ratio"],
                    "l": sig["launch_vol_ratio"], "c": sig["composite_score"],
                })
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.warning("ambush save failed: %s", e)
    return {"status": "success", "signals": len(signals)}
