"""AlphaFlow Step 2: 分钟线特征提取.

对每个正样本的启动前 60 天, 从 stk_mins 下载30分钟K线,
提取 5 个日内行为特征:

  1. V型反转频率 — (盘中跌>1%后拉回>1%)的次数/60天
  2. 尾盘异动率 — 14:30后的成交量占全天比 (avg over 60d)
  3. 日内振幅趋势 — 日振幅的60日斜率 (收敛/扩张)
  4. 量能集中度 — 最大30分钟量/全天量 (avg over 60d)
  5. 开盘冲高率 — 开盘30分钟涨>2%但收盘涨幅<1%的天数/60天
"""
import asyncio, sys, logging, time, json
from datetime import date, timedelta, datetime
from collections import defaultdict
import numpy as np
import httpx, os
from dotenv import load_dotenv
from sqlalchemy import text
from app.core.database import async_session_factory

load_dotenv('C:/AI-Agent-Local/Stock/backend/.env')
TUSHARE_TOKEN = os.getenv('TUSHARE_TOKEN')
TUSHARE_URL = "https://api.tushare.pro"
SEMAPHORE = asyncio.Semaphore(3)  # API限频

logger = logging.getLogger("alphaflow.mins")


async def _call_tushare_mins(ts_code: str, start_dt: str, end_dt: str) -> list[dict]:
    """调取 stk_mins 30分钟线."""
    async with SEMAPHORE:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(TUSHARE_URL, json={
                    "api_name": "stk_mins",
                    "token": TUSHARE_TOKEN,
                    "params": {
                        "ts_code": ts_code,
                        "freq": "30min",
                        "start_date": f"{start_dt} 09:00:00",
                        "end_date": f"{end_dt} 15:00:00",
                    },
                    "fields": "ts_code,trade_time,open,close,high,low,vol,amount"
                })
                data = resp.json()
                if data.get("code") != 0:
                    return []
                items = data.get("data", {}).get("items", []) or []
                return [{
                    "trade_time": item[1],
                    "open": float(item[2]), "close": float(item[3]),
                    "high": float(item[4]), "low": float(item[5]),
                    "vol": float(item[6]), "amount": float(item[7]),
                } for item in items]
        except Exception as e:
            logger.warning(f"stk_mins failed for {ts_code}: {e}")
            await asyncio.sleep(2)
            return []


def _extract_features(mins_by_day: dict[str, list[dict]]) -> dict:
    """从分钟线提取 5 个日内行为特征."""
    days = sorted(mins_by_day.keys())
    if len(days) < 10:
        return {"v_reversal_rate": 0, "tail_ratio": 0, "amp_trend": 0,
                "vol_conc": 0, "morning_spike_rate": 0}

    v_reversals = []
    tail_ratios = []
    daily_amps = []
    vol_concentrations = []
    morning_spikes = 0

    for d in days:
        bars = mins_by_day[d]
        if len(bars) < 6:  # need at least 6 x 30min = 3h of data
            continue

        opens = [b["open"] for b in bars]
        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        vols = [b["vol"] for b in bars]

        day_open = opens[0] if opens else 0
        day_close = closes[-1] if closes else 0
        if day_open <= 0:
            continue

        # 1. V型反转检测: 任意30分钟内跌>1% 且 后续30分钟内涨回>1%
        has_v = False
        for i in range(2, len(bars) - 3):
            drop = (lows[i] - day_open) / day_open * 100
            if drop < -1.0:
                for j in range(i + 1, min(i + 4, len(bars))):
                    recovery = (highs[j] - lows[i]) / lows[i] * 100
                    if recovery > 1.0:
                        has_v = True
                        break
            if has_v:
                break
        v_reversals.append(1 if has_v else 0)

        # 2. 尾盘异动: 最后 3 根30分钟K线的量占全天比
        tail_vol = sum(vols[-3:]) if len(vols) >= 3 else 0
        total_vol = sum(vols)
        tail_ratios.append(tail_vol / total_vol if total_vol > 0 else 0)

        # 3. 日内振幅
        day_high = max(highs) if highs else day_open
        day_low = min(lows) if lows else day_open
        daily_amps.append((day_high - day_low) / day_open * 100)

        # 4. 量能集中度: 最大单根30分钟量/全天量
        max_vol = max(vols) if vols else 1
        vol_concentrations.append(max_vol / total_vol if total_vol > 0 else 0)

        # 5. 开盘冲高回落: 前2根30min涨>2%, 但最终收盘涨<1%
        first_30m_chg = (closes[1] - day_open) / day_open * 100 if len(closes) > 1 else 0
        day_chg = (day_close - day_open) / day_open * 100
        if first_30m_chg > 2.0 and day_chg < 1.0:
            morning_spikes += 1

    n_days = len(days)

    # 振幅趋势: 后半段 vs 前半段
    mid = n_days // 2
    if mid > 5:
        amp_trend = np.mean(daily_amps[mid:]) - np.mean(daily_amps[:mid])
        amp_trend = round(float(amp_trend), 2)
    else:
        amp_trend = 0.0

    return {
        "v_reversal_rate": round(sum(v_reversals) / max(n_days, 1), 3),       # 0~1
        "tail_ratio": round(float(np.mean(tail_ratios)), 3),                    # 0~1
        "amp_trend": amp_trend,                                                 # 正=扩张, 负=收敛
        "vol_conc": round(float(np.mean(vol_concentrations)), 3),               # 0~1
        "morning_spike_rate": round(morning_spikes / max(n_days, 1), 3),       # 0~1
    }


async def extract_for_samples(limit: int = 200):
    """对 trend_samples 中的主升浪样本提取分钟线特征."""

    # 先创建特征表
    async with async_session_factory() as s:
        await s.execute(text("""
            CREATE TABLE IF NOT EXISTS trend_sample_features (
                id SERIAL PRIMARY KEY,
                ts_code VARCHAR(20) NOT NULL,
                sample_date DATE NOT NULL,
                v_reversal_rate DECIMAL(6,4),
                tail_ratio DECIMAL(6,4),
                amp_trend DECIMAL(6,2),
                vol_conc DECIMAL(6,4),
                morning_spike_rate DECIMAL(6,4),
                mins_days_available INT DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(ts_code, sample_date)
            )
        """))
        await s.commit()

    # 获取待提取的正样本
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT ts_code, sample_date
            FROM trend_samples
            WHERE label = 'major_rally'
              AND NOT EXISTS (
                  SELECT 1 FROM trend_sample_features WHERE
                  trend_sample_features.ts_code = trend_samples.ts_code
                  AND trend_sample_features.sample_date = trend_samples.sample_date
              )
            ORDER BY RANDOM()
            LIMIT :lim
        """), {"lim": limit})
        samples = [(row[0], row[1]) for row in r.fetchall()]

    logger.info(f"Extracting minute features for {len(samples)} major_rally samples")

    extracted = 0
    for idx, (ts_code, sample_date) in enumerate(samples):
        end_dt = sample_date.strftime("%Y-%m-%d") if hasattr(sample_date, 'strftime') else str(sample_date)
        start_dt = (sample_date - timedelta(days=90)).strftime("%Y-%m-%d") if hasattr(sample_date, 'strftime') else str(date.fromisoformat(str(sample_date)) - timedelta(days=90))

        raw = await _call_tushare_mins(ts_code, start_dt, end_dt)

        if not raw:
            logger.debug(f"No minute data for {ts_code} {end_dt}")
            continue

        # 按天分组
        mins_by_day = defaultdict(list)
        for bar in raw:
            day_key = bar["trade_time"][:10]
            mins_by_day[day_key].append(bar)

        feats = _extract_features(mins_by_day)
        feats["mins_days_available"] = len(mins_by_day)

        async with async_session_factory() as s:
            await s.execute(text("""
                INSERT INTO trend_sample_features
                (ts_code, sample_date, v_reversal_rate, tail_ratio, amp_trend,
                 vol_conc, morning_spike_rate, mins_days_available)
                VALUES (:c, :d, :v, :t, :a, :vc, :ms, :md)
                ON CONFLICT (ts_code, sample_date) DO UPDATE SET
                    v_reversal_rate=EXCLUDED.v_reversal_rate,
                    tail_ratio=EXCLUDED.tail_ratio,
                    amp_trend=EXCLUDED.amp_trend,
                    vol_conc=EXCLUDED.vol_conc,
                    morning_spike_rate=EXCLUDED.morning_spike_rate
            """), {
                "c": ts_code, "d": sample_date,
                "v": feats["v_reversal_rate"], "t": feats["tail_ratio"],
                "a": feats["amp_trend"], "vc": feats["vol_conc"],
                "ms": feats["morning_spike_rate"], "md": feats["mins_days_available"],
            })
            await s.commit()

        extracted += 1
        if (idx + 1) % 10 == 0:
            logger.info(f"Progress: {idx+1}/{len(samples)} extracted ({extracted} success)")

    logger.info(f"Minute feature extraction complete: {extracted}/{len(samples)}")
    return extracted


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    extracted = await extract_for_samples(limit=200)
    print(f"\nExtracted features for {extracted} major_rally samples")


if __name__ == "__main__":
    asyncio.run(main())
