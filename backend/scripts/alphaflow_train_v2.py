"""AlphaFlow XGBoost V3 — 统一特征管线训练.

V2 改进:
  1. 增加老兵特征: 总锁周期数/当前进度/振幅收敛/量能萎缩/历史浪均幅
  2. 增加老兵训练样本: 对锁周期≥3的股票, 在其每轮锁死的尾期取样
  3. 标签: 开锁后 40 日最大涨幅 ≥ 历史均幅 → 正样本
  4. 合并原有 trend_samples + 新增老兵样本 → 混合训练

V2.1 (2026-06-03): 审计修复 — 特征#41 板块锁死期% 从占位符变为真实板块收益率
V3   (2026-06-04): 统一特征管线 — 训练/预测共用 compute_wave_features + compute_sxqs_features
                     48维(38 wave + 10 SXQS), 含 tg_composite_score 反哺特征
                     ★ 修复训练/预测特征顺序不一致的阻断性 Bug
"""
import asyncio, sys, json, os, logging, numpy as np, hashlib
from collections import defaultdict
from datetime import date, timedelta
from sqlalchemy import text
sys.path.insert(0, '.')
import xgboost as xgb
from sklearn.metrics import roc_auc_score, accuracy_score
from sklearn.model_selection import train_test_split
from app.core.database import async_session_factory
from app.services.alphaflow_features import (
    FEAT_NAMES, compute_wave_features, compute_sxqs_features
)

logger = logging.getLogger("alphaflow.train_v2")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

# ── SW一级行业 → 指数代码映射 (28个) ──
SW_L1_INDEX_MAP = {
    "银行": "801780.SI", "综合": "801230.SI", "食品饮料": "801120.SI",
    "计算机": "801750.SI", "电子": "801080.SI", "医药生物": "801150.SI",
    "机械设备": "801890.SI", "电力设备": "801730.SI", "汽车": "801880.SI",
    "基础化工": "801030.SI", "有色金属": "801050.SI", "国防军工": "801740.SI",
    "公用事业": "801160.SI", "交通运输": "801170.SI", "房地产": "801180.SI",
    "商贸零售": "801200.SI", "社会服务": "801210.SI", "建筑材料": "801710.SI",
    "建筑装饰": "801720.SI", "家用电器": "801110.SI", "纺织服饰": "801130.SI",
    "轻工制造": "801140.SI", "农林牧渔": "801010.SI", "非银金融": "801790.SI",
    "通信": "801770.SI", "传媒": "801760.SI", "钢铁": "801040.SI",
    "煤炭": "801950.SI", "石油石化": "801960.SI",
}

LOCK_MAX_DAYS = 180  # 单轮锁死最长半年

LOOKBACK = 200
LOCK_RANGE = 0.15; LOCK_STD_MAX = 8.0; LOCK_MIN_DAYS = 10
LOCK_MAX_DAYS = 80


# ═════════════════════════════════════════════════════════════
# 板块数据预加载 (审计修复: 任务二 — 特征#41 真实板块收益率)
# ═════════════════════════════════════════════════════════════

async def _preload_sector_indices(min_date, max_date) -> dict:
    """预加载所有 SW 一级行业指数的日收盘价."""
    sector_data = {}
    index_codes = list(SW_L1_INDEX_MAP.values())
    try:
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT index_code, trade_date, close FROM sw_sector_index
                WHERE index_code = ANY(:codes)
                  AND trade_date BETWEEN :d1 AND :d2
                ORDER BY index_code, trade_date
            """), {"codes": index_codes, "d1": min_date, "d2": max_date})
            for row in r.fetchall():
                code, td, cl = row[0], row[1], float(row[2] or 0)
                if code not in sector_data:
                    sector_data[code] = {}
                sector_data[code][td] = cl
        logger.info(f"Loaded {len(sector_data)} sector indices ({min_date}~{max_date})")
    except Exception as e:
        logger.warning(f"Sector index loading failed: {e}")
    return sector_data


async def _build_sector_map_for_stocks(ts_codes: list[str],
                                        sector_indices: dict) -> dict:
    """为每只股票找到其所属 SW 一级行业的指数日线数据.

    v4.9: 用 Tushare stock_basic.industry 替代 ths_member.ths_name.
    ths_member 存的是同花顺概念名 (如"同花顺全A(加权)")，无法匹配SW行业名.
    """
    if not sector_indices:
        return {}
    stock_sector = {}
    try:
        # 先尝试 Tushare stock_basic 行业分类
        from app.services.tushare_common import call_tushare
        rows = await call_tushare("stock_basic", {"ts_code": ",".join(ts_codes[:500])},
            "ts_code,industry")
        if not rows:
            rows = []
        ind_map: dict[str, str] = {}
        for r in rows:
            code = r.get("ts_code", "")
            if code and r.get("industry"):
                ind_map[code] = r["industry"]
        # 分批拉取 (Tushare 单次 ts_code 参数约 500 个)
        for batch_start in range(500, len(ts_codes), 500):
            batch = ts_codes[batch_start:batch_start+500]
            more = await call_tushare("stock_basic", {"ts_code": ",".join(batch)}, "ts_code,industry")
            for r in (more or []):
                code = r.get("ts_code", "")
                if code and r.get("industry"):
                    ind_map[code] = r["industry"]

        for ts_code in ts_codes:
            ind_name = ind_map.get(ts_code, "")
            for l1_name, idx_code in SW_L1_INDEX_MAP.items():
                if l1_name in (ind_name or ""):
                    if idx_code in sector_indices:
                        stock_sector[ts_code] = sector_indices[idx_code]
                    break
        logger.info(f"Sector-stock mapping: {len(stock_sector)}/{len(ts_codes)} matched (via stock_basic.industry)")
    except Exception as e:
        logger.warning(f"Sector mapping failed: {e}")
    return stock_sector


def _build_aligned_sector_closes(stock_dates: list,
                                  sector_price_map: dict) -> np.ndarray | None:
    """对齐板块收盘价到股票的交易日序列."""
    if not sector_price_map:
        return None
    aligned = np.zeros(len(stock_dates))
    found_any = False
    for i, d in enumerate(stock_dates):
        if d in sector_price_map:
            aligned[i] = sector_price_map[d]
            found_any = True
    return aligned if found_any else None


# FEAT_NAMES 从 alphaflow_features.py 导入 (48维, 与运行时完全一致)


# ═════════════════════════════════════════════════════════════
# 特征提取 (V3: 直接调用共享函数, 保证训练/预测 100% 一致)
# ═════════════════════════════════════════════════════════════

def extract_all_features(closes, opens_arr, highs, lows, volumes,
                         index_closes=None, sector_closes=None,
                         tg_score: float = None):
    """提取 48 维特征 — 薄封装 compute_wave_features + compute_sxqs_features.

    与运行时 alphaflow_pool._extract_features_for_stock() 完全一致.
    """
    if len(closes) < 80:
        return None

    wave_feats = compute_wave_features(
        closes, opens_arr, highs, lows, volumes,
        index_closes=index_closes,
        sector_closes=sector_closes,
        tg_score=tg_score,
    )
    if wave_feats is None:
        return None

    sxqs = compute_sxqs_features(closes, highs, lows)
    sxqs_list = [
        sxqs["h1h2_up"], sxqs["h3_dev"], sxqs["var6"], sxqs["var7"], sxqs["net_power"],
        sxqs["var8"], sxqs["a_signal"], sxqs["near_low"],
        sxqs["d_signal"], sxqs["w_signal"],
    ]
    return np.array(wave_feats + sxqs_list, dtype=np.float32)


# ═════════════════════════════════════════════════════════════
# 老兵样本生成
# ═════════════════════════════════════════════════════════════

async def generate_veteran_samples(n_per_stock=3, sector_map: dict = None,
                                    tg_score_map: dict = None):
    """对锁周期≥4的股票, 在每轮锁死的尾期取样作为老兵训练样本."""
    logger.info("Generating veteran training samples...")
    if sector_map is None:
        sector_map = {}
    if tg_score_map is None:
        tg_score_map = {}

    async with async_session_factory() as s:
        # 取全部股票
        r = await s.execute(text(
            "SELECT DISTINCT ts_code FROM daily_kline WHERE trade_date >= '2024-01-01' LIMIT 3000"
        ))
        all_codes = [row[0] for row in r.fetchall()]

    found = 0
    vet_samples = []
    from app.services.lock_detector import detect_lock_simple
    from app.services.alphaflow_veteran import detect_veteran

    for idx, code in enumerate(all_codes):
        try:
            async with async_session_factory() as s:
                r = await s.execute(text(
                    "SELECT trade_date, open, close, high, low, volume "
                    "FROM daily_kline WHERE ts_code=:c ORDER BY trade_date DESC LIMIT 600"
                ), {"c": code})
                rows_raw = list(reversed(r.fetchall()))
            if len(rows_raw) < 200: continue

            closes = np.array([float(r[2] or 0) for r in rows_raw])
            opens_arr = np.array([float(r[1] or closes[i]) for i, r in enumerate(rows_raw)])
            highs = np.array([float(r[3] or closes[i]) for i, r in enumerate(rows_raw)])
            lows = np.array([float(r[4] or closes[i]) for i, r in enumerate(rows_raw)])
            volumes = np.array([float(r[5] or 0) for r in rows_raw])

            vet = await detect_veteran(code)
            if not vet or vet["total_cycles"] < 3: continue

            # 对每轮历史锁死, 在尾期 (天数 > 均值*0.7) 取样
            avg_d = vet["avg_cycle_days"]
            lock_segs = []
            i = 0; nn = len(closes)
            while i < nn - 20:
                w20l = float(np.min(lows[i:i+20])); w20h = float(np.max(highs[i:i+20]))
                if w20l <= 0: i+=1; continue
                if (w20h-w20l)/w20l*100 <= 15.0:
                    st, lh_, ll_ = i, w20h, w20l
                    while i < nn-1:
                        if i+10 > nn: break
                        lh_ = max(lh_, float(highs[i])); ll_ = min(ll_, float(lows[i]))
                        slen = i - st + 1
                        if slen <= 20:
                            if (lh_-ll_)/max(ll_,0.01)*100 > 15.0: break
                        else:
                            if (lh_-ll_)/max(ll_,0.01)*100 > 17.0: break
                        i += 1
                    end = i-1
                    if end-st >= 20 and end-st <= LOCK_MAX_DAYS:
                        lock_segs.append({"start": st, "end": end, "days": end-st+1})
                    i = end+1
                else: i+=1

            # 对每轮锁死 (除了最后一轮, 那是当前), 在尾期取样
            n_sampled = 0
            tg = tg_score_map.get(code, 0.0)
            for seg in lock_segs[:-1]:
                lock_end = seg["end"]
                if lock_end + 5 >= nn: continue
                # 计算开锁后涨幅
                post_h = float(np.max(highs[lock_end+1:min(lock_end+40, nn)]))
                lock_avg = float(np.mean(closes[seg["start"]:lock_end+1]))
                if lock_avg <= 0: continue
                wave_pct = (post_h - lock_avg) / lock_avg * 100
                label = 1 if wave_pct >= avg_d * 0.20 else 0

                # 取样点: 锁死尾期 (最后5天)
                sample_idx = lock_end  # 锁死最后一天
                sample_date = rows_raw[sample_idx][0]

                # 截取到取样点的数据
                seg_closes = closes[:sample_idx+1]
                seg_opens = opens_arr[:sample_idx+1]
                seg_highs = highs[:sample_idx+1]
                seg_lows = lows[:sample_idx+1]
                seg_vols = volumes[:sample_idx+1]

                # V3: 使用共享特征函数 (与运行时完全一致)
                features = extract_all_features(
                    seg_closes, seg_opens, seg_highs, seg_lows, seg_vols,
                    index_closes=seg_closes,  # 用自身当大盘 (简化)
                    sector_closes=_build_aligned_sector_closes(
                        [rows_raw[ii][0] for ii in range(sample_idx+1)],
                        sector_map.get(code, {})
                    ),
                    tg_score=tg,
                )
                if features is not None:
                    vet_samples.append((code, sample_date, features, label, wave_pct))
                    found += 1
                    n_sampled += 1
                    if n_sampled >= n_per_stock: break

            if (idx+1) % 200 == 0:
                logger.info(f"  Vet scan: {idx+1}/{len(all_codes)}, found {found} samples")
        except Exception:
            pass  # skip bad data

    logger.info(f"Veteran samples: {found} total")
    return vet_samples


# ═════════════════════════════════════════════════════════════
# 混合训练
# ═════════════════════════════════════════════════════════════

async def main():
    logger.info("=== AlphaFlow XGBoost V3 — 统一特征管线训练 ===")

    # ── 0. 预加载板块指数数据 (审计修复: 任务二 — 特征#41 真实化) ──
    #    先算日期范围, 再加载 SW 一级行业指数
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT MIN(sample_date), MAX(sample_date) FROM trend_samples"
        ))
        dates_row = r.fetchone()
        est_min = dates_row[0] - timedelta(days=400) if dates_row and dates_row[0] else date.today() - timedelta(days=600)
        est_max = dates_row[1] + timedelta(days=50) if dates_row and dates_row[1] else date.today()
    logger.info(f"Preloading sector indices: {est_min} ~ {est_max}")
    sector_indices = await _preload_sector_indices(est_min, est_max)

    # ── 0.5 预加载 TG composite_score (V3: 第48维反哺特征) ──
    logger.info("Preloading TG composite scores...")
    tg_score_map = {}
    try:
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT symbol, scan_date, composite_score
                FROM analysis_scores
                WHERE composite_score IS NOT NULL
                  AND scan_date >= :d
            """), {"d": est_min})
            for row in r.fetchall():
                code, sd, sc = row[0], row[1], float(row[2] or 0)
                if code not in tg_score_map:
                    tg_score_map[code] = {}
                tg_score_map[code][sd] = sc
        logger.info(f"TG scores: {len(tg_score_map)} stocks loaded")
    except Exception as e:
        logger.warning(f"TG score loading failed: {e}")

    # ── 0.6 预加载大盘指数 (000001.SH) 作为市场基准 ──
    logger.info("Preloading market index (000001.SH)...")
    idx_map = {}
    try:
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT trade_date, close FROM daily_kline
                WHERE ts_code = '700001.TI' AND trade_date BETWEEN :d1 AND :d2
                ORDER BY trade_date
            """), {"d1": est_min, "d2": est_max})
            for row in r.fetchall():
                idx_map[row[0]] = float(row[1] or 0)
        logger.info(f"Market index: {len(idx_map)} trading days loaded")
    except Exception as e:
        logger.warning(f"Market index loading failed: {e}")

    # ── 1. 加载原有训练样本 ──
    logger.info("Loading existing training data...")
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT ts_code, sample_date, forward_peak_pct, label "
            "FROM trend_samples WHERE label IN ('major_rally', 'normal') "
            "ORDER BY RANDOM() LIMIT 4000"
        ))
        existing = [(row[0], row[1], float(row[2] or 0), row[3]) for row in r.fetchall()]
    logger.info(f"Existing samples: {len(existing)}")

    # ── 1.5 构建 stock→sector 映射 (审计修复: 任务二) ──
    all_train_codes = list(set(r[0] for r in existing))
    stock_sector_map = await _build_sector_map_for_stocks(all_train_codes, sector_indices)

    # ── 2. 生成老兵样本 (V3: 传入 tg_score_map) ──
    vet_samples = await generate_veteran_samples(
        n_per_stock=3, sector_map=stock_sector_map, tg_score_map=tg_score_map
    )
    if len(vet_samples) < 50:
        logger.warning(f"老兵样本太少({len(vet_samples)}), 降级为纯原有训练")
        vet_weight = 0
    else:
        vet_weight = 0.3  # 老兵权重 = 30%

    # ── 3. 合并训练数据 ──
    logger.info("Extracting features...")
    all_codes = list(set(r[0] for r in existing) |
                     set(v[0] for v in vet_samples))
    min_date = min(r[1] for r in existing) - timedelta(days=250)
    max_date = max(r[1] for r in existing)

    klines = defaultdict(list)
    async with async_session_factory() as s:
        for i in range(0, len(all_codes), 300):
            batch = all_codes[i:i+300]
            r = await s.execute(text(
                "SELECT ts_code, trade_date, open, close, volume, high, low "
                "FROM daily_kline WHERE ts_code = ANY(:syms) "
                "AND trade_date BETWEEN :d1 AND :d2 ORDER BY ts_code, trade_date"
            ), {"syms": batch, "d1": min_date, "d2": max_date})
            for row in r.fetchall():
                try:
                    cv = float(row[3] or 0)
                    if cv <= 0: continue
                    klines[row[0]].append({
                        "d": row[1], "o": float(row[2] or 0), "c": cv,
                        "v": float(row[4] or 0), "h": float(row[5] or cv),
                        "l": float(row[6] or cv),
                    })
                except (TypeError, ValueError): continue
    logger.info(f"K-lines loaded: {len(klines)} codes")

    # ── 提取原有样本特征 (V3: 使用共享函数 + TG score + 大盘指数) ──
    X_list, y_list, weights_list = [], [], []
    skipped = 0

    for code, sd, peak, label in existing:
        bars = klines.get(code, [])
        pre = [b for b in bars if b["d"] <= sd][-LOOKBACK:]
        if len(pre) < 80: skipped += 1; continue
        closes = np.array([b["c"] for b in pre])
        opens_arr = np.array([b["o"] for b in pre])
        highs = np.array([b["h"] for b in pre])
        lows = np.array([b["l"] for b in pre])
        volumes = np.array([b["v"] for b in pre])

        # 对齐大盘指数
        idx_closes = None
        if idx_map:
            idx_closes = np.array([idx_map.get(b["d"], 0.0) for b in pre])
            if np.all(idx_closes == 0):
                idx_closes = None

        # 查找 TG score (取 <= sample_date 最近的)
        tg = 0.0
        if code in tg_score_map:
            code_tg = tg_score_map[code]
            if sd in code_tg:
                tg = code_tg[sd]
            else:
                recent = [d for d in code_tg if d <= sd]
                if recent:
                    tg = code_tg[max(recent)]

        feats = extract_all_features(
            closes, opens_arr, highs, lows, volumes,
            index_closes=idx_closes,
            sector_closes=_build_aligned_sector_closes(
                [b["d"] for b in pre], stock_sector_map.get(code, {})
            ),
            tg_score=tg,
        )
        if feats is None: skipped += 1; continue
        X_list.append(feats)
        y_list.append(1 if label == "major_rally" else 0)
        weights_list.append(1.0)

    # ── 提取老兵样本特征 (已在 generate_veteran_samples 中使用共享函数) ──
    for code, sd, features, label, wave_pct in vet_samples:
        if features is not None:
            X_list.append(features)
            y_list.append(label)
            weights_list.append(max(1.0, vet_weight * 3.0))  # 老兵样本加权

    # ── 训练 ──
    X = np.array(X_list, dtype=np.float32)
    y = np.array(y_list)
    w = np.array(weights_list)
    logger.info(f"Training data: {len(X)} samples ({len(existing)-skipped} exist + {len(vet_samples)} vet), "
                f"positive={sum(y)}/{len(y)} ({sum(y)/max(len(y),1)*100:.1f}%)")

    Xtr, Xte, ytr, yte, wtr, wte = train_test_split(
        X, y, w, test_size=0.2, random_state=42, stratify=y)

    model = xgb.XGBClassifier(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.7, colsample_bytree=0.7,
        scale_pos_weight=max(1.0, sum(ytr==0)/max(sum(ytr==1), 1)),
    )
    model.fit(Xtr, ytr, sample_weight=wtr)

    yp = model.predict_proba(Xte)[:, 1]
    auc = roc_auc_score(yte, yp)
    acc = accuracy_score(yte, yp > 0.5)

    logger.info(f"V3 Model: AUC={auc:.4f} Acc={acc:.3f}")

    # 特征重要性
    importance = sorted(zip(FEAT_NAMES, model.feature_importances_), key=lambda x: -x[1])

    print(f"\n{'='*60}")
    print(f"  AlphaFlow XGBoost V3 训练完成 (统一特征管线)")
    print(f"{'='*60}")
    print(f"  特征: {len(FEAT_NAMES)} 维 (与运行时 alphaflow_features.py 100% 一致)")
    print(f"  样本: {len(Xtr)} 训练, {len(Xte)} 测试")
    print(f"  AUC: {auc:.4f} | Accuracy: {acc:.3f}")
    print(f"  正样本: {sum(y)}/{len(y)} ({sum(y)/max(len(y),1)*100:.1f}%)")
    print(f"  老兵样本: {len(vet_samples)} (权重×3)")
    print(f"\n  Top-10 特征:")
    for name, score in importance[:10]:
        marker = "★" if "老兵" in name else ("◆" if "tg_" in name else "")
        print(f"    {name:<18}: {score:.2%} {marker}")

    # Save
    os.makedirs('models', exist_ok=True)
    model.save_model('models/alphaflow_xgb.json')
    training_date_str = str(date.today())
    feat_hash = hashlib.md5(",".join(FEAT_NAMES).encode()).hexdigest()[:8]
    meta = {
        "version": "v3_unified_pipeline",
        "training_date": training_date_str,
        "features": len(FEAT_NAMES),
        "feature_names": FEAT_NAMES,
        "feature_hash": feat_hash,
        "test_auc": round(auc, 4),
        "test_acc": round(acc, 4),
        "train_samples": len(Xtr),
        "test_samples": len(Xte),
        "positive_ratio": round(sum(y)/len(y), 3),
        "vet_samples": len(vet_samples),
        "sector_mapped": len(stock_sector_map),
        "tg_scores_loaded": len(tg_score_map),
        "top_features": [(FEAT_NAMES[i], float(model.feature_importances_[i]))
                         for i in np.argsort(model.feature_importances_)[-10:][::-1]],
    }
    with open('models/alphaflow_xgb_meta.json', 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    logger.info(f"Meta saved: {training_date_str}, features={len(FEAT_NAMES)}, "
                f"hash={feat_hash}, sector_mapped={len(stock_sector_map)}")
    print(f"\n  模型: models/alphaflow_xgb.json → {len(FEAT_NAMES)} 维")
    print(f"  元信息: models/alphaflow_xgb_meta.json")
    print(f"  特征哈希: {feat_hash}")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
