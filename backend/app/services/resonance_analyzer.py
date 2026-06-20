"""四维共振分析引擎 (v4.3).

对个股历史 TG 信号进行四维度共振分析, 判断信号的可靠性和独立性。
所有数据来自数据库真实记录, 无模拟/猜测。

四个维度:
  1. 指数共振 — 信号后 T+5 个股 vs 上证指数收益, 判断独立行情/共振/伪强势
  2. 板块共振 — 信号后 T+5 个股 vs 申万行业指数收益, 判断领先/跟随/背离
  3. 消息共振 — 信号日前后 3 天是否有新闻/公告, 判断消息驱动 vs 技术驱动
  4. 筹码共振 — 信号前 20 天的三区吸收率, 统计不同吸收水平下的胜率
"""
import logging
import numpy as np
from collections import defaultdict
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("resonance_analyzer")

# ── 分类阈值 ──
RESONANCE_THRESHOLD = 3.0      # 同步涨跌的阈值 (%)
SECTOR_LEAD_THRESHOLD = 2.0    # 领先板块的阈值 (%)


# ═══════════════════════════════════════════════════════════
# 维度 1: 指数共振分析
# ═══════════════════════════════════════════════════════════

async def analyze_index_resonance(
    symbol: str, signal_dates: list[str], scan_date: date
) -> dict:
    """对每个历史 TG 信号日, 计算信号后 T+5 个股 vs 上证指数收益。

    分类规则 (信号后 T+5):
      个股 +2%+ AND 指数 +1%+     → 共振上涨 (resonant_up)
      个股 +2%+ AND 指数 < +1%    → 独立上涨 (independent_up)
      个股 -2%- AND 指数 -1%-     → 共振下跌 (resonant_down)
      个股 +1%+ AND 指数 < -1%    → 伪强势 (pseudo_strength / 假突破)
      其他                        → 中性
    """
    if not signal_dates:
        return {"status": "insufficient", "index_resonance_rate": 0,
                "independence_rate": 0, "pseudo_strength_rate": 0,
                "summary": "无历史信号数据"}

    # 预加载上证指数日线 (信号日前后 5 天)
    index_td_map: dict[str, float] = {}
    try:
        async with async_session_factory() as s:
            all_dates = set()
            for sd in signal_dates:
                all_dates.add(sd)
            if all_dates:
                r = await s.execute(text("""
                    SELECT trade_date, close FROM daily_kline
                    WHERE ts_code = '700001.TI'
                    ORDER BY trade_date
                """))
                index_td_map = {str(row[0]): float(row[1] or 0) for row in r.fetchall()}
    except Exception as e:
        logger.debug(f"Index data load failed: {e}")

    # 预加载个股日线
    stock_td_map: dict[str, float] = {}
    try:
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT trade_date, close FROM daily_kline
                WHERE ts_code = :s ORDER BY trade_date
            """), {"s": symbol})
            stock_td_map = {str(row[0]): float(row[1] or 0) for row in r.fetchall()}
    except Exception as e:
        logger.debug(f"Stock data load failed: {e}")

    # 获取交易日列表
    sorted_td = sorted(index_td_map.keys())

    categories: dict[str, int] = {"resonant_up": 0, "resonant_down": 0,
                                    "independent_up": 0, "pseudo_strength": 0,
                                    "neutral": 0}
    details: list[dict] = []
    total = 0

    for sd_str in signal_dates:
        if sd_str not in sorted_td:
            continue
        try:
            idx = sorted_td.index(sd_str)
            t5_str = sorted_td[idx + 5] if idx + 5 < len(sorted_td) else None
        except ValueError:
            continue
        if not t5_str:
            continue

        stock_entry = stock_td_map.get(sd_str, 0)
        stock_exit = stock_td_map.get(t5_str, 0)
        idx_entry = index_td_map.get(sd_str, 0)
        idx_exit = index_td_map.get(t5_str, 0)

        if stock_entry <= 0 or idx_entry <= 0:
            continue

        stock_ret = (stock_exit - stock_entry) / stock_entry * 100
        idx_ret = (idx_exit - idx_entry) / idx_entry * 100
        total += 1

        if stock_ret > 2.0 and idx_ret > 1.0:
            cat = "resonant_up"
        elif stock_ret > 2.0 and idx_ret <= 1.0:
            cat = "independent_up"
        elif stock_ret < -2.0 and idx_ret < -1.0:
            cat = "resonant_down"
        elif stock_ret > 1.0 and idx_ret < -1.0:
            cat = "pseudo_strength"
        else:
            cat = "neutral"

        categories[cat] += 1
        if len(details) < 10:
            details.append({
                "date": sd_str,
                "stock_ret": round(stock_ret, 2),
                "index_ret": round(idx_ret, 2),
                "category": cat,
            })

    if total < 3:
        return {"status": "insufficient", "index_resonance_rate": 0,
                "independence_rate": 0, "pseudo_strength_rate": 0,
                "summary": f"有效信号不足 ({total}条)", "details": details}

    resonance_rate = round(categories["resonant_up"] / total, 3)
    independence_rate = round(categories["independent_up"] / total, 3)
    pseudo_rate = round(categories["pseudo_strength"] / total, 3)

    # 生成总结
    parts = []
    if independence_rate >= 0.3:
        parts.append(f"独立上涨率 {independence_rate*100:.0f}%，有独立行情基因")
    elif independence_rate >= 0.15:
        parts.append(f"独立上涨率 {independence_rate*100:.0f}%")
    if resonance_rate >= 0.4:
        parts.append(f"共振率 {resonance_rate*100:.0f}%，依赖大盘环境")
    if pseudo_rate >= 0.2:
        parts.append(f"⚠ 伪强势率 {pseudo_rate*100:.0f}%（大盘跌≠个股涨），注意假突破")

    return {
        "status": "success",
        "total_signals": total,
        "index_resonance_rate": resonance_rate,
        "independence_rate": independence_rate,
        "pseudo_strength_rate": pseudo_rate,
        "resonant_up_count": categories["resonant_up"],
        "independent_up_count": categories["independent_up"],
        "pseudo_strength_count": categories["pseudo_strength"],
        "categories": categories,
        "details": details,
        "summary": " | ".join(parts) if parts else f"指数共振分析({total}条)完成",
    }


# ═══════════════════════════════════════════════════════════
# 维度 2: 板块共振分析
# ═══════════════════════════════════════════════════════════

async def analyze_sector_resonance(
    symbol: str, signal_dates: list[str], scan_date: date
) -> dict:
    """对每个历史 TG 信号日, 计算 T+5 个股 vs 申万行业指数收益。

    通过 ths_member 找所属行业, 从 sw_sector_index 取板块收益。
    分类:
      个股 +2%+ AND 板块 +1%+ AND 个股 > 板块+2% → 领先板块 (leader)
      个股 +1%+ AND 板块 +1%+ AND abs(个股-板块) < 2% → 跟随板块 (follower)
      个股 +2%+ AND 板块 < 0% → 背离板块 (divergent)
      其他 → 中性
    """
    if not signal_dates:
        return {"status": "insufficient", "lead_rate": 0, "follow_rate": 0,
                "diverge_rate": 0, "summary": "无历史信号数据"}

    # 1. 找该股所属 SW 行业指数
    sector_idx_code: str | None = None
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
    try:
        async with async_session_factory() as s:
            r = await s.execute(text(
                "SELECT ths_name FROM ths_member WHERE ts_code = :s AND out_date IS NULL LIMIT 1"
            ), {"s": symbol})
            row = r.fetchone()
            if row and row[0]:
                ths_name = row[0]
                for l1_name, idx_code in SW_L1_MAP.items():
                    if l1_name in ths_name:
                        sector_idx_code = idx_code
                        break
    except Exception as e:
        logger.debug(f"Sector lookup failed for {symbol}: {e}")

    if not sector_idx_code:
        return {"status": "insufficient", "lead_rate": 0, "follow_rate": 0,
                "diverge_rate": 0, "summary": "未匹配到申万行业"}

    # 2. 预加载板块指数日线
    sector_td_map: dict[str, float] = {}
    try:
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT trade_date, close FROM sw_sector_index
                WHERE index_code = :c ORDER BY trade_date
            """), {"c": sector_idx_code})
            sector_td_map = {str(row[0]): float(row[1] or 0) for row in r.fetchall()}
    except Exception as e:
        logger.debug(f"Sector index load failed: {e}")

    # 3. 预加载个股日线
    stock_td_map: dict[str, float] = {}
    try:
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT trade_date, close FROM daily_kline
                WHERE ts_code = :s ORDER BY trade_date
            """), {"s": symbol})
            stock_td_map = {str(row[0]): float(row[1] or 0) for row in r.fetchall()}
    except Exception as e:
        logger.debug(f"Stock data load failed: {e}")

    sorted_td = sorted(stock_td_map.keys())

    categories: dict[str, int] = {"leader": 0, "follower": 0, "divergent": 0, "neutral": 0}
    details: list[dict] = []
    total = 0

    for sd_str in signal_dates:
        if sd_str not in sorted_td:
            continue
        try:
            idx = sorted_td.index(sd_str)
            t5_str = sorted_td[idx + 5] if idx + 5 < len(sorted_td) else None
        except ValueError:
            continue
        if not t5_str:
            continue

        stock_entry = stock_td_map.get(sd_str, 0)
        stock_exit = stock_td_map.get(t5_str, 0)
        sector_entry = sector_td_map.get(sd_str, 0)
        sector_exit = sector_td_map.get(t5_str, 0)

        if stock_entry <= 0 or sector_entry <= 0:
            continue

        stock_ret = (stock_exit - stock_entry) / stock_entry * 100
        sector_ret = (sector_exit - sector_entry) / sector_entry * 100
        diff = stock_ret - sector_ret
        total += 1

        if stock_ret > 2.0 and sector_ret > 1.0 and diff > SECTOR_LEAD_THRESHOLD:
            cat = "leader"
        elif stock_ret > 1.0 and sector_ret > 1.0 and abs(diff) < SECTOR_LEAD_THRESHOLD:
            cat = "follower"
        elif stock_ret > 2.0 and sector_ret < 0:
            cat = "divergent"
        else:
            cat = "neutral"

        categories[cat] += 1
        if len(details) < 10:
            details.append({
                "date": sd_str,
                "stock_ret": round(stock_ret, 2),
                "sector_ret": round(sector_ret, 2),
                "diff": round(diff, 2),
                "category": cat,
            })

    if total < 3:
        return {"status": "insufficient", "lead_rate": 0, "follow_rate": 0,
                "diverge_rate": 0, "summary": f"有效信号不足 ({total}条)", "details": details}

    lead_rate = round(categories["leader"] / total, 3)
    follow_rate = round(categories["follower"] / total, 3)
    diverge_rate = round(categories["divergent"] / total, 3)

    parts = []
    if lead_rate >= 0.25:
        parts.append(f"领先率 {lead_rate*100:.0f}%，有一定龙头属性")
    if diverge_rate >= 0.2:
        parts.append(f"背离率 {diverge_rate*100:.0f}%，独立于板块运行")
    if follow_rate >= 0.5:
        parts.append(f"跟随率 {follow_rate*100:.0f}%，主要跟随板块联动")

    return {
        "status": "success",
        "total_signals": total,
        "sector_code": sector_idx_code,
        "lead_rate": lead_rate,
        "follow_rate": follow_rate,
        "diverge_rate": diverge_rate,
        "leader_count": categories["leader"],
        "follower_count": categories["follower"],
        "divergent_count": categories["divergent"],
        "categories": categories,
        "details": details,
        "summary": " | ".join(parts) if parts else f"板块共振分析({total}条)完成",
    }


# ═══════════════════════════════════════════════════════════
# 维度 3: 消息共振分析
# ═══════════════════════════════════════════════════════════

async def analyze_news_resonance(
    symbol: str, signal_dates: list[str], scan_date: date
) -> dict:
    """查每个历史信号日前后 3 天是否有该股相关新闻/公告。

    判断利好/利空 vs 后续涨跌方向的一致性:
      有新闻 AND 方向一致 → 消息驱动 (news_driven)
      无新闻 → 技术驱动 (tech_driven)
    """
    if not signal_dates:
        return {"status": "insufficient", "news_driven_rate": 0,
                "tech_driven_rate": 0, "summary": "无历史信号数据"}

    # 预加载新闻 — 从 stock_events 查
    news_dates: set[str] = set()
    news_direction: dict[str, str] = {}
    try:
        async with async_session_factory() as s:
            for sd_str in signal_dates:
                sd = date.fromisoformat(sd_str) if len(sd_str) == 10 else None
                if not sd:
                    continue
                r = await s.execute(text("""
                    SELECT event_date, direction, composite_impact
                    FROM stock_events
                    WHERE ts_code = :s
                      AND event_date BETWEEN :d1 AND :d2
                    ORDER BY composite_impact DESC LIMIT 1
                """), {"s": symbol, "d1": sd - timedelta(days=3), "d2": sd + timedelta(days=3)})
                row = r.fetchone()
                if row:
                    news_dates.add(sd_str)
                    news_direction[sd_str] = row[1] or "neutral"
    except Exception as e:
        logger.debug(f"News query failed for {symbol}: {e}")

    # 预加载个股日线
    stock_td_map: dict[str, float] = {}
    try:
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT trade_date, close FROM daily_kline
                WHERE ts_code = :s ORDER BY trade_date
            """), {"s": symbol})
            stock_td_map = {str(row[0]): float(row[1] or 0) for row in r.fetchall()}
    except Exception as e:
        logger.debug(f"Stock data load failed for news resonance: {e}")

    sorted_td = sorted(stock_td_map.keys())

    news_count = 0
    news_correct = 0     # 消息方向与涨跌一致
    tech_count = 0
    total = 0

    for sd_str in signal_dates:
        if sd_str not in sorted_td:
            continue
        try:
            idx = sorted_td.index(sd_str)
            t5_str = sorted_td[idx + 5] if idx + 5 < len(sorted_td) else None
        except ValueError:
            continue
        if not t5_str:
            continue

        stock_entry = stock_td_map.get(sd_str, 0)
        stock_exit = stock_td_map.get(t5_str, 0)
        if stock_entry <= 0:
            continue

        ret = (stock_exit - stock_entry) / stock_entry * 100
        total += 1

        if sd_str in news_dates:
            news_count += 1
            direction = news_direction.get(sd_str, "neutral")
            # 判断消息方向与涨跌是否一致
            if (direction == "bullish" and ret > 0) or (direction == "bearish" and ret < 0):
                news_correct += 1
        else:
            tech_count += 1

    if total < 3:
        return {"status": "insufficient", "news_driven_rate": 0,
                "tech_driven_rate": 0, "summary": f"有效信号不足 ({total}条)"}

    news_driven_rate = round(news_count / total, 3) if total > 0 else 0
    tech_driven_rate = round(tech_count / total, 3) if total > 0 else 0
    news_accuracy = round(news_correct / max(news_count, 1), 3)

    parts = []
    if tech_driven_rate >= 0.5:
        parts.append(f"技术驱动为主({tech_driven_rate*100:.0f}%)，消息影响有限")
    elif news_driven_rate >= 0.3:
        parts.append(f"消息驱动率 {news_driven_rate*100:.0f}%，准确率{news_accuracy*100:.0f}%")
    if news_count > 0 and news_accuracy < 0.5:
        parts.append(f"⚠ 消息方向准确率仅{news_accuracy*100:.0f}%")

    return {
        "status": "success",
        "total_signals": total,
        "news_driven_rate": news_driven_rate,
        "tech_driven_rate": tech_driven_rate,
        "news_accuracy": news_accuracy,
        "news_count": news_count,
        "tech_count": tech_count,
        "news_correct_count": news_correct,
        "summary": " | ".join(parts) if parts else f"消息共振分析({total}条)完成",
    }


# ═══════════════════════════════════════════════════════════
# 维度 4: 筹码共振分析
# ═══════════════════════════════════════════════════════════

async def analyze_chip_resonance(
    symbol: str, signal_dates: list[str], scan_date: date
) -> dict:
    """对每个历史信号日, 用信号前 20 天的 5 分钟数据计算三区吸收率。

    统计高吸收(>60%)、中吸收(40-60%)、低吸收(<40%)三种情况下的后续上涨概率。
    """
    if not signal_dates:
        return {"status": "insufficient", "high_absorption_win_rate": 0,
                "low_absorption_win_rate": 0, "summary": "无历史信号数据"}

    # 预加载个股日线
    stock_td_map: dict[str, float] = {}
    try:
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT trade_date, close, high, low FROM daily_kline
                WHERE ts_code = :s ORDER BY trade_date
            """), {"s": symbol})
            all_rows = [(str(row[0]), float(row[1] or 0), float(row[2] or 0),
                        float(row[3] or 0)) for row in r.fetchall()]
    except Exception as e:
        return {"status": "insufficient", "high_absorption_win_rate": 0,
                "low_absorption_win_rate": 0, "summary": f"日线加载失败: {e}"}

    if len(all_rows) < 60:
        return {"status": "insufficient", "high_absorption_win_rate": 0,
                "low_absorption_win_rate": 0, "summary": "日线数据不足 (<60天)"}

    stock_td_map = {r[0]: r[1] for r in all_rows}
    td_to_data = {r[0]: r for r in all_rows}
    sorted_td = sorted(stock_td_map.keys())

    # 预加载分钟线 (最多回看 scan_date 前 400 天)
    min_bars_by_date: dict[str, list] = defaultdict(list)
    lookback_start = scan_date - timedelta(days=400)
    try:
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT trade_time, high, low, volume
                FROM min_kline
                WHERE ts_code = :s AND trade_time >= :start
                ORDER BY trade_time
            """), {"s": symbol, "start": lookback_start})
            for row in r.fetchall():
                tt = row[0]
                day = str(tt.date()) if hasattr(tt, 'date') else str(tt)[:10]
                min_bars_by_date[day].append({
                    "high": float(row[1] or 0),
                    "low": float(row[2] or 0),
                    "vol": float(row[3] or 0),
                })
    except Exception as e:
        logger.debug(f"Min kline load failed for {symbol}: {e}")

    # 对每个历史信号日，计算信号前 20 天的吸收率
    levels: dict[str, list] = {
        "high": [],    # 吸收率 > 60%
        "medium": [],  # 40-60%
        "low": [],     # < 40%
    }
    processed = 0

    for sd_str in signal_dates:
        if sd_str not in sorted_td:
            continue
        try:
            td_idx = sorted_td.index(sd_str)
            start_idx = max(0, td_idx - 20)
            if td_idx - start_idx < 10:
                continue
        except ValueError:
            continue

        # 取信号前 20 天的日线确定锁死区间
        lookback_days = [sorted_td[i] for i in range(start_idx, td_idx)
                         if i < len(sorted_td)]
        if len(lookback_days) < 10:
            continue

        # 从日线提取价格区间
        h_values = []
        l_values = []
        for d in lookback_days:
            r = td_to_data.get(d)
            if r:
                h_values.append(r[2])
                l_values.append(r[3])

        if not h_values or not l_values:
            continue

        lock_high = float(np.max(h_values))
        lock_low = float(np.min(l_values))
        if lock_low <= 0:
            continue

        # 用分钟线计算三区吸收率
        vol_lock = 0.0
        vol_over = 0.0
        vol_below = 0.0

        for d in lookback_days:
            bars = min_bars_by_date.get(d, [])
            for bar in bars:
                mid = (bar["high"] + bar["low"]) / 2
                vol = bar["vol"]
                if mid >= lock_low and mid <= lock_high:
                    vol_lock += vol
                elif mid > lock_high:
                    vol_over += vol
                else:
                    vol_below += vol

        total_vol = vol_lock + vol_over + vol_below
        if total_vol <= 0:
            continue

        ar = vol_lock / total_vol

        # 查 T+5 涨跌
        try:
            t5_idx = td_idx + 5
            if t5_idx >= len(sorted_td):
                continue
            t5_str = sorted_td[t5_idx]
            entry_p = stock_td_map.get(sd_str, 0)
            exit_p = stock_td_map.get(t5_str, 0)
            if entry_p <= 0:
                continue
            ret = (exit_p - entry_p) / entry_p * 100
            is_win = ret > 0
        except Exception:
            continue

        if ar > 0.60:
            levels["high"].append(is_win)
        elif ar > 0.40:
            levels["medium"].append(is_win)
        else:
            levels["low"].append(is_win)

        processed += 1

    if processed < 5:
        return {"status": "insufficient", "high_absorption_win_rate": 0,
                "low_absorption_win_rate": 0,
                "summary": f"有效信号不足 ({processed}条有分钟线数据)", "processed": processed}

    high_wr = round(sum(levels["high"]) / max(len(levels["high"]), 1), 3)
    med_wr = round(sum(levels["medium"]) / max(len(levels["medium"]), 1), 3)
    low_wr = round(sum(levels["low"]) / max(len(levels["low"]), 1), 3)

    parts = []
    if len(levels["high"]) >= 3:
        if high_wr >= 0.65:
            parts.append(f"筹码高吸收时胜率 {high_wr*100:.0f}%，筹码是关键因子")
        else:
            parts.append(f"高吸收胜率 {high_wr*100:.0f}% (H={len(levels['high'])}条)")
    if len(levels["low"]) >= 3 and low_wr < 0.35:
        parts.append(f"⚠ 低吸收胜率仅 {low_wr*100:.0f}% (L={len(levels['low'])}条)")
    if len(levels["medium"]) >= 3:
        parts.append(f"中吸收胜率 {med_wr*100:.0f}% (M={len(levels['medium'])}条)")

    return {
        "status": "success",
        "processed": processed,
        "high_absorption_win_rate": high_wr,
        "medium_absorption_win_rate": med_wr,
        "low_absorption_win_rate": low_wr,
        "high_count": len(levels["high"]),
        "medium_count": len(levels["medium"]),
        "low_count": len(levels["low"]),
        "summary": " | ".join(parts) if parts else f"筹码共振分析({processed}条)完成",
    }


# ═══════════════════════════════════════════════════════════
# 集成入口
# ═══════════════════════════════════════════════════════════

async def analyze_all_resonance(
    symbol: str, signal_dates: list[str], scan_date: date
) -> dict:
    """四维共振分析总入口。返回聚合报告。"""
    import asyncio as _asyncio

    index_task = analyze_index_resonance(symbol, signal_dates, scan_date)
    sector_task = analyze_sector_resonance(symbol, signal_dates, scan_date)
    news_task = analyze_news_resonance(symbol, signal_dates, scan_date)
    chip_task = analyze_chip_resonance(symbol, signal_dates, scan_date)

    index_r, sector_r, news_r, chip_r = await _asyncio.gather(
        index_task, sector_task, news_task, chip_task
    )

    return {
        "index_resonance": index_r,
        "sector_resonance": sector_r,
        "news_resonance": news_r,
        "chip_resonance": chip_r,
    }
