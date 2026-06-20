"""DNA 训练数据生成器 — 从 daily_kline + min_kline 生成训练样本.

对每只目标股票, 逐日构建 ~150 维特征 + 多窗口标签, 写入 daily_samples 表.
完全不依赖 signal_history/analysis_scores/scan_results。

核心入口: build_dna_data()
"""
import asyncio
import logging
import numpy as np
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory
from app.core.market_data import get_benchmark_closes, compute_excess_return

from .features import (
    FEAT_NAMES_77, EMOTION_FEAT_NAMES, MARKET_FEAT_NAMES,
    TRANSITION_FEAT_NAMES, CYCLE_FEAT_NAMES, HISTORY_FEAT_NAMES,
    INTERACT_FEAT_NAMES, ALL_FEAT_NAMES,
    compute_daily_features_77, features_to_array, check_feature_quality,
)
from .emotion import (
    extract_emotion_vector, cluster_emotions, build_transition_matrix,
    stationary_distribution, extract_transition_features, name_emotions,
)
from .cycle import detect_lockup, find_cycles, cycle_statistics, current_cycle_position, extract_cycle_features
from .market_context import extract_market_emotion, extract_linkage_features

logger = logging.getLogger("stock_dna.data_builder")


async def build_dna_data(
    symbols: list[str] = None,
    start_date: str = "2022-01-01",
    progress_cb=None,
) -> dict:
    """对指定股票列表生成 DNA 训练样本。

    Steps:
      1. 加载每只股票的 daily_kline + min_kline
      2. 逐日计算特征 + 标签
      3. 聚类表情
      4. 计算转移矩阵 + 周期统计
      5. 写入 stock_dna.daily_samples

    Args:
        symbols: 目标股票列表, None = 持仓 + 最近推荐
        start_date: 起始日期
        progress_cb: 进度回调 (phase, current, total, message)

    Returns:
        {status, total_samples, symbols_processed, errors}
    """
    # ── 确定目标股票 ──
    if symbols is None:
        symbols = await _get_target_symbols()
    if not symbols:
        return {"status": "error", "detail": "无目标股票"}

    logger.info(f"开始生成 DNA 数据: {len(symbols)} 只股票")
    # 预加载基准指数 (避免热循环中重复查询)
    benchmark_closes = await get_benchmark_closes()
    total_samples = 0
    errors = []

    for idx, sym in enumerate(symbols):
        try:
            if progress_cb:
                progress_cb("dna_data", idx + 1, len(symbols), f"处理 {sym} ({idx+1}/{len(symbols)})")

            # ── Step 1: 加载K线 ──
            kline_rows = await _load_daily_kline(sym, start_date)
            if len(kline_rows) < 100:
                errors.append(f"{sym}: K线不足 ({len(kline_rows)}行)")
                continue

            min_bars_by_date = await _load_min_kline(sym, start_date)
            market_bars_by_date = await _load_market_min_kline(start_date)

            # ── Step 2: 逐日计算特征 + 标签 ──
            daily_features_list = []
            emotion_vectors_raw = []
            valid_indices = []

            for i in range(60, len(kline_rows)):  # 前 60 天作为 warmup
                row = kline_rows[i]
                td = row["trade_date"]
                if isinstance(td, str):
                    td = date.fromisoformat(td) if '-' in td else date(int(td[:4]), int(td[4:6]), int(td[6:8]))

                # 日线 77 维
                feat77 = compute_daily_features_77(kline_rows, i)

                # 表情向量 (优先分时数据, 无则日线伪表情降级)
                stock_bars = min_bars_by_date.get(td) or min_bars_by_date.get(str(td))
                mkt_bars = market_bars_by_date.get(td) or market_bars_by_date.get(str(td))
                ev = None
                if stock_bars:
                    ev = extract_emotion_vector(stock_bars, mkt_bars)
                if ev and any(abs(float(ev.get(k, 0) or 0)) > 1e-9 for k in EMOTION_FEAT_NAMES):
                    ev["day_ret"] = feat77.get("chg_1d", 0.0)
                    emotion_vectors_raw.append(ev)
                    valid_indices.append(i)
                else:
                    # 降级: 日线伪表情
                    from .emotion import pseudo_emotion_from_daily
                    pe = pseudo_emotion_from_daily(kline_rows, i)
                    pe["day_ret"] = feat77.get("chg_1d", 0.0)
                    emotion_vectors_raw.append(pe)
                    valid_indices.append(i)

                # 市场情绪
                if mkt_bars:
                    mkt_feat = extract_market_emotion(mkt_bars) or {}
                else:
                    mkt_feat = {}

                # 联动特征
                if stock_bars and mkt_bars:
                    link_feat = extract_linkage_features(stock_bars, mkt_bars)
                else:
                    link_feat = {}

                # 标签: 超额收益 (vs 700001.TI)
                labels = await _compute_labels(kline_rows, i, row, td, benchmark_closes)

                # 组装
                sample_feat = {}
                sample_feat.update(feat77)
                sample_feat.update({k: emotion_vectors_raw[-1].get(k, 0.0) for k in EMOTION_FEAT_NAMES})
                sample_feat.update(mkt_feat)
                sample_feat.update(link_feat)
                sample_feat.update({"excess_ret_t2": labels["t2"], "excess_ret_t5": labels["t5"],
                                    "excess_ret_t10": labels["t10"], "excess_ret_t20": labels["t20"]})
                sample_feat["symbol"] = sym
                sample_feat["trade_date"] = td

                daily_features_list.append(sample_feat)

            # ── Step 3: 表情聚类 ──
            emotion_labels, n_emotions, cluster_info = cluster_emotions(emotion_vectors_raw, k_range=(5, 8))

            # ── Step 4: 周期扫描 ──
            cycles = find_cycles(kline_rows)
            cycle_stats = cycle_statistics(cycles)

            # ── Step 5: 转移矩阵 ──
            if n_emotions > 1:
                P = build_transition_matrix(emotion_labels, n_emotions)
                pi = stationary_distribution(P)
            else:
                P = np.array([[1.0]])
                pi = np.array([1.0])

            # ── Step 6: 写入 daily_samples ──
            n_written = 0
            for j, (frow, emo_idx) in enumerate(zip(daily_features_list, range(len(daily_features_list)))):
                if j < len(emotion_labels):
                    emo_label = int(emotion_labels[j])
                else:
                    emo_label = 0

                # 转移特征
                tr_feat = extract_transition_features(P, pi, emo_label)

                # 周期特征
                cy_feat = extract_cycle_features(kline_rows, cycle_stats)

                # 历史 DNA (聚合统计)
                hist_feat = _compute_history_features(daily_features_list, j)

                # 交互特征
                ix_feat = {}
                cy_pos = cy_feat.get("cy_position_pct", 0)
                ix_feat["ix_lockup_emotion_cross_0"] = cy_pos * (1 if emo_label == 0 else 0)
                ix_feat["ix_lockup_emotion_cross_1"] = cy_pos * (1 if emo_label == 1 else 0)
                ix_feat["ix_lockup_emotion_cross_2"] = cy_pos * (1 if emo_label == 2 else 0)
                ix_feat["ix_lockup_emotion_cross_3"] = cy_pos * (1 if emo_label == 3 else 0)
                ix_feat["ix_breakout_emotion_cross_0"] = (1 - cy_pos) * (1 if emo_label == 0 else 0)
                ix_feat["ix_breakout_emotion_cross_1"] = (1 - cy_pos) * (1 if emo_label == 1 else 0)
                ix_feat["ix_breakout_emotion_cross_2"] = (1 - cy_pos) * (1 if emo_label == 2 else 0)
                ix_feat["ix_breakout_emotion_cross_3"] = (1 - cy_pos) * (1 if emo_label == 3 else 0)

                # 全特征合并
                full_feat = {}
                full_feat.update(frow)
                full_feat.update(tr_feat)
                full_feat.update(cy_feat)
                full_feat.update(hist_feat)
                full_feat.update(ix_feat)

                await _upsert_daily_sample(
                    sym, frow["trade_date"],
                    emotion_label=emo_label,
                    emotion_features=emotion_vectors_raw[emo_idx] if emo_idx < len(emotion_vectors_raw) else {},
                    cycle_phase=cy_feat.get("cy_is_locked", 0) and "lockup" or (cy_feat.get("cy_position_pct", 0) > 1.5 and "breakout" or "normal"),
                    cycle_day=int(cy_feat.get("cy_lockup_day", 0)),
                    lead_lag_min=link_feat.get("mkt_lead_lag", 0),
                    independent_pct=link_feat.get("mkt_independent_ratio", 0),
                    amplify_ratio=link_feat.get("mkt_amplify_ratio", 0),
                    excess_ret_t2=frow.get("excess_ret_t2", 0),
                    excess_ret_t5=frow.get("excess_ret_t5", 0),
                    excess_ret_t10=frow.get("excess_ret_t10", 0),
                    excess_ret_t20=frow.get("excess_ret_t20", 0),
                    daily_features={k: frow.get(k, 0) for k in FEAT_NAMES_77},
                )
                n_written += 1

            # ── Step 7: 写入 DNA 档案 ──
            # 计算每种表情的平均超额收益 (best_emotion_ret)
            emotion_ret_map: dict[str, float] = {}
            best_emotion_id = 0
            best_avg_ret = -999.0
            for eid in range(n_emotions):
                eid_str = str(eid)
                e_returns = [frow.get("excess_ret_t5", 0) or 0
                            for j, frow in enumerate(daily_features_list)
                            if j < len(emotion_labels) and int(emotion_labels[j]) == eid]
                if e_returns:
                    avg_r = round(float(np.mean(e_returns)), 2)
                    emotion_ret_map[eid_str] = avg_r
                    if avg_r > best_avg_ret:
                        best_avg_ret = avg_r
                        best_emotion_id = eid

            # 表情命名
            label_to_ret = {i: emotion_ret_map.get(str(i), 0.0) for i in range(n_emotions)}
            emotion_names = name_emotions(emotion_labels, emotion_vectors_raw, n_emotions, label_to_ret)

            dna_profile = {
                "symbol": sym,
                "n_emotions": n_emotions,
                "emotion_names": emotion_names,
                "transition_matrix": P.tolist(),
                "stationary_dist": pi.tolist(),
                "emotion_entropy": float(-sum(pi[i] * sum(P[i, j] * np.log2(max(P[i, j], 1e-9)) for j in range(n_emotions)) for i in range(n_emotions))),
                "best_emotion": best_emotion_id,
                "best_emotion_ret": emotion_ret_map,
                "avg_lockup_days": cycle_stats.get("avg_lockup_days", 0),
                "std_lockup_days": cycle_stats.get("std_lockup_days", 0),
                "cycle_cv": cycle_stats.get("cycle_cv", 999),
                "avg_breakout_return": cycle_stats.get("avg_breakout_return", 0),
                "avg_breakout_days": cycle_stats.get("avg_breakout_days", 0),
                "training_samples": n_written,
                "archetype": "unknown",
            }
            await _upsert_dna_profile(sym, dna_profile)

            total_samples += n_written
            logger.info(f"  {sym}: {n_written} 样本, {n_emotions} 表情, {cycle_stats.get('n_cycles', 0)} 周期")

        except Exception as e:
            logger.error(f"处理 {sym} 失败: {e}", exc_info=True)
            errors.append(f"{sym}: {e}")

    return {
        "status": "success" if len(errors) < len(symbols) * 0.3 else "partial",
        "total_samples": total_samples,
        "symbols_processed": len(symbols) - len(errors),
        "errors": errors[:10],
    }


# ══════════════════════════════════════════════════════════════════════
# 内部工具函数
# ══════════════════════════════════════════════════════════════════════

async def _get_target_symbols() -> list[str]:
    """获取目标股票: 持仓 + 最近推荐."""
    async with async_session_factory() as s:
        syms = set()
        r = await s.execute(text("SELECT DISTINCT symbol FROM holdings"))
        for row in r.fetchall():
            syms.add(row[0])
        r2 = await s.execute(text("SELECT DISTINCT symbol FROM analysis_scores ORDER BY scan_date DESC LIMIT 200"))
        for row in r2.fetchall():
            syms.add(row[0])
    return sorted(syms)


async def _load_daily_kline(symbol: str, start_date_str: str) -> list[dict]:
    """加载该股票的全部日线."""
    from datetime import date as dt_date
    sd = dt_date.fromisoformat(start_date_str) if isinstance(start_date_str, str) else start_date_str
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT trade_date, open, high, low, close, volume FROM daily_kline "
            "WHERE ts_code=:sym AND trade_date >= :sd ORDER BY trade_date"
        ), {"sym": symbol, "sd": sd})
        return [{"trade_date": row[0], "open": float(row[1] or 0), "high": float(row[2] or 0),
                 "low": float(row[3] or 0), "close": float(row[4] or 0), "volume": float(row[5] or 0)}
                for row in r.fetchall() if float(row[4] or 0) > 0]


async def _load_min_kline(symbol: str, start_date_str: str) -> dict:
    """加载该股票的分钟线, 返回 {date: [bars]}."""
    from datetime import date as dt_date
    sd = dt_date.fromisoformat(start_date_str) if isinstance(start_date_str, str) else start_date_str
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT trade_time, open, high, low, close, volume FROM min_kline "
            "WHERE ts_code=:sym AND trade_time >= :sd ORDER BY trade_time"
        ), {"sym": symbol, "sd": sd})
        result = {}
        for row in r.fetchall():
            td = row[0].date() if hasattr(row[0], 'date') else str(row[0])[:10]
            bar = {"time": str(row[0]), "open": float(row[1] or 0), "high": float(row[2] or 0),
                   "low": float(row[3] or 0), "close": float(row[4] or 0), "volume": float(row[5] or 0)}
            result.setdefault(td, []).append(bar)
    return result


async def _load_market_min_kline(start_date_str: str) -> dict:
    """加载大盘分钟线 (000001.SH), 返回 {date: [bars]}."""
    from datetime import date as dt_date
    sd = dt_date.fromisoformat(start_date_str) if isinstance(start_date_str, str) else start_date_str
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT trade_time, open, high, low, close, vol FROM sector_min_kline "
            "WHERE sector_code='000001.SH' AND trade_time >= :sd ORDER BY trade_time"
        ), {"sd": sd})
        result = {}
        for row in r.fetchall():
            td = row[0].date() if hasattr(row[0], 'date') else str(row[0])[:10]
            bar = {"time": str(row[0]), "open": float(row[1] or 0), "high": float(row[2] or 0),
                   "low": float(row[3] or 0), "close": float(row[4] or 0), "volume": float(row[5] or 0)}
            result.setdefault(td, []).append(bar)
    return result


async def _load_index_closes(start_date: str) -> dict:
    """[DEPRECATED] 使用 app.core.market_data.get_benchmark_closes() 替代."""
    return await get_benchmark_closes()


async def _compute_labels(
    kline_rows: list[dict], idx: int, row: dict, td,
    benchmark_closes: dict[date, float] | None = None,
) -> dict:
    """计算 T+N 超额收益 (vs 700001.TI) — v2: 交易日计数, 使用预加载基准."""
    close = float(row["close"])
    result = {"t2": 0.0, "t5": 0.0, "t10": 0.0, "t20": 0.0}

    if benchmark_closes is None:
        benchmark_closes = await get_benchmark_closes()

    for horizon, key in [(2, "t2"), (5, "t5"), (10, "t10"), (20, "t20")]:
        future_i = idx + horizon
        if future_i < len(kline_rows):
            future_c = float(kline_rows[future_i]["close"])
            result[key] = compute_excess_return(
                close, future_c, td, horizon, benchmark_closes
            )

    return result


def _compute_history_features(daily_list: list[dict], current_idx: int) -> dict[str, float]:
    """从已处理的历史数据中计算 15 维统计特征."""
    hf = {k: 0.0 for k in HISTORY_FEAT_NAMES}
    if current_idx < 30:
        return hf

    past = daily_list[:current_idx]
    t2_rets = [d.get("excess_ret_t2", 0) or 0 for d in past if d.get("excess_ret_t2") is not None]
    t5_rets = [d.get("excess_ret_t5", 0) or 0 for d in past if d.get("excess_ret_t5") is not None]
    t10_rets = [d.get("excess_ret_t10", 0) or 0 for d in past if d.get("excess_ret_t10") is not None]
    t20_rets = [d.get("excess_ret_t20", 0) or 0 for d in past if d.get("excess_ret_t20") is not None]

    for horizon, rets in [(2, t2_rets), (5, t5_rets), (10, t10_rets), (20, t20_rets)]:
        if rets:
            hf[f"hi_avg_ret_t{horizon}"] = round(float(np.mean(rets)), 3)
            hf[f"hi_winrate_t{horizon}"] = round(sum(1 for r in rets if r > 0) / len(rets), 3)

    all_rets = t2_rets + t5_rets + t10_rets + t20_rets
    hf["hi_ret_volatility"] = round(float(np.std(all_rets)), 3) if all_rets else 0.0

    # 最佳窗口
    wr_map = {2: hf.get("hi_winrate_t2", 0), 5: hf.get("hi_winrate_t5", 0),
              10: hf.get("hi_winrate_t10", 0), 20: hf.get("hi_winrate_t20", 0)}
    hf["hi_best_horizon"] = float(max(wr_map, key=wr_map.get)) if wr_map else 5.0

    # 抗跌性 (为简化，暂用占位值)
    hf["hi_crash_resilience"] = 0.0
    hf["hi_rally_capture"] = 0.0
    hf["hi_deception_rate"] = 0.3
    hf["hi_consistency"] = hf["hi_ret_volatility"]
    hf["hi_extreme_tail"] = round(sum(1 for r in all_rets if abs(r) > 15) / max(len(all_rets), 1), 3) if all_rets else 0.0

    return hf


# ══════════════════════════════════════════════════════════════════════
# 数据库写入
# ══════════════════════════════════════════════════════════════════════

async def _upsert_daily_sample(symbol, td, **kwargs):
    """写入/更新 daily_samples."""
    import json
    async with async_session_factory() as s:
        await s.execute(text("""
            INSERT INTO stock_dna.daily_samples
                (symbol, trade_date, emotion_label, emotion_features,
                 cycle_phase, cycle_day, lead_lag_min, independent_pct, amplify_ratio,
                 excess_ret_t2, excess_ret_t5, excess_ret_t10, excess_ret_t20,
                 daily_features, updated_at)
            VALUES
                (:sym, :td, :el, CAST(:ef AS jsonb),
                 :cp, :cd, :ll, :ip, :ar,
                 :r2, :r5, :r10, :r20,
                 CAST(:df AS jsonb), NOW())
            ON CONFLICT (symbol, trade_date) DO UPDATE SET
                emotion_label=EXCLUDED.emotion_label,
                emotion_features=EXCLUDED.emotion_features,
                cycle_phase=EXCLUDED.cycle_phase,
                cycle_day=EXCLUDED.cycle_day,
                lead_lag_min=EXCLUDED.lead_lag_min,
                independent_pct=EXCLUDED.independent_pct,
                amplify_ratio=EXCLUDED.amplify_ratio,
                daily_features=EXCLUDED.daily_features,
                excess_ret_t2=COALESCE(EXCLUDED.excess_ret_t2, stock_dna.daily_samples.excess_ret_t2),
                excess_ret_t5=COALESCE(EXCLUDED.excess_ret_t5, stock_dna.daily_samples.excess_ret_t5),
                excess_ret_t10=COALESCE(EXCLUDED.excess_ret_t10, stock_dna.daily_samples.excess_ret_t10),
                excess_ret_t20=COALESCE(EXCLUDED.excess_ret_t20, stock_dna.daily_samples.excess_ret_t20),
                updated_at=NOW()
        """), {
            "sym": symbol, "td": td,
            "el": kwargs.get("emotion_label", 0),
            "ef": json.dumps(kwargs.get("emotion_features", {}), ensure_ascii=False),
            "cp": kwargs.get("cycle_phase", "normal"),
            "cd": kwargs.get("cycle_day", 0),
            "ll": kwargs.get("lead_lag_min", 0),
            "ip": kwargs.get("independent_pct", 0),
            "ar": kwargs.get("amplify_ratio", 0),
            "r2": kwargs.get("excess_ret_t2", 0),
            "r5": kwargs.get("excess_ret_t5", 0),
            "r10": kwargs.get("excess_ret_t10", 0),
            "r20": kwargs.get("excess_ret_t20", 0),
            "df": json.dumps(kwargs.get("daily_features", {}), ensure_ascii=False),
        })
        await s.commit()


async def _upsert_dna_profile(symbol: str, profile: dict):
    """写入 DNA 档案."""
    import json
    async with async_session_factory() as s:
        await s.execute(text("""
            INSERT INTO stock_dna.profiles
                (symbol, n_emotions, emotion_names, transition_matrix, stationary_dist,
                 emotion_entropy, best_emotion, best_emotion_ret,
                 avg_lockup_days, std_lockup_days, cycle_cv,
                 avg_breakout_return, avg_breakout_days,
                 training_samples, archetype, last_dna_update, updated_at)
            VALUES
                (:sym, :ne, CAST(:en AS jsonb), CAST(:tm AS jsonb), CAST(:sd AS jsonb),
                 :ee, :be, CAST(:ber AS jsonb),
                 :al, :sl, :cv,
                 :abr, :abd,
                 :ts, :at, NOW(), NOW())
            ON CONFLICT (symbol) DO UPDATE SET
                n_emotions=EXCLUDED.n_emotions,
                emotion_names=EXCLUDED.emotion_names,
                transition_matrix=EXCLUDED.transition_matrix,
                stationary_dist=EXCLUDED.stationary_dist,
                emotion_entropy=EXCLUDED.emotion_entropy,
                best_emotion=EXCLUDED.best_emotion,
                best_emotion_ret=EXCLUDED.best_emotion_ret,
                avg_lockup_days=EXCLUDED.avg_lockup_days,
                std_lockup_days=EXCLUDED.std_lockup_days,
                cycle_cv=EXCLUDED.cycle_cv,
                avg_breakout_return=EXCLUDED.avg_breakout_return,
                avg_breakout_days=EXCLUDED.avg_breakout_days,
                training_samples=EXCLUDED.training_samples,
                last_dna_update=NOW(),
                updated_at=NOW()
        """), {
            "sym": symbol,
            "ne": profile.get("n_emotions", 1),
            "en": json.dumps(profile.get("emotion_names", {}), ensure_ascii=False),
            "tm": json.dumps(profile.get("transition_matrix", [])),
            "sd": json.dumps(profile.get("stationary_dist", [])),
            "ee": profile.get("emotion_entropy", 0),
            "be": profile.get("best_emotion", 0),
            "ber": json.dumps(profile.get("best_emotion_ret", {}), ensure_ascii=False),
            "al": profile.get("avg_lockup_days", 0),
            "sl": profile.get("std_lockup_days", 0),
            "cv": profile.get("cycle_cv", 999),
            "abr": profile.get("avg_breakout_return", 0),
            "abd": profile.get("avg_breakout_days", 0),
            "ts": profile.get("training_samples", 0),
            "at": profile.get("archetype", "unknown"),
        })
        await s.commit()
