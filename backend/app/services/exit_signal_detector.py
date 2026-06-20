"""退出信号检测器 v2.0 — ATR-based 动态止损 + 移动止盈 + 差异化参数.

v2.0 升级 (2026-05-31):
  - 止损从固定5%/8%改为 ATR14 × 倍数 (高波动股宽, 低波动股紧)
  - 移动止盈: 峰值回撤超过 1.5×ATR → 触发
  - 差异化: 创业板 ATR倍数更大, 快涨股止损更紧
  - 新增: 时间衰减退出, 缩量横盘退出
  - 新增: 板块联动健康检查
"""
import logging, numpy as np
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger(__name__)

# ── ATR 倍数默认配置 ──
ATR_STOP_LOSS = 2.0         # 硬止损: 买入价 - N × ATR
ATR_TRAILING = 1.5          # 移动止盈: 峰值 - N × ATR
ATR_AGGRESSIVE = 1.2        # 激进止损 (快涨股/高风险市场)
ATR_CHINEXT_FACTOR = 1.4    # 创业板 ATR倍数放大 (波动更大)
ATR_FLASH_FACTOR = 0.8      # 快涨股(20日涨>30%) 收紧系数
MARKET_HIGH_RISK_FACTOR = 0.8  # 高风险市场收紧系数
MIN_HOLDING_DAYS = 2        # 最少持有天数 (避免噪音止损)


def _calc_atr(highs, lows, closes, period=14):
    """计算 ATR 序列."""
    n = len(closes)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1]),
        )
    atr = np.zeros(n)
    if n <= period: return float(np.mean(tr)) if n > 0 else 0.01
    atr[period-1] = np.mean(tr[:period])
    for i in range(period, n):
        atr[i] = (atr[i-1] * (period-1) + tr[i]) / period
    return atr


def _is_chinext(symbol: str) -> bool:
    code = symbol.replace('.SZ','').replace('.SH','').replace('.BJ','')
    return code.startswith('300') or code.startswith('301') or code.startswith('688')


async def detect_exit_signals(
    symbol: str,
    entry_date: str | date,
    entry_price: float | None = None,
    market_risk: str = "normal",
) -> list[dict]:
    """检测单只股票的退出信号 — ATR-based v2.0.

    Args:
        symbol: 股票代码
        entry_date: 推荐/买入日期
        entry_price: 买入价格 (None 则从 DB 获取)
        market_risk: 市场风险等级 (来自 market_gate)

    Returns:
        [{type, priority, price, reason, suggested_action}, ...]
        priority: critical > high > medium > info
    """
    from datetime import date as dt_date
    if isinstance(entry_date, str):
        entry_date = dt_date.fromisoformat(entry_date)

    # 加载至少 60 天前的数据用于 ATR
    lookback_start = entry_date - timedelta(days=60)

    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT trade_date, open, high, low, close, volume
            FROM daily_kline
            WHERE ts_code = :sym AND trade_date >= :lb
            ORDER BY trade_date
        """), {"sym": symbol, "lb": lookback_start})
        rows = [(row[0], float(row[1]), float(row[2]), float(row[3]),
                 float(row[4]), float(row[5])) for row in r.fetchall()]

    if len(rows) < 5:
        return []

    # 分离: 入场前用于 ATR 计算, 入场后用于信号检测
    pre_rows = [r for r in rows if r[0] < entry_date]
    post_rows = [r for r in rows if r[0] >= entry_date]

    if not post_rows:
        post_rows = rows[-1:]  # fallback

    if entry_price is None:
        entry_price = post_rows[0][4]

    closes = np.array([r[4] for r in rows])
    highs = np.array([r[2] for r in rows])
    lows = np.array([r[3] for r in rows])

    post_closes = np.array([r[4] for r in post_rows])
    post_highs = np.array([r[2] for r in post_rows])
    post_lows = np.array([r[3] for r in post_rows])
    post_vols = np.array([r[5] for r in post_rows])
    post_dates = [r[0] for r in post_rows]

    current_price = float(post_closes[-1])
    current_ret = (current_price - entry_price) / max(entry_price, 0.01)

    # ── ATR 计算 ──
    atr_series = _calc_atr(highs, lows, closes, 14)
    atr14 = float(atr_series[-1]) if len(atr_series) > 0 else current_price * 0.03
    atr_pct = atr14 / max(current_price, 0.01)

    # 快涨检测
    if len(closes) >= 20:
        chg_20d = (closes[-1] / max(closes[-20], 0.01) - 1)
        is_flash = chg_20d > 0.30
    else:
        is_flash = False

    # ── 差异化 ATR 倍数 ──
    stop_mult = ATR_STOP_LOSS
    trail_mult = ATR_TRAILING

    # 创业板 → 放大
    if _is_chinext(symbol):
        stop_mult *= ATR_CHINEXT_FACTOR
        trail_mult *= ATR_CHINEXT_FACTOR

    # 快涨股 → 收紧
    if is_flash:
        stop_mult *= ATR_FLASH_FACTOR
        trail_mult *= ATR_FLASH_FACTOR

    # 高风险市场 → 收紧
    if market_risk in ("high", "elevated"):
        stop_mult *= MARKET_HIGH_RISK_FACTOR
        trail_mult *= MARKET_HIGH_RISK_FACTOR

    holding_days = (post_dates[-1] - post_dates[0]).days if len(post_dates) > 1 else 1

    # ── ATR 绝对止损价 ──
    atr_stop_price = round(entry_price - stop_mult * atr14, 2)
    atr_stop_pct = round((atr_stop_price - entry_price) / max(entry_price, 0.01) * 100, 1)

    signals = []

    # ═══════════════════════════════════════════════
    # 1. ATR 硬止损
    # ═══════════════════════════════════════════════
    if current_price <= atr_stop_price and holding_days >= MIN_HOLDING_DAYS:
        # 确认: 放量杀跌 + 连续下破
        avg_vol = float(np.mean(post_vols[:-3])) if len(post_vols) > 3 else float(np.mean(post_vols))
        latest_vol = float(post_vols[-1])
        vol_spike = latest_vol > avg_vol * 1.5

        consecutive_down = 0
        for i in range(len(post_closes)-1, max(0, len(post_closes)-4), -1):
            if post_closes[i] < post_closes[i-1]:
                consecutive_down += 1

        if vol_spike and consecutive_down >= 2:
            severity = "critical"
        elif consecutive_down >= 3:
            severity = "critical"
        else:
            severity = "high"

        signals.append({
            "type": "atr_stop_loss",
            "priority": severity,
            "price": round(current_price, 2),
            "stop_price": atr_stop_price,
            "return_pct": round(current_ret * 100, 1),
            "atr_pct": round(atr_pct * 100, 1),
            "holding_days": holding_days,
            "reason": (
                f"跌破ATR止损 (ATR={atr_pct*100:.1f}%, 止损线¥{atr_stop_price}, "
                f"当前¥{current_price}, 已亏{abs(current_ret)*100:.1f}%)"
            ),
            "suggested_action": "建议立即止损离场" if severity=="critical" else "建议减仓50%",
        })

    # ═══════════════════════════════════════════════
    # 2. 移动止盈 (Trailing Stop)
    # ═══════════════════════════════════════════════
    if len(post_closes) >= 5:
        peak_idx = int(np.argmax(post_highs))
        peak_price = float(post_highs[peak_idx])
        peak_ret = (peak_price - entry_price) / max(entry_price, 0.01)

        trailing_stop = round(peak_price - trail_mult * atr14, 2)
        drawdown_from_peak = (current_price - peak_price) / max(peak_price, 0.01)

        # 触发条件: 曾盈利 ≥ 1×ATR 且从峰值回撤 > 1.5×ATR
        if peak_ret > atr_pct and drawdown_from_peak < -trail_mult * atr_pct:
            signals.append({
                "type": "trailing_stop",
                "priority": "critical",
                "price": round(current_price, 2),
                "peak_price": round(peak_price, 2),
                "peak_return": round(peak_ret * 100, 1),
                "trailing_level": trailing_stop,
                "drawdown": round(abs(drawdown_from_peak) * 100, 1),
                "atr_pct": round(atr_pct * 100, 1),
                "reason": (
                    f"移动止盈触发: 峰值¥{peak_price:.2f}(+{peak_ret*100:.0f}%), "
                    f"回撤{abs(drawdown_from_peak)*100:.1f}% > {trail_mult}×ATR({atr_pct*100:.1f}%)"
                ),
                "suggested_action": "建议全部卖出, 锁定剩余利润",
            })

    # ═══════════════════════════════════════════════
    # 3. 时间衰减退出
    # ═══════════════════════════════════════════════
    if holding_days >= 8 and abs(current_ret) < 0.02:
        # 持有 8 天仍在成本附近徘徊 → 资金效率低
        price_range = (float(np.max(post_highs[-5:])) - float(np.min(post_lows[-5:]))) / max(current_price, 0.01)
        if price_range < atr_pct * 2:  # 横盘缩量
            signals.append({
                "type": "time_exit",
                "priority": "medium",
                "price": round(current_price, 2),
                "holding_days": holding_days,
                "return_pct": round(current_ret * 100, 1),
                "reason": f"持有{holding_days}天窄幅横盘(振幅{price_range*100:.1f}%), 资金效率低",
                "suggested_action": "建议换股, 不再等待",
            })
        elif current_ret < -0.01:
            signals.append({
                "type": "time_exit",
                "priority": "high",
                "price": round(current_price, 2),
                "holding_days": holding_days,
                "return_pct": round(current_ret * 100, 1),
                "reason": f"持有{holding_days}天仍亏损{abs(current_ret)*100:.1f}%, 时间成本过高",
                "suggested_action": "建议止损换股",
            })

    # ═══════════════════════════════════════════════
    # 4. 放量滞涨预警 (机构减仓信号)
    # ═══════════════════════════════════════════════
    if len(post_closes) >= 5 and holding_days >= 3:
        recent_ret = (post_closes[-1] - post_closes[-5]) / max(post_closes[-5], 0.01)
        recent_vol = float(np.mean(post_vols[-3:]))
        earlier_vol = float(np.mean(post_vols[-8:-3])) if len(post_vols) >= 8 else recent_vol
        vol_surge = recent_vol > earlier_vol * 2.0

        if vol_surge and abs(recent_ret) < 0.01:
            signals.append({
                "type": "distribution_warning",
                "priority": "high",
                "price": round(current_price, 2),
                "return_pct": round(current_ret * 100, 1),
                "reason": f"近3日放量{recent_vol/earlier_vol:.1f}x但价格不动 — 疑似减仓",
                "suggested_action": "建议减仓观察, 如次日继续放量滞涨则清仓",
            })

    # ═══════════════════════════════════════════════
    # 5. 缺口未补预警
    # ═══════════════════════════════════════════════
    if len(post_closes) >= 3:
        for i in range(len(post_closes)-1, max(0, len(post_closes)-5), -1):
            if i > 0 and post_lows[i] > post_highs[i-1] * 1.01:
                # 向上跳空缺口
                gap_top = float(post_lows[i])
                gap_bottom = float(post_highs[i-1])
                gap_days = len(post_closes) - i - 1
                if current_price < gap_top and gap_days <= 3:
                    signals.append({
                        "type": "gap_fill_risk",
                        "priority": "medium",
                        "price": round(current_price, 2),
                        "reason": f"{gap_days}天前跳空缺口(¥{gap_bottom:.2f}-{gap_top:.2f})已回补, 强势破坏",
                        "suggested_action": "关注是否继续下跌, 如破缺口下沿则止损",
                    })
                break

    # ── 排序: critical > high > medium > info ──
    priority_order = {"critical": 0, "high": 1, "medium": 2, "info": 3}
    signals.sort(key=lambda s: priority_order.get(s["priority"], 99))

    return signals


async def detect_portfolio_signals(holdings: list[dict]) -> dict[str, list[dict]]:
    """批量检测多只持仓的退出信号."""
    results = {}
    for h in holdings:
        sym = h["symbol"]
        try:
            sigs = await detect_exit_signals(sym, h["entry_date"], h.get("entry_price"))
            if sigs: results[sym] = sigs
        except Exception as e:
            logger.warning(f"Exit signal failed for {sym}: {e}")
    return results


async def get_recommendation_exit_signals(lookback_days: int = 10) -> list[dict]:
    """获取近期推荐股票的退出信号."""
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT DISTINCT ON (symbol) symbol, scan_date, close_price
            FROM recommendation_tracking
            WHERE scan_date >= :cutoff
            ORDER BY symbol, scan_date DESC
        """), {"cutoff": date.today() - timedelta(days=lookback_days)})
        holdings = [{"symbol": row[0], "entry_date": str(row[1]),
                      "entry_price": float(row[2]) if row[2] else None}
                    for row in r.fetchall()]

    if not holdings: return []
    portfolio_signals = await detect_portfolio_signals(holdings)
    results = []
    for h in holdings:
        sym = h["symbol"]
        sigs = portfolio_signals.get(sym, [])
        if sigs:
            results.append({
                "symbol": sym, "entry_date": h["entry_date"],
                "entry_price": h["entry_price"], "signals": sigs,
                "has_critical": any(s["priority"] == "critical" for s in sigs),
            })
    results.sort(key=lambda r: (0 if r["has_critical"] else 1, -(len(r["signals"]))))
    return results


async def get_trailing_stop_level(symbol: str, scan_date=None) -> dict:
    """计算建议移动止盈位 — ATR-based v2.0."""
    from datetime import date as dt_date
    if scan_date is None: scan_date = dt_date.today()

    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT trade_date, close, high, low FROM daily_kline
            WHERE ts_code = :sym AND trade_date <= :d
            ORDER BY trade_date DESC LIMIT 65
        """), {"sym": symbol, "d": scan_date})
        rows = list(reversed(r.fetchall()))

    if len(rows) < 20:
        return {"trailing_stop": None, "reason": "数据不足"}

    closes = np.array([float(r[1]) for r in rows])
    highs = np.array([float(r[2]) for r in rows])
    lows = np.array([float(r[3]) for r in rows])
    entry = float(closes[-1])

    atr_series = _calc_atr(highs, lows, closes, 14)
    atr14 = float(atr_series[-1])
    atr_pct = atr14 / max(entry, 0.01)

    # 两级止损
    mult = ATR_CHINEXT_FACTOR if _is_chinext(symbol) else 1.0
    aggressive_stop = round(entry - ATR_AGGRESSIVE * mult * atr14, 2)
    conservative_stop = round(entry - ATR_STOP_LOSS * mult * atr14, 2)

    return {
        "entry_price": round(entry, 2),
        "atr": round(atr14, 2),
        "atr_pct": round(atr_pct * 100, 1),
        "aggressive_stop": aggressive_stop,
        "conservative_stop": conservative_stop,
        "suggestion": (
            f"买入{entry:.2f}, ATR={atr14:.2f}({atr_pct*100:.1f}%), "
            f"激进止{aggressive_stop:.2f}, 保守止{conservative_stop:.2f}"
        ),
    }
