"""AlphaFlow XGBoost 波浪周期训练器 V3.

200天回看 → 识别多轮锁死→爆发周期 → 按周期数+加速比+红量突破排序.

26维特征:
  F1-F4:   周期计数 (几个锁死周期 / 当前第几轮 / 锁死天数 / 强度)
  F5-F10:  跨周期对比 (加速比 / 量趋势 / 爆发趋势 / 完成浪数 / 间隔)
  F11-F13: 阈值标志 (加速锁死 / 缩量吸筹 / 爆发递增)
  F14-F16: 启动前兆 (区间位置 / 5日涨幅 / 明显上涨)
  F17-F21: 红量特征 (连续红量 / 最近红量 / 红量比10d / 红放最长 / 红放最近)
  F22-F26: 状态标志 (是否锁死 / 第3轮+ / 第2轮+加速 / 距上轮天数 / 锁周期总数)
"""
import asyncio, sys, json, os, logging, numpy as np, time
from collections import defaultdict
from datetime import date, timedelta
from sqlalchemy import text
sys.path.insert(0, '.')

import xgboost as xgb
from sklearn.metrics import roc_auc_score, accuracy_score
from sklearn.model_selection import train_test_split
from app.core.database import async_session_factory

logger = logging.getLogger("alphaflow.train")
LOOKBACK = 200
TRAIN_SAMPLES = 5000

LOCK_RANGE = 0.15
LOCK_STD_MAX = 8.0
LOCK_MIN_DAYS = 10

FEAT_NAMES = [
    "锁周期数", "当前第几轮", "锁死天数", "锁死强度",
    "锁死均量", "周期加速比", "量趋势", "爆发趋势",
    "已完成浪数", "浪间隔", "加速锁死", "缩量吸筹",
    "爆发递增", "区间位置", "5日涨幅", "明显上涨",
    "红量连续", "最近红量", "红量比10d", "红放最长",
    "红放最近", "是否锁死", "第3轮+", "第2轮加速", "距上轮天数", "锁周期总数",
    # SXQS 资金博弈 (8维)
    "H1_H2方向", "距H3基差%", "买力VAR6", "卖力VAR7", "净买力",
    "力波动VAR8", "A买入信号", "近低点超卖", "ZIG_D买", "ZIG_W卖",
    "第一轮均价", "第一轮涨幅%", "蛋奖励",
    "大盘锁死期%", "板块锁死期%",
]


def _compute_zig_inline(highs, lows, closes, n):
    """ZIG(3,10)拐点 — 训练脚本内联版."""
    REV = 0.10; zig = np.zeros(n); zig[0] = closes[0]
    direction = 1; last_ext = closes[0]
    for i in range(1, n):
        if direction == 1:
            if highs[i] > last_ext: last_ext = highs[i]
            zig[i] = last_ext
            if closes[i] < last_ext * (1 - REV): direction = -1; last_ext = lows[i]
        else:
            if lows[i] < last_ext: last_ext = lows[i]
            zig[i] = last_ext
            if closes[i] > last_ext * (1 + REV): direction = 1; last_ext = highs[i]
    zma = np.convolve(zig, np.ones(2)/2, mode='same'); zma[0] = zig[0]
    d, w = 0.0, 0.0
    for off in [0, 1, 2]:
        idx = n - 1 - off; prev = idx - 1
        if prev < 0: continue
        if zig[prev] <= zma[prev] and zig[idx] > zma[idx]: d = 1.0; break
        if zma[prev] <= zig[prev] and zma[idx] > zig[idx]: w = 1.0; break
    return d, w, "up" if direction == 1 else "down"


async def load_training_data(n_rally=5000, n_normal=5000):
    logger.info(f"Loading {n_rally} rally + {n_normal} normal...")

    async with async_session_factory() as s:
        r = await s.execute(text("SELECT ts_code, sample_date, forward_peak_pct FROM trend_samples WHERE label = 'major_rally' AND sample_date >= '2022-01-01' ORDER BY RANDOM() LIMIT :n"), {"n": n_rally})
        rally_samples = [(row[0], row[1], float(row[2] or 0)) for row in r.fetchall()]
        r2 = await s.execute(text("SELECT ts_code, sample_date, forward_peak_pct FROM trend_samples WHERE label = 'normal' AND sample_date >= '2022-01-01' ORDER BY RANDOM() LIMIT :n"), {"n": n_normal})
        normal_samples = [(row[0], row[1], float(row[2] or 0)) for row in r2.fetchall()]

    all_ts = list(set(r[0] for r in rally_samples + normal_samples))
    min_d = min(r[1] for r in rally_samples + normal_samples) - timedelta(days=220)
    max_d = max(r[1] for r in rally_samples + normal_samples)

    klines = defaultdict(list)
    async with async_session_factory() as s:
        for i in range(0, len(all_ts), 300):
            batch = all_ts[i:i+300]
            r = await s.execute(text("""SELECT ts_code, trade_date, open, close, volume, amount, high, low FROM daily_kline WHERE ts_code = ANY(:syms) AND trade_date BETWEEN :d1 AND :d2 ORDER BY ts_code, trade_date"""), {"syms": batch, "d1": min_d, "d2": max_d})
            for row in r.fetchall():
                try:
                    c_val = float(row[3] or 0)
                    if c_val <= 0: continue
                    klines[row[0]].append({"d": row[1], "o": float(row[2] or 0), "c": c_val, "v": float(row[4] or 0), "a": float(row[5] or 0), "h": float(row[6] or c_val), "l": float(row[7] or c_val)})
                except (TypeError, ValueError): continue
    logger.info(f"K-lines: {len(klines)} symbols")

    X, y_labels, skipped = [], [], 0
    for ts_code, sample_date, peak in rally_samples + normal_samples:
        bars = klines.get(ts_code, [])
        pre = [b for b in bars if b["d"] <= sample_date][-LOOKBACK:]
        if len(pre) < 80: skipped += 1; continue

        closes = np.array([b["c"] for b in pre])
        opens_arr = np.array([b["o"] for b in pre])
        volumes = np.array([b["v"] for b in pre])
        highs = np.array([b["h"] for b in pre])
        lows = np.array([b["l"] for b in pre])
        n = len(closes)
        entry_c = closes[-1]
        if entry_c <= 0: skipped += 1; continue

        # ── 锁死周期检测 ──
        lock_segments = []
        i = 0
        while i <= n - 20:
            window_c = closes[i:i+20]
            wm = float(np.median(window_c))
            in_r = np.all((window_c >= wm*(1-LOCK_RANGE)) & (window_c <= wm*(1+LOCK_RANGE)))
            s2 = float(np.std(window_c)/wm*100)
            if in_r and s2 < LOCK_STD_MAX:
                start = i
                while i < n - 1:
                    i += 1
                    if i + 20 > n: break
                    w2c = closes[i:i+20]; w2m = float(np.median(w2c))
                    if not np.all((w2c >= w2m*(1-LOCK_RANGE)) & (w2c <= w2m*(1+LOCK_RANGE))): break
                    if float(np.std(w2c)/w2m*100) > LOCK_STD_MAX: break
                end = min(i+19, n-1)
                if end - start >= LOCK_MIN_DAYS:
                    avg_v = float(np.mean(volumes[start:end+1]))
                    l_std = float(np.std(closes[start:end+1])/float(np.median(closes[start:end+1]))*100)
                    lock_segments.append((start, end, l_std, avg_v))
            else:
                i += 1

        n_cycles = len(lock_segments)
        cycle_lengths = [e-s for s, e, _, _ in lock_segments]
        cycle_vols = [v for _, _, _, v in lock_segments]

        current_in_lock = False
        current_cycle_idx = -1
        current_lock_days = 0; current_lock_vol = 0.0; current_lock_std = 10.0

        breakout_magnitudes = []
        for idx, (s, e, std, vol) in enumerate(lock_segments):
            if e >= n - 5:
                current_in_lock = True
                current_cycle_idx = idx
                current_lock_days = e - s
                current_lock_vol = vol
                current_lock_std = std
            if idx > 0:
                pe = lock_segments[idx-1][1]
                if s > pe:  # 确保有间隔
                    seg_hi = float(np.max(highs[pe:s+1])); seg_lo = float(np.min(lows[pe:s+1]))
                    if seg_lo > 0:
                        breakout_magnitudes.append((seg_hi-seg_lo)/seg_lo*100)

        waves_completed = len(breakout_magnitudes)
        # ── 方向校验 ──
        lock_midpoints = [float(np.mean(closes[s:e+1])) for s,e,_,_ in lock_segments]
        lock_highs_peak = [float(np.max(highs[s:e+1])) for s,e,_,_ in lock_segments]
        if len(lock_midpoints) >= 3:
            up_trend = float(np.mean(lock_highs_peak[-2:]) > np.mean(lock_highs_peak[:2]))
        elif len(lock_midpoints) >= 2:
            up_trend = 1.0 if lock_midpoints[-1] > lock_midpoints[0] else 0.0
        else:
            up_trend = 0.5
        direction_penalty = 1.0 if up_trend > 0 else 0.0

        acceleration = 0.0
        if len(cycle_lengths) >= 2:
            last = cycle_lengths[-1]; prev = cycle_lengths[-2]
            acceleration = (prev-last)/max(prev,1)*100

        # 蛋/鹅: 第一轮锁定均价 vs 当前价
        first_lock_avg = 0.0; gain_from_first_lock = 0.0; egg_bonus = 0.0
        if lock_segments:
            s0, e0, _, _ = lock_segments[0]
            first_lock_avg = float(np.mean(closes[s0:e0+1]))
            if first_lock_avg > 0:
                gain_from_first_lock = (closes[-1] - first_lock_avg) / first_lock_avg * 100
                if gain_from_first_lock < 50: egg_bonus = 1.0
                elif gain_from_first_lock <= 100: egg_bonus = 0.5
                else: egg_bonus = -1.0

        vol_trend_across = 0.0
        if len(cycle_vols) >= 2:
            rv2 = cycle_vols[-min(3,len(cycle_vols)):]; ev2 = cycle_vols[:min(3,len(cycle_vols))]
            if np.mean(ev2) > 0: vol_trend_across = (np.mean(rv2)-np.mean(ev2))/np.mean(ev2)*100

        breakout_trend = 0.0
        if len(breakout_magnitudes) >= 2:
            rb = breakout_magnitudes[-min(3,len(breakout_magnitudes)):]; eb = breakout_magnitudes[:min(3,len(breakout_magnitudes))]
            if np.mean(eb) > 0: breakout_trend = (np.mean(rb)-np.mean(eb))/np.mean(eb)*100

        last_gap = lock_segments[-1][0] - lock_segments[-2][1] if len(lock_segments) >= 2 else 0

        # ── 红量+MAVOL ──
        r60c = closes[-60:] if n >= 60 else closes; r60o = opens_arr[-60:] if n >= 60 else opens_arr
        r60v = volumes[-60:] if n >= 60 else volumes
        red60 = r60c > r60o
        mavol = float(np.mean(r60v))
        red_vol60 = red60 & (r60v > mavol)

        max_red = 0; cur = 0
        for v in red60:
            if v: cur += 1; max_red = max(max_red, cur)
            else: cur = 0
        rcnt = 0
        for v in reversed(red60):
            if v: rcnt += 1
            else: break
        rr10 = float(np.mean(red60[-10:])) if len(red60) >= 10 else float(np.mean(red60))

        max_rv = 0; cur = 0
        for v in red_vol60:
            if v: cur += 1; max_rv = max(max_rv, cur)
            else: cur = 0
        rrv = 0
        for v in reversed(red_vol60):
            if v: rrv += 1
            else: break

        h60 = float(np.max(highs[-60:])) if n >= 60 else float(np.max(highs))
        l60 = float(np.min(lows[-60:])) if n >= 60 else float(np.min(lows))
        pos = (entry_c-l60)/(h60-l60) if h60 > l60 else 0.5
        ret5 = (closes[-1]/closes[-5]-1)*100 if n >= 5 else 0
        days_since_lock = 0
        if lock_segments:
            last_lock_end = lock_segments[-1][1]
            days_since_lock = max(0, n - last_lock_end - 1)

        # ── SXQS 资金博弈特征 ──
        # H1=EMA(C,6), H2=EMA(H1,18), H3=EMA(C,108)
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

        # VAR6/VAR7: 25日买卖力%
        tr = np.maximum(highs-lows, np.abs(highs-np.roll(closes,1)))
        tr[0] = highs[0]-lows[0]
        var1_arr = np.array([np.sum(tr[max(0,i-24):i+1]) for i in range(n)])
        v2 = highs - np.roll(highs, 1); v2[0] = 0
        v3 = np.roll(lows, 1) - lows; v3[0] = 0
        buy_mask = (v2 > 0) & (v2 > v3); sell_mask = (v3 > 0) & (v3 > v2)
        var6_arr = np.zeros(n); var7_arr = np.zeros(n)
        for i in range(n):
            s, e = max(0, i-24), i+1
            var6_arr[i] = np.sum(np.where(buy_mask[s:e], v2[s:e], 0)) * 100 / max(var1_arr[i], 0.01)
            var7_arr[i] = np.sum(np.where(sell_mask[s:e], v3[s:e], 0)) * 100 / max(var1_arr[i], 0.01)

        # VAR8 = 15-MA of power divergence
        var8_arr = np.zeros(n)
        for i in range(14, n):
            div = np.abs(var7_arr[i-14:i+1]-var6_arr[i-14:i+1])/(var7_arr[i-14:i+1]+var6_arr[i-14:i+1])*100
            var8_arr[i] = np.mean(div)

        # A信号 = 买力>卖力 + 买力>25 + 卖力<25
        a_signal = (var7_arr[-1] > var6_arr[-1]) & (var7_arr[-1] > 25) & (var6_arr[-1] < 25)
        # 近20日低点超卖近似
        near_low = closes[-1] <= np.min(lows[-20:])*1.03 if n >= 20 else False

        h1_h2_up = 1.0 if h1_arr[-1] > h2_arr[-1] else 0.0
        h3_dev = (closes[-1]-h3_arr[-1])/h3_arr[-1]*100 if h3_arr[-1] > 0 else 0.0
        net_power = var6_arr[-1] - var7_arr[-1]

        # ZIG拐点 (训练特征)
        d_sig, w_sig, _ = _compute_zig_inline(highs, lows, closes, n)

        feats = [
            float(n_cycles), float(current_cycle_idx+1), float(current_lock_days), float(current_lock_std),
            float(current_lock_vol), float(acceleration), float(vol_trend_across), float(breakout_trend),
            float(waves_completed) * direction_penalty, float(last_gap),
            1.0 if acceleration > 15 else 0.0, 1.0 if vol_trend_across < -20 else 0.0, 1.0 if breakout_trend > 20 else 0.0,
            float(pos), float(ret5), 1.0 if ret5 > 3 else 0.0,
            float(max_red), float(rcnt), float(rr10), float(max_rv), float(rrv),
            1.0 if current_in_lock else 0.0, 1.0 if current_cycle_idx >= 2 and up_trend else 0.0,
            1.0 if current_cycle_idx >= 1 and acceleration > 10 and up_trend else 0.0,
            float(days_since_lock), float(n_cycles) * direction_penalty,
            # 蛋优先 (3维) — 第一轮均价/涨幅/蛋奖励
            float(first_lock_avg) if lock_segments else 0.0,
            float(gain_from_first_lock) if lock_segments and first_lock_avg > 0 else 0.0,
            float(egg_bonus),
            # SXQS (8维)
            float(h1_h2_up), float(h3_dev), float(var6_arr[-1]), float(var7_arr[-1]), float(net_power),
            float(var8_arr[-1]), 1.0 if a_signal else 0.0, 1.0 if near_low else 0.0,
            float(d_sig), float(w_sig),
            # 大盘/板块环境 (训练数据暂无, 留0占位)
            0.0, 0.0,
        ]
        X.append(feats)
        y_labels.append(1.0 if peak >= 30 and n_cycles >= 1 else 0.0)

    X = np.array(X, dtype=np.float32); X = sanitize_array(X, fill=0.0)
    y_labels = np.array(y_labels, dtype=np.float32)
    logger.info(f"Feature matrix: {X.shape}, skipped {skipped}, pos_rate={y_labels.mean():.2%}")
    return X, y_labels


async def train():
    X, y = await load_training_data(n_rally=TRAIN_SAMPLES, n_normal=TRAIN_SAMPLES)
    pw = (1-y.mean())/max(y.mean(), 0.01)
    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    logger.info(f"Train: {len(X_tr)}, Test: {len(X_te)}, pos_rate={y_tr.mean():.2%}")

    model = xgb.XGBClassifier(n_estimators=200, max_depth=5, learning_rate=0.03, subsample=0.7, colsample_bytree=0.7, random_state=42, reg_alpha=1.0, reg_lambda=2.0, min_child_weight=5, scale_pos_weight=pw, eval_metric='auc')
    t0 = time.time()
    model.fit(X_tr, y_tr, eval_set=[(X_te, y_te)], verbose=20)
    logger.info(f"Training done in {time.time()-t0:.0f}s")

    y_prob = model.predict_proba(X_te)[:, 1]
    auc = roc_auc_score(y_te, y_prob)
    acc = accuracy_score(y_te, y_prob >= 0.5)

    importances = sorted(zip(FEAT_NAMES, model.feature_importances_), key=lambda x: -x[1])
    top5 = [(n, f'{v:.1%}') for n, v in importances[:5]]
    logger.info(f"Test AUC={auc:.4f} Acc={acc:.4f}")
    logger.info(f"Top-5: {top5}")

    top_k = {}
    nt = len(X_te)
    for kp in [5, 10, 20]:
        k = max(1, int(nt*kp/100))
        hi = y_te[np.argsort(-y_prob)[:k]].mean()
        top_k[f'top{kp}%'] = round(float(hi), 3)
    logger.info(f"Top-K: {top_k}")

    os.makedirs('models', exist_ok=True)
    model.save_model('models/alphaflow_xgb.json')
    with open('models/alphaflow_xgb_meta.json', 'w') as f:
        json.dump({'lookback': LOOKBACK, 'features': len(FEAT_NAMES), 'test_auc': round(auc,4), 'test_acc': round(acc,4), 'top_k': {k:round(float(v),4) for k,v in top_k.items()}, 'top_features': [(str(n), round(float(v),4)) for n,v in importances[:10]]}, f, ensure_ascii=False, indent=2)

    return {'auc': round(auc, 4), 'acc': round(acc, 4), 'top_k': top_k, 'top_features': top5}


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    r = await train()
    print(f"\n=== AlphaFlow Wave V3 ===")
    print(f"AUC: {r['auc']}  Acc: {r['acc']}  Top-K: {r['top_k']}")
    print(f"Top-5: {r['top_features']}")

if __name__ == "__main__":
    asyncio.run(main())
