"""AlphaFlow 乏力度检测器 — 反向五阶段.

浪启动 = 锁死→突破 (已在 Wave V3)
乏力度 = 浪顶→破平台→加速下跌 (本模块)

核心逻辑:
  1. 标记已完成的每一浪的 "平台底部" (浪结束后形成的箱体下沿)
  2. 追踪当前价格相对各层平台的距离
  3. 跌破最上层平台 → 乏力预警
  4. 跌破下层平台 → 趋势确认反转
"""

import logging, numpy as np
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("alphaflow.fatigue")

# ── 分级 ──
# 平台层: 每一轮锁死→爆发结束后形成的震荡区间下沿
# 层级越高(越近)越重要


async def detect_fatigue(symbol: str, scan_date: date = None) -> dict:
    """检测个股是否进入乏力/衰退期.

    Returns:
        {
            status: "active" | "fatigue_warning" | "broken" | "capitulation",
            waves: [{peak_date, peak_price, platform_low, platform_days, broken}],
            current_price, below_platforms, severity
        }
    """
    if scan_date is None:
        scan_date = date.today()

    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT trade_date, open, close, volume, high, low
            FROM daily_kline WHERE ts_code = :c AND trade_date <= :d
            ORDER BY trade_date DESC LIMIT 400
        """), {"c": symbol, "d": scan_date})
        rows_raw = r.fetchall()
    rows = list(reversed(rows_raw))

    if len(rows) < 150:
        return {"status": "insufficient_data", "waves": [], "current_price": None}

    closes = np.array([float(row[2] or 0) for row in rows])
    highs = np.array([float(row[4] or closes[i]) for i, row in enumerate(rows)])
    lows = np.array([float(row[5] or closes[i]) for i, row in enumerate(rows)])
    volumes = np.array([float(row[3] or 0) for row in rows])
    n = len(closes)
    current_price = closes[-1]

    # ── 锁定周期检测 (与训练一致的算法) ──
    LOCK_RANGE = 0.15; LOCK_STD_MAX = 8.0; LOCK_MIN_DAYS = 10
    lock_segments = []
    i = 0
    while i <= n - 20:
        w_c = closes[i:i+20]; wm = float(np.median(w_c))
        in_r = np.all((w_c >= wm*(1-LOCK_RANGE)) & (w_c <= wm*(1+LOCK_RANGE)))
        s2 = float(np.std(w_c)/wm*100)
        if in_r and s2 < LOCK_STD_MAX:
            start = i
            while i < n - 1:
                i += 1
                if i+20 > n: break
                w2c = closes[i:i+20]; w2m = float(np.median(w2c))
                if not np.all((w2c >= w2m*(1-LOCK_RANGE)) & (w2c <= w2m*(1+LOCK_RANGE))): break
                if float(np.std(w2c)/w2m*100) > LOCK_STD_MAX: break
            end = min(i+19, n-1)
            if end - start >= LOCK_MIN_DAYS:
                lock_segments.append((start, end))
        else:
            i += 1

    # ── 对每段锁死, 找其后发生的浪 ──
    waves = []

    for idx, (lock_start, lock_end) in enumerate(lock_segments):
        # 锁死后的爆发: lock_end+1 开始, 找下一个锁死段或数据末尾
        burst_start = lock_end + 1
        if idx < len(lock_segments) - 1:
            burst_end = lock_segments[idx+1][0] - 1
        else:
            burst_end = n - 1

        if burst_end <= burst_start:
            continue

        burst_closes = closes[burst_start:burst_end+1]
        burst_dates = [rows[i][0] for i in range(burst_start, burst_end+1)]
        burst_highs = highs[burst_start:burst_end+1]
        burst_lows = lows[burst_start:burst_end+1]

        peak_idx = int(np.argmax(burst_highs))
        peak_price = float(burst_highs[peak_idx])
        peak_date = rows[burst_start + peak_idx][0] if burst_start + peak_idx < len(rows) else None
        lock_avg_price = float(np.mean(closes[lock_start:lock_end+1]))

        # 浪后平台: 爆发结束后到下一个锁死开始之间的震荡区间
        if idx < len(lock_segments) - 1:
            inter_locks = closes[burst_end:lock_segments[idx+1][0]]
            inter_lows = lows[burst_end:lock_segments[idx+1][0]]
        else:
            # 最后一浪: 爆发结束后的所有数据
            inter_locks = closes[burst_end:]
            inter_lows = lows[burst_end:]

        if len(inter_locks) >= 10:
            platform_low = float(np.percentile(inter_lows, 10))   # 10分位低点
            platform_high = float(np.percentile(closes[burst_end:burst_end+len(inter_locks)], 90) if burst_end < n else platform_low * 1.2)
        else:
            platform_low = lock_avg_price * 0.85
            platform_high = lock_avg_price * 1.15

        # 判定当前价格 vs 平台
        broken = current_price < platform_low * 0.97  # 3%容差
        distance_pct = (current_price - platform_low) / platform_low * 100

        waves.append({
            "lock_start": str(rows[lock_start][0]) if lock_start < len(rows) else "",
            "lock_end": str(rows[lock_end][0]) if lock_end < len(rows) else "",
            "peak_date": str(peak_date) if peak_date else "",
            "peak_price": round(peak_price, 2),
            "platform_low": round(platform_low, 2),
            "platform_high": round(platform_high, 2),
            "platform_days": len(inter_locks),
            "broken": broken,
            "distance_pct": round(distance_pct, 1),
            "lock_avg": round(lock_avg_price, 2),
        })

    if not waves:
        return {"status": "no_waves_found", "waves": [], "current_price": round(float(current_price), 2)}

    # ── 乏力度判定 ──
    n_broken = sum(1 for w in waves if w["broken"])
    last_wave = waves[-1]
    first_wave = waves[0] if waves else last_wave

    # 多层判断
    broken_current_platform = last_wave["broken"]                                   # 跌破最近平台
    broken_all_platforms = n_broken >= len(waves)                                    # 全部跌穿
    broken_first_platform = first_wave["broken"]                                     # 跌穿最早的平台 (最致命)
    distance_from_first = float(first_wave["distance_pct"])

    if broken_first_platform and distance_from_first < -15:
        status = "capitulation"
        label = "崩塌 — 跌穿多轮平台, 大势已去"
        severity = 10
    elif broken_current_platform and n_broken >= 2:
        status = "broken"
        label = f"趋势已破 — 跌穿{n_broken}轮平台, 不建议参与"
        severity = 7
    elif broken_current_platform:
        status = "fatigue_warning"
        label = "乏力预警 — 最近平台已破, 关注下一平台支撑"
        severity = 4
    elif distance_from_first < 0 and distance_from_first > -10:
        status = "testing_support"
        label = "测试支撑 — 价格接近关键支撑位, 方向未定"
        severity = 2
    else:
        status = "active"
        label = "活跃 — 当前位置在多层平台之上, 趋势良好"
        severity = 0

    return {
        "symbol": symbol,
        "scan_date": str(scan_date),
        "status": status,
        "label": label,
        "severity": severity,
        "current_price": round(float(current_price), 2),
        "waves": waves,
        "n_waves": len(waves),
        "n_broken_platforms": n_broken,
        "broken_all": broken_all_platforms,
    }
