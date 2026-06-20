"""AlphaFlow 蛋期分时训练 — 批量下载5分钟线 + 特征提取 + 训练.

三步:
  A: 从379只大雁中找蛋期(第一轮锁死后涨幅0→50%的窗口)
  B: 批量下载5分钟线(每只取蛋期中段20天)
  C: 提取8维分时特征 → 存入 mins_train_samples
"""
import asyncio, sys, numpy as np, os, time, logging, json
from collections import defaultdict
from datetime import date, timedelta
import httpx
from dotenv import load_dotenv
from sqlalchemy import text
sys.path.insert(0, 'C:/AI-Agent-Local/Stock/backend')

load_dotenv('C:/AI-Agent-Local/Stock/backend/.env')
TOKEN = os.getenv('TUSHARE_TOKEN')
SEM = asyncio.Semaphore(3)
logger = logging.getLogger("alphaflow.mins_train")

from app.core.database import async_session_factory
from app.services.alphaflow_features import compute_wave_features


async def step_a_find_egg_phases():
    """从所有大雁中找到蛋期窗口 (涨幅0→50%)."""
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT ts_code, gain_pct FROM goose_archive ORDER BY gain_pct"))
        geese = [(row[0], float(row[1] or 0)) for row in r.fetchall()]

    logger.info(f"Step A: Analyzing {len(geese)} geese for egg phases...")
    egg_phases = []

    for idx, (code, total_gain) in enumerate(geese):
        try:
            async with async_session_factory() as s:
                r = await s.execute(text(
                    "SELECT trade_date, close, open, high, low, volume "
                    "FROM daily_kline WHERE ts_code = :c ORDER BY trade_date"
                ), {"c": code})
                rows = list(r.fetchall())

            if len(rows) < 200:
                continue

            cs = np.array([float(rw[1] or 0) for rw in rows])
            os_z = np.array([float(rw[2] or cs[i]) for i, rw in enumerate(rows)])
            vs = np.array([float(rw[4] or 0) for rw in rows])
            hs = np.array([float(rw[3] or cs[i]) for i, rw in enumerate(rows)])
            ls = np.array([float(rw[5] or cs[i]) for i, rw in enumerate(rows)])
            wf = compute_wave_features(cs, os_z, hs, ls, vs)

            if wf is None:
                continue

            lock_avg = wf[26]  # 第一轮锁死均价
            if lock_avg <= 1:
                continue

            # 找到蛋期: 从第一次突破锁均价到涨幅达50%
            egg_start = None
            egg_end = None
            for i, rw in enumerate(rows):
                c_val = float(rw[1] or 0)
                pct = (c_val - lock_avg) / lock_avg * 100
                if egg_start is None and pct >= 0:
                    egg_start = rw[0]
                if egg_start and pct >= 50:
                    egg_end = rw[0]
                    break

            if egg_start and egg_end:
                egg_days = (egg_end - egg_start).days
                if 30 <= egg_days <= 500:  # 合理的蛋期长度
                    egg_mid = egg_start + timedelta(days=egg_days // 2)
                    egg_phases.append({
                        "ts_code": code,
                        "lock_avg": round(lock_avg, 2),
                        "egg_start": str(egg_start),
                        "egg_end": str(egg_end),
                        "egg_days": egg_days,
                        "egg_mid": str(egg_mid),
                        "total_gain": total_gain,
                    })
                    if len(egg_phases) <= 10:
                        logger.info(f"  {code}: egg {egg_start}→{egg_end} ({egg_days}d) lock={lock_avg:.1f} total={total_gain:.0f}%")
        except Exception:
            pass

        if (idx + 1) % 100 == 0:
            logger.info(f"  Analyzed {idx+1}/{len(geese)}, found {len(egg_phases)} egg phases")

    logger.info(f"Step A done: {len(egg_phases)} geese with valid egg phases")

    # Save egg phases
    async with async_session_factory() as s:
        await s.execute(text("""
            CREATE TABLE IF NOT EXISTS egg_phase_samples (
                ts_code VARCHAR(20) PRIMARY KEY, lock_avg DECIMAL(8,2),
                egg_start DATE, egg_end DATE, egg_days INT, egg_mid DATE,
                total_gain DECIMAL(8,2), created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        for ep in egg_phases:
            await s.execute(text(
                "INSERT INTO egg_phase_samples VALUES (:c,:l,:s,:e,:d,:m,:g,NOW()) "
                "ON CONFLICT (ts_code) DO NOTHING"
            ), {"c": ep["ts_code"], "l": ep["lock_avg"],
                "s": date.fromisoformat(ep["egg_start"]),
                "e": date.fromisoformat(ep["egg_end"]),
                "d": ep["egg_days"],
                "m": date.fromisoformat(ep["egg_mid"]),
                "g": ep["total_gain"]})
        await s.commit()

    return egg_phases


async def download_5min_batch(ts_code, start_dt, end_dt):
    """下载一个蛋期中段的5分钟线."""
    async with SEM:
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post('https://api.tushare.pro', json={
                    'api_name': 'stk_mins', 'token': TOKEN,
                    'params': {
                        'ts_code': ts_code, 'freq': '5min',
                        'start_date': f'{start_dt} 09:00:00',
                        'end_date': f'{end_dt} 15:00:00',
                    },
                    'fields': 'ts_code,trade_time,open,close,high,low,vol,amount'
                })
                data = resp.json()
                if data.get('code') != 0:
                    return []
                return data.get('data', {}).get('items', []) or []
        except Exception as e:
            return []


def extract_features(bars, n_days):
    """提取8维分时特征."""
    if len(bars) < 100:
        return None

    closes = np.array([float(b[3]) for b in bars])
    opens = np.array([float(b[2]) for b in bars])
    highs = np.array([float(b[4]) for b in bars])
    lows = np.array([float(b[5]) for b in bars])
    vols = np.array([float(b[6]) for b in bars])
    n = len(bars)
    total_days = max(n_days, 1)

    def by_day(key_fn, values):
        groups = defaultdict(list)
        for i, b in enumerate(bars):
            groups[key_fn(b)].append(values[i])
        return groups

    features = []

    # 1. V反频率: 每天次数
    v_count = 0
    for i in range(5, n - 6):
        drop = (lows[i] - closes[i - 5]) / closes[i - 5] * 100
        if drop < -1.5:
            for j in range(i + 1, min(i + 7, n)):
                recovery = (highs[j] - lows[i]) / lows[i] * 100
                if recovery > 1.0:
                    v_count += 1
                    break
    features.append(round(v_count / total_days, 2))

    # 2. 尾盘量比
    day_vols = defaultdict(lambda: {'tail': 0, 'total': 0})
    for b in bars:
        d = b[1][:10]
        day_vols[d]['total'] += float(b[6])
        if b[1] >= f'{d} 14:30':
            day_vols[d]['tail'] += float(b[6])
    tail_ratios = [v['tail'] / max(v['total'], 1) for v in day_vols.values() if v['total'] > 0]
    features.append(round(np.mean(tail_ratios), 3) if tail_ratios else 0.25)

    # 3. 量能集中度
    concs = []
    for d, v in day_vols.items():
        if v['total'] > 0:
            day_v = [float(b[6]) for b in bars if b[1][:10] == d]
            concs.append(max(day_v) / max(np.mean(day_v), 1))
    features.append(round(np.mean(concs), 2) if concs else 3.0)

    # 4. 开盘冲高率
    spike_d = 0
    for d in set(b[1][:10] for b in bars):
        db = sorted([b for b in bars if b[1][:10] == d], key=lambda x: x[1])
        if len(db) < 20: continue
        do = float(db[0][2]); dc = float(db[-1][3])
        fh = max(float(b[4]) for b in db[:6])
        if (fh - do) / do * 100 > 2 and (dc - do) / do * 100 < 1:
            spike_d += 1
    features.append(round(spike_d / total_days, 3))

    # 5. 日内振幅
    amps = []
    for d in set(b[1][:10] for b in bars):
        dh = [float(b[4]) for b in bars if b[1][:10] == d]
        dl = [float(b[5]) for b in bars if b[1][:10] == d]
        if dh and dl: amps.append((max(dh) - min(dl)) / max(min(dl), 0.01) * 100)
    features.append(round(np.mean(amps), 1) if amps else 0)

    # 6. 脉冲方向
    avg_v = np.mean(vols)
    spike_up = sum(1 for i in range(1, n) if vols[i] > avg_v * 3 and closes[i] > opens[i])
    spike_tot = sum(1 for i in range(1, n) if vols[i] > avg_v * 3)
    features.append(round(spike_up / max(spike_tot, 1), 2))

    # 7. 连续红量
    reds = closes > opens
    max_red = 0; cur = 0
    for v in reds:
        if v: cur += 1; max_red = max(max_red, cur)
        else: cur = 0
    features.append(max_red)

    # 8. 价格稳定性
    ma_l = np.convolve(closes, np.ones(20) / 20, mode='valid')
    features.append(round(float(np.mean(np.abs(closes[-len(ma_l):] - ma_l) / ma_l * 100)), 2) if len(ma_l) > 10 else 1.0)

    return features


async def step_bc_download_and_train(egg_phases, n_samples=100):
    """下载蛋期5分钟线并提取特征(只取前N只)."""
    logger.info(f"Step B: Downloading 5-min data for {min(n_samples, len(egg_phases))} geese...")

    # Create training table
    async with async_session_factory() as s:
        await s.execute(text("""
            CREATE TABLE IF NOT EXISTS mins_train_samples (
                id SERIAL PRIMARY KEY, ts_code VARCHAR(20) NOT NULL,
                feature_1 DECIMAL(8,4), feature_2 DECIMAL(8,4),
                feature_3 DECIMAL(8,4), feature_4 DECIMAL(8,4),
                feature_5 DECIMAL(8,4), feature_6 DECIMAL(8,4),
                feature_7 DECIMAL(8,4), feature_8 DECIMAL(8,4),
                egg_days INT, lock_avg DECIMAL(8,2), total_gain DECIMAL(8,2),
                label VARCHAR(20) DEFAULT 'pre_goose',
                created_at TIMESTAMPTZ DEFAULT NOW(), UNIQUE(ts_code)
            )
        """))
        await s.commit()

    # Download + extract
    features_saved = 0
    batch_size = 5  # Small batches to respect rate limits

    for i in range(0, min(n_samples, len(egg_phases)), batch_size):
        batch = egg_phases[i:i + batch_size]
        tasks = []

        for ep in batch:
            code = ep["ts_code"]
            egg_start = date.fromisoformat(ep["egg_start"])
            egg_end = date.fromisoformat(ep["egg_end"])
            egg_mid = date.fromisoformat(ep["egg_mid"])

            # Download 20-day window around egg mid point
            window_start = max(egg_start, egg_mid - timedelta(days=10))
            window_end = min(egg_end, egg_mid + timedelta(days=10))

            tasks.append((code, ep, window_start, window_end))

        for code, ep, ws, we in tasks:
            try:
                bars = await download_5min_batch(code, str(ws), str(we))
                if len(bars) < 50:
                    continue

                feats = extract_features(bars, ep["egg_days"])
                if feats is None:
                    continue

                async with async_session_factory() as s:
                    await s.execute(text("""
                        INSERT INTO mins_train_samples
                        (ts_code, feature_1, feature_2, feature_3, feature_4,
                         feature_5, feature_6, feature_7, feature_8,
                         egg_days, lock_avg, total_gain, label)
                        VALUES (:c, :f1, :f2, :f3, :f4, :f5, :f6, :f7, :f8,
                                :d, :l, :g, 'pre_goose')
                        ON CONFLICT (ts_code) DO NOTHING
                    """), {
                        "c": code,
                        "f1": feats[0], "f2": feats[1], "f3": feats[2], "f4": feats[3],
                        "f5": feats[4], "f6": feats[5], "f7": feats[6], "f8": feats[7],
                        "d": ep["egg_days"], "l": ep["lock_avg"], "g": ep["total_gain"],
                    })
                    await s.commit()
                features_saved += 1
            except Exception as e:
                pass

            await asyncio.sleep(0.3)  # Rate limit

        logger.info(f"  Batch {i // batch_size + 1}: {features_saved} saved so far")

    logger.info(f"Step B done: {features_saved} pre-goose samples saved")
    return features_saved


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    # Step A: Find egg phases
    egg_phases = await step_a_find_egg_phases()
    print(f"\n蛋期样本: {len(egg_phases)} 只")

    if egg_phases:
        # Step B: Download + extract (batch of 100)
        n = await step_bc_download_and_train(egg_phases, n_samples=100)
        print(f"分钟线特征已保存: {n} 只")

        # Quick stats
        async with async_session_factory() as s:
            r = await s.execute(text("SELECT COUNT(*) FROM mins_train_samples"))
            print(f"数据库总计: {r.scalar()} 条 pre-goose 样本")


if __name__ == "__main__":
    asyncio.run(main())
