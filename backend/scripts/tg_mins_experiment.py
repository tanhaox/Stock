"""TG分钟线验证 — 交易员视角: 20天分时图能否预判T+2涨跌?

从 signal_history 中采样 100 赢(>2%) + 100 亏(<-2%) 记录,
下载推荐前20天的5分钟线, 提取分时特征, 训练分类器.
验证: 分钟线信息是否能提升 TG 推荐的准确性.
"""
import asyncio, sys, numpy as np, os, time, json, logging
from collections import defaultdict
from datetime import date, timedelta
import httpx
from dotenv import load_dotenv
from sqlalchemy import text
sys.path.insert(0, 'C:/AI-Agent-Local/Stock/backend')

load_dotenv('C:/AI-Agent-Local/Stock/backend/.env')
TOKEN = os.getenv('TUSHARE_TOKEN')
SEM = asyncio.Semaphore(3)
logger = logging.getLogger("tg_mins_exp")

from app.core.database import async_session_factory


async def download_5min(symbol, start_dt, end_dt):
    """下载5分钟线."""
    async with SEM:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post('https://api.tushare.pro', json={
                'api_name': 'stk_mins', 'token': TOKEN,
                'params': {'ts_code': symbol, 'freq': '5min',
                           'start_date': f'{start_dt} 09:00:00',
                           'end_date': f'{end_dt} 15:00:00'},
                'fields': 'ts_code,trade_time,open,close,high,low,vol,amount'
            })
            data = resp.json()
            if data.get('code') != 0: return []
            return data.get('data', {}).get('items', []) or []


def extract_trader_features(bars_5min):
    """交易员视角的10维分时特征.

    交易员每天看的就是这些:
      - 早盘有没有资金进场 (9:30-10:30 的量/方向)
      - 尾盘有没有人在收 (14:30-15:00 的量/方向)
      - 盘中跌了有没有人接 (V反频率)
      - 放量是拉升还是出货 (巨量K线方向)
      - 全天缩量还是放量 (量能趋势)
      - 价格稳定性 (波动率)
    """
    if len(bars_5min) < 200:
        return None

    closes = np.array([float(b[3]) for b in bars_5min])
    opens = np.array([float(b[2]) for b in bars_5min])
    highs = np.array([float(b[4]) for b in bars_5min])
    lows = np.array([float(b[5]) for b in bars_5min])
    vols = np.array([float(b[6]) for b in bars_5min])
    n = len(bars_5min)

    # Group by day for daily metrics
    by_day = defaultdict(list)
    for b in bars_5min:
        d = b[1][:10]
        by_day[d].append({
            'o': float(b[2]), 'c': float(b[3]), 'h': float(b[4]),
            'l': float(b[5]), 'v': float(b[6]), 'time': b[1]
        })
    days = sorted(by_day.keys())
    if len(days) < 10: return None

    features = []

    # 1. 尾盘量比均值: 最后20根5分钟线(约100分钟)的量占全天比
    tail_ratios = []
    for d in days:
        bars_d = by_day[d]
        if len(bars_d) < 40: continue
        total_v = sum(b['v'] for b in bars_d)
        tail_v = sum(b['v'] for b in bars_d[-20:])
        tail_ratios.append(tail_v / max(total_v, 1))
    features.append(round(float(np.mean(tail_ratios)), 3))

    # 2. 尾盘方向: 尾盘涨是买入迹象, 尾盘跌是出货迹象
    tail_changes = []
    for d in days:
        bars_d = by_day[d]
        if len(bars_d) < 40: continue
        tail_open = bars_d[-20]['o'] if len(bars_d) >= 20 else 0
        tail_close = bars_d[-1]['c']
        if tail_open > 0:
            tail_changes.append((tail_close - tail_open) / tail_open * 100)
    features.append(round(float(np.mean(tail_changes)), 2))

    # 3. V反频率: 日内跌幅>1.5%后30分钟内反弹>1%的次数/天
    v_count = 0
    for d in days:
        bars_d = by_day[d]
        for i in range(5, len(bars_d) - 5):
            drop = (bars_d[i]['l'] - bars_d[i-5]['c']) / bars_d[i-5]['c'] * 100
            if drop < -1.5:
                for j in range(i + 1, min(i + 7, len(bars_d))):
                    recovery = (bars_d[j]['h'] - bars_d[i]['l']) / bars_d[i]['l'] * 100
                    if recovery > 1.0:
                        v_count += 1
                        break
    features.append(round(v_count / max(len(days), 1), 1))

    # 4. 开盘冲高回落率: 早盘冲高>2%但收盘<1%的天数比 (诱多信号)
    morning_trap = 0
    for d in days:
        bars_d = by_day[d]
        if len(bars_d) < 40: continue
        first_6 = bars_d[:6]
        morning_high = max(b['h'] for b in first_6)
        day_open = bars_d[0]['o']
        day_close = bars_d[-1]['c']
        if (morning_high - day_open) / day_open * 100 > 2 and (day_close - day_open) / day_open * 100 < 1:
            morning_trap += 1
    features.append(round(morning_trap / max(len(days), 1), 3))

    # 5. 量能集中度: 最大单根K线量/日均量
    concs = []
    for d in days:
        day_vs = [b['v'] for b in by_day[d]]
        if day_vs:
            concs.append(max(day_vs) / max(np.mean(day_vs), 1))
    features.append(round(float(np.mean(concs)), 2))

    # 6. 巨量脉冲方向: 单根量>3x均量的K线, 涨的比例
    avg_v = float(np.mean(vols))
    spike_up = sum(1 for i in range(1, n) if vols[i] > avg_v * 3 and closes[i] > opens[i])
    spike_total = sum(1 for i in range(1, n) if vols[i] > avg_v * 3)
    features.append(round(spike_up / max(spike_total, 1), 2))

    # 7. 连续红量: 最长连续 close>open 天数
    reds = closes > opens
    max_red = 0; cur = 0
    for v in reds:
        if v: cur += 1; max_red = max(max_red, cur)
        else: cur = 0
    features.append(max_red)

    # 8. 低点量特征: 每天最低价附近的量 vs 日均量 (低点缩量=无人卖, 低点放量=恐慌)
    low_vol_ratios = []
    for d in days:
        bars_d = by_day[d]
        if len(bars_d) < 20: continue
        avg_d_v = np.mean([b['v'] for b in bars_d])
        low_idx = min(range(len(bars_d)), key=lambda i: bars_d[i]['l'])
        low_v = bars_d[low_idx]['v']
        low_vol_ratios.append(low_v / max(avg_d_v, 1))
    features.append(round(float(np.mean(low_vol_ratios)), 2))

    # 9. 振幅趋势: 前10天日均振幅 vs 后10天
    amps = []
    for d in days:
        bars_d = by_day[d]
        if bars_d:
            d_high = max(b['h'] for b in bars_d)
            d_low = min(b['l'] for b in bars_d)
            amps.append((d_high - d_low) / max(d_low, 0.01) * 100)
    mid = len(amps) // 2
    if mid >= 3:
        features.append(round(float(np.mean(amps[mid:]) - np.mean(amps[:mid])), 1))
    else:
        features.append(0.0)

    # 10. 净主动买入估算: (尾盘量×方向 - 早盘量×方向) 的日均
    net_active = []
    for d in days:
        bars_d = by_day[d]
        if len(bars_d) < 40: continue
        morning = bars_d[:12]
        afternoon = bars_d[-12:]
        m_vol = sum(b['v'] for b in morning)
        a_vol = sum(b['v'] for b in afternoon)
        m_chg = (morning[-1]['c'] - morning[0]['o']) / max(morning[0]['o'], 0.01) * 100
        a_chg = (afternoon[-1]['c'] - afternoon[0]['o']) / max(afternoon[0]['o'], 0.01) * 100
        net_active.append((a_vol * np.sign(a_chg) - m_vol * np.sign(m_chg)) / max(m_vol + a_vol, 1))
    features.append(round(float(np.mean(net_active)), 3))

    return features


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    # ── 采样 ──
    async with async_session_factory() as s:
        r = await s.execute(text("""
            (SELECT symbol, scan_date, ret_t2, 'win' as label
             FROM signal_history WHERE ret_t2 > 2.0 AND scan_date >= '2025-01-01'
             ORDER BY RANDOM() LIMIT 100)
            UNION ALL
            (SELECT symbol, scan_date, ret_t2, 'loss' as label
             FROM signal_history WHERE ret_t2 < -2.0 AND scan_date >= '2025-01-01'
             ORDER BY RANDOM() LIMIT 100)
        """))
        samples = [(row[0], row[1], float(row[2] or 0), row[3]) for row in r.fetchall()]

    logger.info(f"Experiment: {len(samples)} labeled samples ({sum(1 for s in samples if s[3]=='win')} win + {sum(1 for s in samples if s[3]=='loss')} loss)")

    # ── 下载5分钟线 ──
    features_win = []; features_loss = []
    downloaded = 0; skipped = 0

    for sym, sd, ret_t2, label in samples:
        if downloaded >= 60 and label == 'win' and len(features_win) >= 30:
            continue  # Enough win samples
        if downloaded >= 60 and label == 'loss' and len(features_loss) >= 30:
            continue

        start_dt = sd - timedelta(days=25)
        end_dt = sd
        bars = await download_5min(sym, str(start_dt), str(end_dt))

        if len(bars) < 200:
            skipped += 1
            continue

        feats = extract_trader_features(bars)
        if feats is None:
            skipped += 1
            continue

        if label == 'win':
            features_win.append(feats)
        else:
            features_loss.append(feats)
        downloaded += 1

        if downloaded % 10 == 0:
            logger.info(f"  Downloaded {downloaded}: {len(features_win)}win/{len(features_loss)}loss")

    logger.info(f"Download complete: {len(features_win)} win, {len(features_loss)} loss, {skipped} skipped")

    if len(features_win) < 20 or len(features_loss) < 20:
        print("Insufficient samples")
        return

    # ── 训练对比 ──
    n = min(len(features_win), len(features_loss))
    X = np.array(features_win[:n] + features_loss[:n], dtype=np.float32)
    y = np.array([1]*n + [0]*n)
    X = np.nan_to_num(X, 0, 0, 0)

    from sklearn.model_selection import train_test_split
    from sklearn.metrics import roc_auc_score, accuracy_score
    import xgboost as xgb

    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.3, random_state=42, stratify=y)
    model = xgb.XGBClassifier(n_estimators=100, max_depth=3, learning_rate=0.05,
                               subsample=0.7, colsample_bytree=0.7)
    model.fit(Xtr, ytr)
    yp = model.predict_proba(Xte)[:, 1]
    auc = roc_auc_score(yte, yp)
    acc = accuracy_score(yte, yp > 0.5)

    feat_names = [
        '尾盘量比', '尾盘方向', 'V反频率', '早盘诱多率',
        '量能集中度', '巨量脉冲方向', '连续红量', '低点量特征',
        '振幅趋势', '净主动买入',
    ]
    importances = sorted(zip(feat_names, model.feature_importances_), key=lambda x: -x[1])

    print(f"\n{'='*60}")
    print(f"  交易员视角: 20天分时图 → T+2 涨跌预测")
    print(f"{'='*60}")
    print(f"  样本: {n}赢 vs {n}亏 | AUC: {auc:.4f} | 准确率: {acc:.1%}")
    print(f"  随机基准 AUC=0.50, Acc=50%")
    print()
    print(f"  Top-5 分时特征:")
    for name, score in importances[:5]:
        print(f"    {name:<14}: {score:.1%}")

    # Feature comparison: win vs loss means
    print(f"\n  {'特征':<14} {'赢均值':<10} {'亏均值':<10} {'差异':<10}")
    print(f"  {'-'*44}")
    for i, name in enumerate(feat_names):
        wm = np.mean([f[i] for f in features_win[:n]])
        lm = np.mean([f[i] for f in features_loss[:n]])
        diff = wm - lm
        sig = '←' if abs(diff) > abs(lm) * 0.15 else ''
        print(f"  {name:<14} {wm:<10.3f} {lm:<10.3f} {diff:+.3f} {sig}")

    # Save model
    os.makedirs('models', exist_ok=True)
    model.save_model('models/tg_mins_classifier.json')
    print(f"\n  模型: models/tg_mins_classifier.json")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
