"""AlphaFlow 共享特征提取 — 训练和预测统一使用此模块.

48维(V3): 26维波浪周期 + 3维蛋/鹅 + 8维 SXQS资金博弈 + 2维环境 + 6维老兵增强 + 2维环境 + 1维 TG反哺
"""
import numpy as np
from datetime import date

LOCK_RANGE = 0.15; LOCK_STD_MAX = 8.0; LOCK_MIN_DAYS = 10

FEAT_NAMES = [
    "锁周期数","当前第几轮","锁死天数","锁死强度",
    "锁死均量","周期加速比","量趋势","爆发趋势",
    "已完成浪数","浪间隔","加速锁死","缩量吸筹",
    "爆发递增","区间位置","5日涨幅","明显上涨",
    "红量连续","最近红量","红量比10d","红放最长",
    "红放最近","是否锁死","第3轮+","第2轮加速","距上轮天数","锁周期总数",
    "第一轮均价","第一轮涨幅%","蛋奖励",
    "H1_H2方向","距H3基差%","买力VAR6","卖力VAR7","净买力",
    "力波动VAR8","A买入信号","近低点超卖","ZIG_D买","ZIG_W卖",
    "大盘锁死期%","板块锁死期%",
    # ★ V2 老兵增强 (6维)
    "老兵_锁死进度比","老兵_振幅收敛度","老兵_量能萎缩率",
    "老兵_历史浪均幅","老兵_历史浪中位幅","老兵_当前振幅",
    # ★ V3: TG 反哺特征 (1维)
    "tg_composite_score",
]


def compute_sxqs_features(closes, highs, lows):
    """计算 SXQS 资金博弈 8 维特征."""
    n = len(closes)
    alpha6, alpha18, alpha108 = 2/7, 2/19, 2/109

    h1_arr = np.zeros(n); h2_arr = np.zeros(n); h3_arr = np.zeros(n)
    h1_arr[0] = closes[0]
    for i in range(1, n): h1_arr[i] = alpha6*closes[i] + (1-alpha6)*h1_arr[i-1]
    h2_arr[0] = h1_arr[0]
    for i in range(1, n): h2_arr[i] = alpha18*h1_arr[i] + (1-alpha18)*h2_arr[i-1]
    if n >= 108:
        h3_arr[107] = np.mean(closes[:108])
        for i in range(108, n): h3_arr[i] = alpha108*closes[i] + (1-alpha108)*h3_arr[i-1]
    else:
        h3_arr = np.full(n, np.mean(closes))

    h1_h2_up = 1.0 if h1_arr[-1] > h2_arr[-1] else 0.0
    h3_dev = (closes[-1]-h3_arr[-1])/h3_arr[-1]*100 if h3_arr[-1] > 0 else 0.0

    tr = np.maximum(highs-lows, np.abs(highs-np.roll(closes, 1))); tr[0] = highs[0]-lows[0]
    var1_arr = np.array([np.sum(tr[max(0,i-24):i+1]) for i in range(n)])

    v2 = highs - np.roll(highs, 1); v2[0] = 0
    v3 = np.roll(lows, 1) - lows; v3[0] = 0
    buy_mask = (v2 > 0) & (v2 > v3); sell_mask = (v3 > 0) & (v3 > v2)

    var6_arr = np.zeros(n); var7_arr = np.zeros(n)
    for i in range(n):
        s, e = max(0, i-24), i+1
        var6_arr[i] = np.sum(np.where(buy_mask[s:e], v2[s:e], 0)) * 100 / max(var1_arr[i], 0.01)
        var7_arr[i] = np.sum(np.where(sell_mask[s:e], v3[s:e], 0)) * 100 / max(var1_arr[i], 0.01)

    var8_arr = np.zeros(n)
    for i in range(14, n):
        div = np.abs(var7_arr[i-14:i+1]-var6_arr[i-14:i+1])/(var7_arr[i-14:i+1]+var6_arr[i-14:i+1])*100
        var8_arr[i] = np.mean(div)

    a_signal = 1.0 if (var7_arr[-1] > var6_arr[-1] and var7_arr[-1] > 25 and var6_arr[-1] < 25) else 0.0
    near_low = 1.0 if (n >= 20 and closes[-1] <= np.min(lows[-20:])*1.03) else 0.0
    net_power = float(var6_arr[-1] - var7_arr[-1])

    # ── ZIG 拐点检测 (SXQS核心缺失的部分) ──
    # ZIG(3, 10): 价格转向≥10%反转, 3日确认
    d_signal, w_signal, zig_direction = _compute_zig_signals(highs, lows, closes, n)

    return {
        "h1h2_up": float(h1_h2_up), "h3_dev": float(h3_dev),
        "var6": float(var6_arr[-1]), "var7": float(var7_arr[-1]),
        "net_power": float(net_power), "var8": float(var8_arr[-1]),
        "a_signal": float(a_signal), "near_low": float(near_low),
        "d_signal": float(d_signal), "w_signal": float(w_signal),
        "zig_direction": zig_direction,
    }


def _compute_zig_signals(highs, lows, closes, n):
    """ZIG转向指标近似 + D/W信号.

    ZIG(3, 10): 价格单向偏离≥10%才确认转向
      - 上升中: ZIG线跟随highs上行, 直到close跌破ZIG的(1-10%) → 转向
      - 下降中: ZIG线跟随lows下行, 直到close突破ZIG的(1+10%) → 转向

    D(买): ZIG上穿MA(ZIG, 2) → 趋势反转向上的确认点
    W(卖): MA(ZIG, 2)下穿ZIG → 趋势反转向下的确认点
    """
    REVERSAL_PCT = 0.10  # ZIG(3,10)的10%反转阈值
    zig = np.zeros(n)
    zig[0] = closes[0]
    direction = 1  # 1=上升, -1=下降
    last_extreme = closes[0]
    extreme_idx = 0

    for i in range(1, n):
        if direction == 1:  # 上升中 → ZIG跟随highs
            if highs[i] > last_extreme:
                last_extreme = highs[i]
                extreme_idx = i
            zig[i] = last_extreme
            # 转向条件: 收盘价跌破极值的(1-REVERSAL_PCT)
            if closes[i] < last_extreme * (1 - REVERSAL_PCT):
                direction = -1
                last_extreme = lows[i]
                extreme_idx = i
        else:  # 下降中 → ZIG跟随lows
            if lows[i] < last_extreme:
                last_extreme = lows[i]
                extreme_idx = i
            zig[i] = last_extreme
            if closes[i] > last_extreme * (1 + REVERSAL_PCT):
                direction = 1
                last_extreme = highs[i]
                extreme_idx = i

    # MA(ZIG, 2)
    zig_ma2 = np.convolve(zig, np.ones(2)/2, mode='same')
    zig_ma2[0] = zig[0]

    # D信号: ZIG上穿 MA(ZIG,2) — 最近3天内
    # W信号: MA(ZIG,2)下穿 ZIG — 最近3天内
    d_signal = 0.0; w_signal = 0.0
    for offset in [0, 1, 2]:
        idx = n - 1 - offset
        prev = idx - 1
        if prev < 0: continue
        if zig[prev] <= zig_ma2[prev] and zig[idx] > zig_ma2[idx]:
            d_signal = 1.0
            break
        if zig_ma2[prev] <= zig[prev] and zig_ma2[idx] > zig[idx]:
            w_signal = 1.0
            break

    return d_signal, w_signal, "up" if direction == 1 else "down"


def _lock_market_return(stock_closes, index_closes, lock_segments):
    """第一次锁死期间的大盘收益率."""
    if not lock_segments or index_closes is None or len(index_closes) < len(stock_closes):
        return 0.0
    s0, e0, _, _ = lock_segments[0]
    if e0 >= len(index_closes) or s0 >= len(index_closes):
        return 0.0
    idx_start = float(index_closes[s0]) if index_closes[s0] > 0 else 1.0
    idx_end = float(index_closes[e0]) if index_closes[e0] > 0 else idx_start
    return (idx_end - idx_start) / idx_start * 100


def _lock_sector_return(stock_closes, sector_closes, lock_segments):
    """第一次锁死期间的板块收益率."""
    if not lock_segments or sector_closes is None or len(sector_closes) < len(stock_closes):
        return 0.0
    s0, e0, _, _ = lock_segments[0]
    if e0 >= len(sector_closes) or s0 >= len(sector_closes):
        return 0.0
    sec_start = float(sector_closes[s0]) if sector_closes[s0] > 0 else 1.0
    sec_end = float(sector_closes[e0]) if sector_closes[e0] > 0 else sec_start
    return (sec_end - sec_start) / sec_start * 100


def compute_wave_features(closes, opens_arr, highs, lows, volumes,
                         index_closes=None, sector_closes=None, tg_score: float = None):
    """计算 29 维波浪周期特征 + 红量特征 + 大盘/板块环境 + ★TG反哺特征.

    Args:
        index_closes: 同期大盘指数日收盘价 (可选, 用于算大盘偏离)
        sector_closes: 同期板块指数日收盘价 (可选, 用于算板块偏离)
        tg_score: ★ V3: TG管线 composite_score (0~100), 作为第48维反哺特征
    """
    n = len(closes)
    entry_c = closes[-1]
    if n < 60: return None

    # ── 锁死周期检测 ──
    lock_segments = []; i = 0
    while i <= n - 20:
        w_c = closes[i:i+20]; wm = float(np.median(w_c))
        if wm <= 0: wm = 1.0
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
                avg_v = float(np.mean(volumes[start:end+1]))
                l_std = float(np.std(closes[start:end+1])/float(np.median(closes[start:end+1]))*100)
                lock_segments.append((start, end, l_std, avg_v))
        else: i += 1

    n_cycles = len(lock_segments)
    if n_cycles == 0:
        tg_val = float(min(100, max(0, tg_score or 0.0))) / 100.0 if tg_score is not None else 0.0
        return [0.0]*26 + [0.0]*3 + [0.0]*2 + [0.0]*6 + [tg_val]  # 38维: wave+egg+env+vet+tg

    cycle_lengths = [e-s for s,e,_,_ in lock_segments]
    cycle_vols = [v for _,_,_,v in lock_segments]

    current_in_lock = False; current_cycle_idx = -1
    current_lock_days = 0; current_lock_vol = 0.0; current_lock_std = 10.0
    breakout_magnitudes = []
    for idx, (s, e, std, vol) in enumerate(lock_segments):
        if e >= n - 5:
            current_in_lock = True; current_cycle_idx = idx
            current_lock_days = e - s; current_lock_vol = vol; current_lock_std = std
        if idx > 0 and s > lock_segments[idx-1][1]:
            pe = lock_segments[idx-1][1]
            seg_hi = float(np.max(highs[pe:s+1])); seg_lo = float(np.min(lows[pe:s+1]))
            if seg_lo > 0: breakout_magnitudes.append((seg_hi-seg_lo)/seg_lo*100)

    waves_completed = len(breakout_magnitudes)
    acceleration = 0.0
    if len(cycle_lengths) >= 2:
        last = cycle_lengths[-1]; prev = cycle_lengths[-2]
        acceleration = (prev-last)/max(prev,1)*100

    # ── 方向校验: 锁死段的高点在抬升还是在下降 ──
    # 取每段锁死的中点价, 判断趋势
    lock_midpoints = [float(np.mean(closes[s:e+1])) for s,e,_,_ in lock_segments]
    lock_highs_peak = [float(np.max(highs[s:e+1])) for s,e,_,_ in lock_segments]
    # 总体斜率: 正=上行通道, 负=下降通道
    if len(lock_midpoints) >= 3:
        trend_slope = float(np.polyfit(range(len(lock_midpoints)), lock_midpoints, 1)[0])
        # 最后两段 vs 前两段: 趋势在加速还是反转
        recent_mids = lock_midpoints[-2:] if len(lock_midpoints) >= 2 else lock_midpoints
        early_mids = lock_midpoints[:2] if len(lock_midpoints) >= 2 else lock_midpoints
        up_trend = float(np.mean(lock_highs_peak[-2:]) > np.mean(lock_highs_peak[:2]))
    elif len(lock_midpoints) >= 2:
        trend_slope = (lock_midpoints[-1] - lock_midpoints[0]) / max(1, lock_segments[-1][0] - lock_segments[0][0])
        up_trend = 1.0 if lock_midpoints[-1] > lock_midpoints[0] else 0.0
    else:
        trend_slope = 0.0; up_trend = 0.5
    # 如果在下降通道 → 浪数是虚假的, 强制清零高权重特征
    direction_penalty = 1.0 if up_trend > 0 else 0.0

    vol_trend = 0.0
    if len(cycle_vols) >= 2:
        rv=cycle_vols[-min(3,len(cycle_vols)):]; ev=cycle_vols[:min(3,len(cycle_vols))]
        if np.mean(ev) > 0: vol_trend = (np.mean(rv)-np.mean(ev))/np.mean(ev)*100

    bk_trend = 0.0
    if len(breakout_magnitudes) >= 2:
        rb=breakout_magnitudes[-min(3,len(breakout_magnitudes)):]; eb=breakout_magnitudes[:min(3,len(breakout_magnitudes))]
        if np.mean(eb) > 0: bk_trend = (np.mean(rb)-np.mean(eb))/np.mean(eb)*100

    last_gap = lock_segments[-1][0] - lock_segments[-2][1] if len(lock_segments) >= 2 else 0

    # ── 红量+MAVOL ──
    r60c = closes[-60:] if n >= 60 else closes; r60o = opens_arr[-60:] if n >= 60 else opens_arr
    r60v = volumes[-60:] if n >= 60 else volumes
    red60 = r60c > r60o; mavol = float(np.mean(r60v)); red_vol60 = red60 & (r60v > mavol)

    max_red = 0; cur = 0
    for v in red60:
        if v: cur += 1; max_red = max(max_red, cur)
        else: cur = 0
    rcnt = sum(1 for v in reversed(red60) if v)
    rr10 = float(np.mean(red60[-10:])) if len(red60) >= 10 else float(np.mean(red60))

    max_rv = 0; cur = 0
    for v in red_vol60:
        if v: cur += 1; max_rv = max(max_rv, cur)
        else: cur = 0
    rrv = sum(1 for v in reversed(red_vol60) if v)

    h60 = float(np.max(highs[-60:])) if n >= 60 else float(np.max(highs))
    l60 = float(np.min(lows[-60:])) if n >= 60 else float(np.min(lows))
    pos = (entry_c-l60)/(h60-l60) if h60 > l60 else 0.5
    ret5 = (closes[-1]/closes[-5]-1)*100 if n >= 5 else 0
    days_since_lock = max(0, n - lock_segments[-1][1] - 1)

    # ── 蛋/鹅判别: 第一轮锁定均价 vs 当前价格 ──
    # 蛋 = 累计涨幅 < 50%, 小鸡 = 50-100%, 大雁 = >100%
    first_lock_avg = 0.0
    egg_type = "unknown"  # egg / chick / goose
    gain_from_first_lock = 0.0

    if lock_segments:
        s0, e0, _, _ = lock_segments[0]
        first_lock_avg = float(np.mean(closes[s0:e0+1]))
        if first_lock_avg > 0:
            gain_from_first_lock = (entry_c - first_lock_avg) / first_lock_avg * 100
            if gain_from_first_lock < 50:
                egg_type = "egg"
            elif gain_from_first_lock <= 100:
                egg_type = "chick"
            else:
                egg_type = "goose"

    # 蛋奖励: egg=1.0, chick=0.5, goose=-1.0 (大雁不进池,仅留作训练样本)
    egg_bonus_map = {"egg": 1.0, "chick": 0.5, "goose": -1.0, "unknown": 0.0}
    egg_bonus = egg_bonus_map.get(egg_type, 0.0)

    # 峰值衰减: 最后两浪的爆发力在递减?
    peak_deceleration = 0.0
    if len(breakout_magnitudes) >= 2:
        last2 = breakout_magnitudes[-2:]; first2 = breakout_magnitudes[:2]
        if np.mean(first2) > 0:
            peak_deceleration = (np.mean(last2) - np.mean(first2)) / np.mean(first2) * 100

    # ★ V2 老兵增强特征 ──────────────────────
    # 1. 锁死进度比: 当前天数 / 历史均值
    vet_progress = 0.0
    if n_cycles >= 2 and cycle_lengths:
        avg_len = float(np.mean(cycle_lengths))
        vet_progress = current_lock_days / max(avg_len, 1)

    # 2. 振幅收敛度: 早期振幅 - 近期振幅 (正=收敛)
    amp_convergence = 0.0
    if current_lock_days > 10 and lock_segments:
        seg_s, seg_e = lock_segments[-1][0], lock_segments[-1][1]
        mid = seg_s + max(1, (seg_e - seg_s) // 2)
        if mid > seg_s + 5 and seg_e - mid > 5:
            early_a = (float(np.max(highs[seg_s:mid])) - float(np.min(lows[seg_s:mid]))) / max(float(np.min(lows[seg_s:mid])), 0.01) * 100
            late_a = (float(np.max(highs[mid:seg_e+1])) - float(np.min(lows[mid:seg_e+1]))) / max(float(np.min(lows[mid:seg_e+1])), 0.01) * 100
            amp_convergence = max(0, early_a - late_a)

    # 3. 量能萎缩率: 前半量 - 后半量
    vol_shrinkage = 0.0
    if current_lock_days > 10 and lock_segments:
        seg_s, seg_e = lock_segments[-1][0], lock_segments[-1][1]
        mid2 = seg_s + max(1, (seg_e - seg_s) // 2)
        if mid2 > seg_s + 5:
            ev = float(np.mean(volumes[seg_s:mid2]))
            lv = float(np.mean(volumes[mid2:seg_e+1]))
            vol_shrinkage = (ev - lv) / max(ev, 1)

    # 4-5. 历史浪均幅/中位幅
    avg_wave_hist = 0.0; med_wave_hist = 0.0
    wave_pcts_from_hist = []
    for j in range(len(lock_segments)-1):
        seg = lock_segments[j]
        if seg[1] + 5 < n:
            post_h = float(np.max(highs[seg[1]+1:min(seg[1]+40, n)]))
            lk_avg = float(np.mean(closes[seg[0]:seg[1]+1]))
            if lk_avg > 0:
                wave_pcts_from_hist.append((post_h - lk_avg) / lk_avg * 100)
    if wave_pcts_from_hist:
        avg_wave_hist = float(np.mean(wave_pcts_from_hist))
        med_wave_hist = float(np.median(wave_pcts_from_hist))

    # 6. 当前振幅
    cur_amplitude = (float(np.max(highs[-30:])) - float(np.min(lows[-30:]))) / max(float(np.min(lows[-30:])), 0.01) * 100 if n >= 30 else 0

    return [
        float(n_cycles), float(current_cycle_idx+1), float(current_lock_days), float(current_lock_std),
        float(current_lock_vol), float(acceleration), float(vol_trend), float(bk_trend),
        float(waves_completed) * direction_penalty * max(0, egg_bonus), float(last_gap),
        1.0 if acceleration > 15 else 0.0, 1.0 if vol_trend < -20 else 0.0, 1.0 if bk_trend > 20 else 0.0,
        float(pos), float(ret5), 1.0 if ret5 > 3 else 0.0,
        float(max_red), float(rcnt), float(rr10), float(max_rv), float(rrv),
        1.0 if current_in_lock else 0.0, 1.0 if current_cycle_idx >= 2 and up_trend else 0.0,
        1.0 if current_cycle_idx >= 1 and acceleration > 10 and up_trend else 0.0,
        float(days_since_lock), float(n_cycles) * direction_penalty,
        # 蛋/鹅 (3维)
        float(first_lock_avg), float(gain_from_first_lock), float(egg_bonus),
        # 大盘/板块环境 (2维)
        _lock_market_return(closes, index_closes, lock_segments),
        _lock_sector_return(closes, sector_closes, lock_segments),
        # ★ V2 老兵增强 (6维)
        float(vet_progress), float(amp_convergence), float(vol_shrinkage),
        float(avg_wave_hist), float(med_wave_hist), float(cur_amplitude),
        # ★ V3: TG 反哺特征 (标准化到 ~0-1 范围)
        float(min(100, max(0, tg_score or 0.0))) / 100.0 if tg_score is not None else 0.0,
    ]
