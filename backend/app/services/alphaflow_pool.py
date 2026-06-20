"""AlphaFlow 候选池追踪系统 — 入池不踢, 每日加权, 三层分级.

核心原则:
  - 概率 ≥ 40% → 入池, 永不出池
  - 每日更新概率分 + 3分钟吸筹分
  - 连续 5 天概率 < 30% → 降为休眠 (不删除, 仍监控)
  - 分层: 🔥活跃 / 👀观察 / 💤休眠
"""

import asyncio, logging, numpy as np, json, os
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("alphaflow.pool")

# ── 阈值 ──
ENTRY_THRESHOLD = 0.40       # XGBoost概率门槛
LOCK_AMPLITUDE_MAX = 15.0   # 锁死最大振幅%
MIN_PRICE = 5.0              # 最低价格 (排除僵尸股)
MIN_DAILY_AMOUNT = 50000     # 最低日均成交额(千元, 约5000万/日)
DORMANT_THRESHOLD = 0.30     # 低于此连续 5 天 → 休眠
REVIVE_THRESHOLD = 0.50      # 休眠后回到此概率 → 复活到观察层
ACTIVE_THRESHOLD = 0.60      # 观察层升活跃的阈值
ACTIVE_DAYS = 3              # 连续 N 天高于阈值 → 升级


async def create_pool_tables():
    """Create pool tables (alphaflow_pool + pool_history now via ORM)."""
    logger.info("Pool tables handled by ORM (data_models.py)")


async def _load_xgb_model():
    """加载训练好的 XGBoost 模型 (带版本校验).

    审计修复 (任务二): 运行时校验模型特征数与代码 FEAT_NAMES 一致性,
    防止特征漂移导致 XGBoost 输入乱序但不出错。
    """
    import xgboost as xgb
    import json as _json
    model = xgb.XGBClassifier()
    import os as _os
    _SRV_DIR = _os.path.dirname(_os.path.abspath(__file__))
    _ROOT = _os.path.dirname(_os.path.dirname(_SRV_DIR))
    model_path = _os.path.join(_ROOT, 'models', 'alphaflow_xgb.json')
    meta_path = _os.path.join(_ROOT, 'models', 'alphaflow_xgb_meta.json')

    if not os.path.exists(model_path):
        logger.warning(f"Model not found at {model_path}, using fallback rules")
        return None

    model.load_model(model_path)

    # ── 版本校验 ──
    from app.services.alphaflow_features import FEAT_NAMES as RUNTIME_FEATS
    model_n_features = model.get_booster().num_features() if hasattr(model, 'get_booster') else 0

    # 校验1: 特征数一致
    if model_n_features > 0 and model_n_features != len(RUNTIME_FEATS):
        logger.error(
            f"❌ XGBoost FEATURE DIMENSION MISMATCH: "
            f"model trained with {model_n_features} features, "
            f"but runtime FEAT_NAMES has {len(RUNTIME_FEATS)}. "
            f"Predictions WILL be wrong. "
            f"IMMEDIATE ACTION: re-train with "
            f"`python -m scripts.alphaflow_train_v2`"
        )
        return None

    # 校验2: 元信息完整性
    if os.path.exists(meta_path):
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                meta = _json.load(f)

            meta_features = meta.get("feature_names", [])
            meta_hash = meta.get("feature_hash", "")
            runtime_hash = __import__('hashlib').md5(
                ",".join(RUNTIME_FEATS).encode()
            ).hexdigest()[:8]

            # 校验2a: 特征名列表逐位比对
            if meta_features and meta_features != RUNTIME_FEATS:
                diffs = [(i, m, r) for i, (m, r) in enumerate(
                    zip(meta_features, RUNTIME_FEATS)) if m != r]
                logger.error(
                    f"❌ XGBoost FEATURE NAME MISMATCH: "
                    f"{len(diffs)} positions differ. "
                    f"First 3: {diffs[:3]}. "
                    f"Re-train required."
                )
                return None

            # 校验2b: 特征哈希一致性
            if meta_hash and meta_hash != runtime_hash:
                logger.error(
                    f"❌ XGBoost FEATURE HASH MISMATCH: "
                    f"model={meta_hash} vs runtime={runtime_hash}. "
                    f"Feature definitions have changed since training."
                )
                return None

            # 校验2c: 训练日期
            training_date = meta.get("training_date", "")
            if not training_date:
                logger.warning(
                    "⚠ XGBoost meta missing training_date — "
                    "cannot verify if model was trained after feature #41 fix. "
                    "Proceeding with caution."
                )
            else:
                logger.info(
                    f"XGBoost v2 model loaded: {model_n_features} features, "
                    f"trained={training_date}, AUC={meta.get('test_auc', '?')}"
                )
        except Exception as e:
            logger.warning(f"XGBoost meta checks failed (non-fatal): {e}")
    else:
        logger.warning(
            "⚠ No XGBoost meta file — cannot verify model version. "
            "Proceeding without checks."
        )

    return model


async def _extract_features_for_stock(ts_code: str, scan_date: date,
                                       idx_map: dict = None, sector_map: dict = None,
                                       tg_score: float = None) -> np.ndarray | None:
    """提取特征 — 传入指数映射用于计算大盘/板块环境, 及 TG 管线评分为第48维反哺特征."""
    from app.services.alphaflow_features import compute_wave_features, compute_sxqs_features
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT trade_date, open, close, volume, amount, high, low
            FROM daily_kline WHERE ts_code = :c AND trade_date <= :d
            ORDER BY trade_date DESC LIMIT 220
        """), {"c": ts_code, "d": scan_date})
        rows = list(reversed(r.fetchall()))
    if len(rows) < 80: return None
    closes = np.array([float(r[2] or 0) for r in rows])
    opens_z = np.array([float(r[1] or closes[i]) for i, r in enumerate(rows)])
    volumes = np.array([float(r[3] or 0) for r in rows])
    highs = np.array([float(r[5] or closes[i]) for i, r in enumerate(rows)])
    lows = np.array([float(r[6] or closes[i]) for i, r in enumerate(rows)])
    if closes[-1] <= 0: return None

    # Build aligned index closes array
    idx_closes = None
    if idx_map:
        idx_closes = np.zeros(len(rows))
        for i, r in enumerate(rows):
            idx_closes[i] = idx_map.get(r[0], 0.0)
        if np.all(idx_closes == 0): idx_closes = None

    # Build aligned sector closes array (★ 板块锁死期% 真实数据)
    sector_closes = None
    if sector_map and ts_code in sector_map:
        sec_data = sector_map[ts_code]
        if sec_data:
            sector_closes = np.zeros(len(rows))
            for i, r in enumerate(rows):
                sector_closes[i] = sec_data.get(r[0], 0.0)
            if np.all(sector_closes == 0): sector_closes = None

    wave_feats = compute_wave_features(closes, opens_z, highs, lows, volumes,
                                       idx_closes, sector_closes, tg_score=tg_score)
    if wave_feats is None: return None
    sxqs = compute_sxqs_features(closes, highs, lows)
    sxqs_list = [
        sxqs["h1h2_up"], sxqs["h3_dev"], sxqs["var6"], sxqs["var7"], sxqs["net_power"],
        sxqs["var8"], sxqs["a_signal"], sxqs["near_low"],
        sxqs["d_signal"], sxqs["w_signal"],
    ]
    feats = wave_feats + sxqs_list
    return np.array([feats], dtype=np.float32)
async def daily_scan(scan_date: date = None, progress_callback=None,
                     restrict_symbols: list[str] | None = None) -> dict:
    """每日扫描: 对所有股票跑 XGBoost, 更新候选池.

    Args:
        restrict_symbols: 如果提供, 仅扫描这些股票 (用于两期策略: 先扫池内, 后全市场)

    Returns:
        {new_entries, total_pool, tier_counts, top_picks}
    """
    if scan_date is None:
        scan_date = date.today()

    model = await _load_xgb_model()
    if model is None:
        return {"status": "error", "reason": "Model not found"}

    await create_pool_tables()

    # ── 0. 加载指数数据 (上证+创业板) ──
    idx_000001 = None; idx_399006 = None
    try:
        async with async_session_factory() as s:
            r = await s.execute(text(
                "SELECT trade_date, close FROM daily_kline WHERE ts_code = '700001.TI' ORDER BY trade_date"
            ))
            idx_000001 = {row[0]: float(row[1] or 0) for row in r.fetchall()}
            r2 = await s.execute(text(
                "SELECT trade_date, close FROM daily_kline WHERE ts_code = '399006.SZ' ORDER BY trade_date"
            ))
            idx_399006 = {row[0]: float(row[1] or 0) for row in r2.fetchall()}
        logger.info(f"Loaded index data: 000001={len(idx_000001)}d, 399006={len(idx_399006)}d")
    except Exception:
        pass

    # ── 0.5 预加载板块指数数据 (★ 板块锁死期% 真实数据) ──
    sector_map: dict[str, dict] = {}  # ts_code → {date → close}
    try:
        async with async_session_factory() as s:
            # 加载 SW 一级行业指数
            r_sec = await s.execute(text("""
                SELECT index_code, trade_date, close FROM sw_sector_index
                WHERE index_code LIKE '801___ SI' AND trade_date >= :cutoff
                ORDER BY index_code, trade_date
            """), {"cutoff": scan_date - timedelta(days=300)})
            sec_data = r_sec.fetchall()
        # 组织为 {index_code: {date: close}}
        sec_index_map: dict[str, dict] = {}
        for row in sec_data:
            code, td, cl = row[0], row[1], float(row[2] or 0)
            if code not in sec_index_map:
                sec_index_map[code] = {}
            sec_index_map[code][td] = cl
        logger.info(f"Loaded {len(sec_index_map)} sector indices for sector feature")
    except Exception as e:
        logger.warning(f"Sector index loading failed: {e}")
        sec_index_map = {}

    # ── 1. 获取股票列表 ──
    _INDEX_PREFIXES = ("000300.SH", "000016.SH", "000905.SH", "000852.SH", "000001.SH",
                       "000688.SH", "399001.SZ", "399006.SZ", "399005.SZ")
    if restrict_symbols:
        all_stocks = [s for s in restrict_symbols
                      if not s.endswith(".SI") and s not in _INDEX_PREFIXES]
        logger.info(f"Daily scan (restricted): {len(all_stocks)} stocks")
    else:
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT DISTINCT ts_code FROM daily_kline
                WHERE trade_date >= :cutoff AND trade_date <= :d
            """), {"cutoff": scan_date - timedelta(days=5), "d": scan_date})
            all_stocks = [row[0] for row in r.fetchall()]
        # ★ 过滤指数
        all_stocks = [s for s in all_stocks if not s.endswith(".SI") and s not in _INDEX_PREFIXES]
        logger.info(f"Daily scan (full): {len(all_stocks)} stocks")
    if progress_callback:
        await progress_callback("load", 0, 0, f"已加载 {len(all_stocks)} 只股票, 开始锁死检测...")

    # ── 2. 先锁死后XGBoost ──
    lock_map = {}
    try:
        from app.services.lock_detector import detect_lock_simple
        scanned = 0
        for ts_code in all_stocks:
            try:
                async with async_session_factory() as s:
                    r_f = await s.execute(text(
                        "SELECT close,high,low,amount FROM daily_kline WHERE ts_code=:c ORDER BY trade_date DESC LIMIT 50"
                    ), {"c": ts_code})
                    rows_f = list(reversed(r_f.fetchall()))
                if len(rows_f) < 30: continue
                avg_price = float(np.mean([float(rw[0] or 0) for rw in rows_f[-10:]]))
                avg_amount = float(np.mean([float(rw[3] or 0) for rw in rows_f[-10:]]))
                if avg_price < MIN_PRICE or avg_amount < MIN_DAILY_AMOUNT:
                    continue
                cs_l = np.array([float(rw[0] or 0) for rw in rows_f])
                hs_l = np.array([float(rw[1] or cs_l[i]) for i, rw in enumerate(rows_f)])
                ls_l = np.array([float(rw[2] or cs_l[i]) for i, rw in enumerate(rows_f)])
                # 不传大盘指数, 纯振幅判断
                lr = detect_lock_simple(cs_l, hs_l, ls_l, None)
                if lr["in_lock"]:
                    lock_map[ts_code] = lr
                scanned += 1
                if scanned % 500 == 0:
                    logger.info(f"  Lock scan: {scanned}/{len(all_stocks)}, in_lock={len(lock_map)}")
                    if progress_callback:
                        await progress_callback("lock", scanned, len(all_stocks),
                            f"锁死检测 {scanned}/{len(all_stocks)} ({len(lock_map)}只锁死)")
            except Exception:
                pass
        logger.info(f"  Lock scan done: {len(lock_map)}/{len(all_stocks)} in lock")
        if progress_callback:
            await progress_callback("lock", len(all_stocks), len(all_stocks),
                f"锁死检测完成: {len(lock_map)}只 ({len(lock_map)/max(len(all_stocks),1)*100:.1f}%)")
    except Exception as e:
        logger.warning(f"Lock scan failed: {e}")

    # ── 2.5 历史浪质量评估 (环节二: A-G 过滤) ──
    history_label_map: dict[str, dict] = {}
    try:
        from app.services.alphaflow_evaluator import evaluate_history
        hist_checked = 0
        for ts_code in list(lock_map.keys()):
            try:
                async with async_session_factory() as s:
                    r = await s.execute(text("""
                        SELECT trade_date, close, high, low FROM daily_kline
                        WHERE ts_code = :c ORDER BY trade_date
                    """), {"c": ts_code})
                    all_rows = [(row[0], float(row[1] or 0), float(row[2] or 0), float(row[3] or 0))
                               for row in r.fetchall()]
                if len(all_rows) < 200: continue
                all_c = np.array([r_[1] for r_ in all_rows])
                all_h = np.array([r_[2] for r_ in all_rows])
                all_l = np.array([r_[3] for r_ in all_rows])
                nn = len(all_c)
                lock_cycles = []; i_ = 0
                while i_ < nn - 20:
                    w20_l = float(np.min(all_l[i_:i_+20])); w20_h = float(np.max(all_h[i_:i_+20]))
                    if w20_l <= 0: i_ += 1; continue
                    if (w20_h - w20_l) / w20_l * 100 <= 15.0:
                        start, lh, ll = i_, w20_h, w20_l
                        while i_ < nn - 1:
                            if i_ + 10 > nn: break
                            lh = max(lh, float(all_h[i_])); ll = min(ll, float(all_l[i_]))
                            seg_len = i_ - start + 1
                            if seg_len <= 20:
                                if (lh - ll) / max(ll, 0.01) * 100 > 15.0: break
                            else:
                                if (lh - ll) / max(ll, 0.01) * 100 > 17.0: break
                            i_ += 1
                        end = i_ - 1
                        if end - start >= 20:
                            lock_cycles.append({"start": start, "end": end, "days": end - start + 1,
                                "high": round(float(lh), 2), "low": round(float(ll), 2)})
                        i_ = end + 1
                    else: i_ += 1
                if len(lock_cycles) >= 3:
                    hist = await evaluate_history(ts_code, lock_cycles)
                    # 老兵保护: 周期≥4 的股票, 除权截断后历史评价可能偏低, 放宽
                    if hist["history_label"] == "fatal" and len(lock_cycles) >= 4:
                        valid_waves = hist.get("valid_waves", 0)
                        if valid_waves >= 1:
                            hist["history_label"] = "moderate"
                            hist["fatal_tags"] = []
                    history_label_map[ts_code] = hist
                    hist_checked += 1
                    if hist_checked % 80 == 0:
                        f = sum(1 for h in history_label_map.values() if h["history_label"] == "fatal")
                        logger.info(f"  History eval: {hist_checked}, fatal={f}")
            except Exception: pass
        if history_label_map:
            fatal_n = sum(1 for h in history_label_map.values() if h["history_label"] == "fatal")
            strong_n = sum(1 for h in history_label_map.values() if h["history_label"] == "strong")
            logger.info(f"  History eval done: {len(history_label_map)} checked, {strong_n} strong, {fatal_n} fatal excluded")
    except Exception as e:
        logger.warning(f"History eval failed: {e}")

    # ── 3. XGBoost: 跳过致命历史股 ──
    new_probs = {}
    lock_codes = list(lock_map.keys())

    # ★ v4.9: 已入池股票即使已突破 (state=breakout_up), 也跑 XGBoost 维持池内评分
    pool_stash: dict[str, dict] = {}
    if restrict_symbols:
        try:
            async with async_session_factory() as s:
                r = await s.execute(text(
                    "SELECT ts_code, current_prob, COALESCE(micro_score,0), tier FROM alphaflow_pool"
                ))
                pool_stash = {}
                for row in r.fetchall():
                    ts, prob, micro, tier = row[0], float(row[1] or 0), row[2] or 0, row[3]
                    if ts not in lock_codes:
                        pool_stash[ts] = {"prob": prob, "micro": micro, "tier": tier}
        except Exception:
            pool_stash = {}
    if pool_stash:
        logger.info(f"  Pool-restricted scan: adding {len(pool_stash)} non-locked pool stocks for XGBoost")

    # ★ V3: 预加载 TG 管线 composite_score 作为第48维反哺特征
    tg_score_map: dict[str, float] = {}
    all_feat_codes = lock_codes + list(pool_stash.keys())
    if all_feat_codes:
        try:
            async with async_session_factory() as s:
                r = await s.execute(text("""
                    SELECT DISTINCT ON (symbol) symbol, composite_score
                    FROM analysis_scores
                    WHERE symbol = ANY(:codes) AND scan_date >= :cut
                    ORDER BY symbol, scan_date DESC
                """), {"codes": lock_codes, "cut": scan_date - timedelta(days=10)})
                tg_score_map = {row[0]: float(row[1] or 0) for row in r.fetchall()}
            logger.info(f"  TG score preloaded: {len(tg_score_map)}/{len(lock_codes)} stocks")
        except Exception as e:
            logger.debug(f"TG score preload failed: {e}")

    # ★ 预计算 stock→sector 映射 (用于板块锁死期%特征)
    # v4.9: 用 Tushare stock_basic.industry 替代 ths_member.ths_name
    stock_sector_map: dict[str, dict] = {}
    if sec_index_map and all_feat_codes:
        try:
            from app.services.tushare_common import call_tushare as _cts
            ind_map: dict[str, str] = {}
            # 分批拉取 Tushare industry
            for batch_start in range(0, len(all_feat_codes), 500):
                batch = all_feat_codes[batch_start:batch_start+500]
                rows = await _cts("stock_basic", {"ts_code": ",".join(batch)}, "ts_code,industry")
                for r in (rows or []):
                    code = r.get("ts_code", "")
                    if code and r.get("industry"):
                        ind_map[code] = r["industry"]
            # SW一级行业 → 指数代码映射 (28个一级行业)
            SW_L1_MAP = {
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
            for ts_code in all_feat_codes:
                ind_name = ind_map.get(ts_code, "")
                for l1_name, idx_code in SW_L1_MAP.items():
                    if l1_name in (ind_name or ""):
                        if idx_code in sec_index_map:
                            stock_sector_map[ts_code] = sec_index_map[idx_code]
                        break
            logger.info(f"  Sector mapping: {len(stock_sector_map)}/{len(all_feat_codes)} stocks matched (via stock_basic.industry)")
        except Exception as e:
            logger.warning(f"Stock-sector mapping failed: {e}")
    logger.info(f"XGBoost scoring: {len(lock_codes)} lock + {len(pool_stash)} pool-stash")

    # 合并评分队列: lock_codes + pool_stash
    score_queue = list(lock_codes)
    for ts in pool_stash:
        if ts not in score_queue:
            score_queue.append(ts)

    if progress_callback:
        await progress_callback("xgb", 0, len(score_queue), f"XGBoost评分中 ({len(score_queue)}只)...")
    for idx, ts_code in enumerate(score_queue):
        try:
            idx_map_for_stock = idx_399006 if (ts_code.startswith('300') or ts_code.startswith('301') or ts_code.startswith('688')) else idx_000001
            feats = await _extract_features_for_stock(ts_code, scan_date, idx_map_for_stock, stock_sector_map,
                                                       tg_score=tg_score_map.get(ts_code))
            if feats is not None:
                prob = float(model.predict_proba(feats)[0, 1])
                new_probs[ts_code] = prob
        except Exception:
            pass
        if (idx + 1) % 100 == 0:
            logger.info(f"  XGBoost: {idx+1}/{len(score_queue)}")
            if progress_callback:
                await progress_callback("xgb", idx+1, len(score_queue),
                    f"XGBoost {idx+1}/{len(score_queue)} (已评分{len(new_probs)}只)")

    logger.info(f"  XGBoost done: {len(new_probs)} scored")
    if progress_callback:
        await progress_callback("xgb", len(score_queue), len(score_queue),
            f"XGBoost完成: {len(new_probs)}只有效评分")

    # ── 3.5 策略分类 (环节三+四: 锁质量 + 策略归类) ──
    strategy_map: dict[str, dict] = {}
    try:
        from app.services.alphaflow_evaluator import evaluate_current_lock, classify_strategy

        # 获取市场环境
        market_risk = "normal"
        try:
            from app.services.market_gate import get_market_state
            ms = await get_market_state()
            market_risk = ms.get("risk", "normal")
        except Exception:
            pass

        for code, prob in new_probs.items():
            lr = lock_map.get(code, {})
            # 池内补入股票 (pool_stash): 历史评估从 history_label_map 取，无则给 moderate
            hist = history_label_map.get(code, {"history_label": "none"})
            if hist["history_label"] == "none":
                if code in pool_stash:
                    hist["history_label"] = "moderate"  # 已入池至少说明历史不差

            # 环节三: 当前锁质量
            try:
                qual = await evaluate_current_lock(code, lr)
            except Exception:
                qual = {"quality_label": "unknown"}

            qual_label = qual.get("quality_label", "unknown")

            # ★ 老兵直通: 若在锁但历史评估没覆盖, 给兜底
            if hist["history_label"] == "none" and lr.get("lock_days", 0) >= 20:
                hist["history_label"] = "moderate"  # 至少锁得够久

            strategy = classify_strategy(hist["history_label"], qual_label, market_risk)
            if not strategy["display"]:
                continue

            strategy_map[code] = {
                "strategy": strategy["strategy"],
                "group": strategy["group"],
                "prob_label": strategy["label"],
                "priority": strategy["priority"],
                "history_label": hist["history_label"],
                "quality_label": qual_label,
                "lock_days": lr.get("lock_days", 0),
            }
            # 历史强/中的低分股给最低入池概率
            if prob < ENTRY_THRESHOLD and hist["history_label"] in ("strong", "moderate"):
                new_probs[code] = max(prob, 0.40)

        if strategy_map:
            groups = set(s["group"] for s in strategy_map.values())
            logger.info(f"  Strategy: {len(strategy_map)} classified, groups={groups}")
            if progress_callback:
                await progress_callback("strategy", len(strategy_map), len(lock_codes),
                    f"策略分类完成: {len(strategy_map)}只")
    except Exception as e:
        import traceback
        logger.warning(f"Strategy classification failed: {e}\n{traceback.format_exc()}")

    # ── 3. 获取已有池成员 ──
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT ts_code, tier, consecutive_dormant, current_prob, COALESCE(micro_score,0) FROM alphaflow_pool"))
        pool_members = {row[0]: {"tier": row[1], "dormant": row[2] or 0, "old_prob": float(row[3] or 0),
                                   "micro_score": row[4] or 0}
                       for row in r.fetchall()}

    # ── 4. 更新池 ──
    new_entries = 0
    updates = 0
    today_tiers = {"active": 0, "observe": 0, "dormant": 0}
    top_picks = []

    async with async_session_factory() as s:
        for ts_code, prob in new_probs.items():
            strat = strategy_map.get(ts_code)
            if not strat:
                continue

            existing = pool_members.get(ts_code)
            prob_trend = prob - existing["old_prob"] if existing else 0.0
            days_in_pool = 1

            # 策略编码: -(priority * 100 + lock_days)
            strategy_code = -(strat["priority"] * 100 + strat["lock_days"])

            if existing:
                r_days = await s.execute(text(
                    "SELECT days_in_pool FROM alphaflow_pool WHERE ts_code = :c"
                ), {"c": ts_code})
                row = r_days.fetchone()
                days_in_pool = (row[0] + 1) if row and row[0] else 1

                consecutive_dormant = existing["dormant"]
                if prob < DORMANT_THRESHOLD:
                    consecutive_dormant += 1
                else:
                    consecutive_dormant = max(0, consecutive_dormant - 1)

                tier = existing["tier"]
                if tier == "dormant":
                    if prob >= REVIVE_THRESHOLD: tier = "observe"
                elif tier == "observe":
                    if consecutive_dormant >= 5: tier = "dormant"
                    elif prob >= ACTIVE_THRESHOLD: tier = "active"
                elif tier == "active":
                    if consecutive_dormant >= 5: tier = "dormant"
                    elif prob < ACTIVE_THRESHOLD - 0.1: tier = "observe"

                await s.execute(text("""
                    UPDATE alphaflow_pool SET
                        last_updated = :d, current_prob = :p, prob_trend = :t,
                        tier = :tier, tier_since = COALESCE(tier_since, :d),
                        days_in_pool = :dp, consecutive_dormant = :cd,
                        micro_score = :ms,
                        strategy_group = COALESCE(:sg, strategy_group),
                        strategy_label = COALESCE(:sl, strategy_label)
                    WHERE ts_code = :c
                """), {"d": scan_date, "p": round(prob, 4), "t": round(prob_trend, 4),
                       "tier": tier, "dp": days_in_pool, "cd": consecutive_dormant,
                       "ms": strat["lock_days"],
                       "sg": strat.get("group", ""), "sl": strat.get("prob_label", ""),
                       "c": ts_code})
                if tier != existing["tier"]:
                    await s.execute(text("""
                        UPDATE alphaflow_pool SET tier_since = :d WHERE ts_code = :c
                    """), {"d": scan_date, "c": ts_code})
                updates += 1
            else:
                # ★ 新入池: 对蛋期股票运行孵化分类器 (低概率降级)
                init_tier = "observe"
                try:
                    gain = strat.get("gain_from_first_lock", 0)
                    if gain < 50:  # 蛋期
                        import os, joblib
                        clf_path = os.path.join(os.path.dirname(__file__), "..", "..",
                                                "models", "mins_egg_classifier.joblib")
                        if os.path.exists(clf_path):
                            artifact = joblib.load(clf_path)
                            from app.services.alphaflow_features import compute_wave_features
                            # 用当前K线数据提取8维分钟特征 (从wave特征近似)
                            if wave_feats and len(wave_feats) >= 38:
                                # 近似: 用日线特征作为分钟特征的代理
                                proxy = [wave_feats[i] for i in [2, 4, 6, 8, 14, 20, 16, 25]]
                                import numpy as np
                                X = artifact["scaler"].transform([proxy])
                                hatch_prob = float(artifact["model"].predict_proba(X)[0, 1])
                                if hatch_prob < 0.3:
                                    init_tier = "dormant"
                                    logger.debug(f"  {ts_code}: 蛋期孵化概率低({hatch_prob:.2f}), 降级为dormant")
                except Exception:
                    pass  # 分类器不可用时默认 observe

                await s.execute(text("""
                    INSERT INTO alphaflow_pool
                    (ts_code, first_seen, last_updated, current_prob, prob_trend,
                     tier, tier_since, days_in_pool, micro_score, strategy_group, strategy_label)
                    VALUES (:c, :d, :d, :p, 0, :tier, :d, 1, :ms, :sg, :sl)
                    ON CONFLICT (ts_code) DO UPDATE SET
                        last_updated = :d, current_prob = :p, micro_score = :ms,
                        strategy_group = COALESCE(EXCLUDED.strategy_group, alphaflow_pool.strategy_group),
                        strategy_label = EXCLUDED.strategy_label
                """), {"c": ts_code, "d": scan_date, "p": round(prob, 4),
                       "tier": init_tier,
                       "ms": strat["lock_days"],
                       "sg": strat.get("group", ""),
                       "sl": strat.get("prob_label", "")})
                tier = init_tier
                consecutive_dormant = 0
                new_entries += 1

            # 保存历史
            await s.execute(text("""
                INSERT INTO alphaflow_pool_history (ts_code, record_date, xgb_prob, tier)
                VALUES (:c, :d, :p, :t)
                ON CONFLICT (ts_code, record_date) DO UPDATE SET
                    xgb_prob = :p, tier = :t
            """), {"c": ts_code, "d": scan_date, "p": round(prob, 4), "t": tier})

            if tier == "active" and prob >= 0.70:
                top_picks.append({"ts_code": ts_code, "prob": round(prob, 3),
                                  "trend": round(prob_trend, 3)})
            today_tiers[tier] = today_tiers.get(tier, 0) + 1

        await s.commit()

    # ── 老兵兜底: 锁死中+老兵预破的股票无论如何都要入池 ──
    veteran_backstop = 0
    try:
        from app.services.alphaflow_veteran import detect_veteran as _dv
        for code, lr in lock_map.items():
            if not lr.get("in_lock"): continue
            if code in strategy_map: continue
            # 快速老兵检测 (已有锁死数据)
            vet = await _dv(code)
            if vet and vet.get("level") in ("pre_breakout", "late_stage"):
                prob = max(new_probs.get(code, 0), 0.40)
                strategy_code = -(vet.get("score", 50) + vet["current_days"])
                async with async_session_factory() as s_v:
                    await s_v.execute(text("""
                        INSERT INTO alphaflow_pool
                        (ts_code, first_seen, last_updated, current_prob, prob_trend,
                         tier, tier_since, days_in_pool, micro_score, strategy_group, strategy_label)
                        VALUES (:c, :d, :d, :p, 0, 'observe', :d, 1, :ms, :sg, :sl)
                        ON CONFLICT (ts_code) DO UPDATE SET
                            last_updated = :d, current_prob = :p,
                            strategy_group = COALESCE(EXCLUDED.strategy_group, alphaflow_pool.strategy_group),
                            strategy_label = COALESCE(EXCLUDED.strategy_label, alphaflow_pool.strategy_label)
                    """), {"c": code, "d": scan_date, "p": round(prob, 4),
                           "ms": vet["current_days"],
                           "sg": "老兵兜底", "sl": vet["verdict"][:100]})
                    await s_v.commit()
                veteran_backstop += 1
                logger.info(f"  Veteran backstop: {code} ({vet['level']}, {vet['total_cycles']}c, {vet['current_days']}d)")
        if veteran_backstop:
            logger.info(f"  Veteran backstop: {veteran_backstop} force-added to pool")
    except Exception as e:
        logger.warning(f"Veteran backstop failed: {e}")

    total = sum(today_tiers.values())

    # ── 结构清理: 崩塌/破位 → 踢出 (老兵豁免) ──
    removed = 0
    try:
        from app.services.structure_break_detector import detect_trend_break
        async with async_session_factory() as s:
            r = await s.execute(text("SELECT ts_code, micro_score FROM alphaflow_pool"))
            pool_codes = [(row[0], row[1] or 0) for row in r.fetchall()]
        for code, ms in pool_codes:
            # 老兵 (micro_score < 0) 免于结构清理
            if ms < 0:
                continue
            try:
                sr = await detect_trend_break(code, scan_date)
                if sr.get("status") in ("dead", "broken"):
                    async with async_session_factory() as s:
                        await s.execute(text("DELETE FROM alphaflow_pool WHERE ts_code = :c"), {"c": code})
                        await s.commit()
                    removed += 1
                    if removed <= 5:
                        logger.info(f"Structure cleanup: removed {code} ({sr.get('status')}) — {sr.get('label','')[:60]}")
            except Exception:
                pass
        if removed > 0:
            total -= removed
            logger.info(f"Structure cleanup: removed {removed} dead/broken stocks, pool now {total}")
    except Exception as e:
        logger.warning(f"Structure cleanup skipped: {e}")

    # ── 大雁清理: 30日振幅>15% + 涨幅>100% → 归档 ──
    goosed = 0
    try:
        from app.services.lock_detector import detect_lock_simple
        async with async_session_factory() as s:
            r = await s.execute(text("SELECT ts_code FROM alphaflow_pool"))
            pool_codes2 = [row[0] for row in r.fetchall()]
        # goose_archive table now created by ORM (data_models.py)
        for code in pool_codes2:
            try:
                async with async_session_factory() as s:
                    r_f = await s.execute(text(
                        "SELECT close,high,low FROM daily_kline WHERE ts_code=:c ORDER BY trade_date DESC LIMIT 100"
                    ), {"c": code})
                    rows_f = list(reversed(r_f.fetchall()))
                if len(rows_f) < 30: continue
                cs = np.array([float(rw[0] or 0) for rw in rows_f])
                hs = np.array([float(rw[1] or cs[i]) for i, rw in enumerate(rows_f)])
                ls = np.array([float(rw[2] or cs[i]) for i, rw in enumerate(rows_f)])

                # 加载对应大盘指数
                idx_code = '399006.SZ' if (code.startswith('300') or code.startswith('301') or code.startswith('688')) else '000001.SH'
                async with async_session_factory() as s2:
                    r_idx = await s2.execute(text(
                        "SELECT close FROM daily_kline WHERE ts_code=:c ORDER BY trade_date DESC LIMIT 100"
                    ), {"c": idx_code})
                    idx_rows = list(reversed(r_idx.fetchall()))
                idx_cs = np.array([float(rw[0] or 0) for rw in idx_rows]) if idx_rows else None

                lock = detect_lock_simple(cs, hs, ls, idx_cs)

                # 计算总涨幅
                all_min = float(np.min(ls))
                gain_pct = (cs[-1] - all_min) / all_min * 100 if all_min > 0 else 0

                if not lock["in_lock"] and gain_pct > 100:
                    from app.models.data_models import GooseArchive
                    async with async_session_factory() as s3:
                        goose = GooseArchive(
                            ts_code=code,
                            first_seen=date.today(),
                            gain_from_first_lock=round(gain_pct, 1),
                            first_lock_avg=round(float(np.mean(cs)), 1),
                            waves_completed=0,
                        )
                        s3.add(goose)
                        # ON CONFLICT: 手动实现 UPSERT
                        existing = await s3.get(GooseArchive, code)
                        if existing:
                            existing.gain_from_first_lock = round(gain_pct, 1)
                            existing.first_lock_avg = round(float(np.mean(cs)), 1)
                            existing.waves_completed = 0
                        else:
                            s3.add(goose)
                        await s3.execute(text("DELETE FROM alphaflow_pool WHERE ts_code=:c"), {"c": code})
                        await s3.commit()
                    goosed += 1
                    if goosed <= 10:
                        logger.info(f"Goose: {code} amp={lock['amplitude_30d']:.0f}% gain={gain_pct:.0f}% → archived")
                    continue

                # 在锁死状态 → 更新锁死信息到池子
                if lock["in_lock"]:
                    async with async_session_factory() as s3:
                        await s3.execute(text("""
                            UPDATE alphaflow_pool SET micro_score = :ms WHERE ts_code = :c
                        """), {"c": code, "ms": lock["lock_days"]})
                        await s3.commit()
            except Exception:
                pass
        if goosed > 0:
            total -= goosed
            logger.info(f"Goose cleanup: {goosed} archived, pool now {total}")
            if progress_callback:
                await progress_callback("goose", goosed, total, f"清理{goosed}只鹅, 池中{total}只")
    except Exception as e:
        logger.warning(f"Goose cleanup skipped: {e}")

    logger.info(f"Pool update: {new_entries} new, {updates} updated, {goosed} goosed, "
                f"total {total} ({today_tiers['active']} active, "
                f"{today_tiers['observe']} observe, {today_tiers['dormant']} dormant)")
    if progress_callback:
        await progress_callback("done", total, total,
            f"完成: {new_entries}新增 {updates}更新 池中{total}只")

    return {
        "status": "success",
        "scan_date": str(scan_date),
        "scored_stocks": len(new_probs),
        "new_entries": new_entries,
        "total_pool": total,
        "tiers": today_tiers,
        "top_picks": sorted(top_picks, key=lambda x: -x["prob"])[:20],
    }
