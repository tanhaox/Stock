"""宏观简报生成器 (v4.3).

在 LLM 深度分析前生成不超过 500 字的中文宏观环境摘要，
让 DeepSeek 分析个股时了解大盘背景，避免脱离市场环境。

数据来源 (按优先级):
  1. 高影响新闻: stock_events 最近3天 impact>=3.0 事件
  2. 市场体制: market_status_log 的 phase + advance_pct
  3. 大盘走势: daily_kline 000300.SH 近5日涨跌幅+成交额趋势
  4. 龙虎榜主线: toplist_daily + ths_member 行业净买卖 Top3
  5. 融资情绪: margin_trading 近5日趋势
"""
import logging
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("macro_briefing")


async def build_macro_context(trade_date: date, max_chars: int = 500) -> str:
    """构建宏观环境中文摘要。

    Args:
        trade_date: 目标日期 (通常是今天)
        max_chars: 最大字符数

    Returns:
        以"【今日宏观环境】"开头的摘要，数据不足时返回 ""
    """
    sections: list[str] = []
    total_chars = 0

    # ── 1. 高影响新闻 (stock_events, 最近3天, impact>=3.0) ──
    try:
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT direction, title, composite_impact, created_at
                FROM stock_events
                WHERE event_date >= :cut AND composite_impact >= 3.0
                ORDER BY composite_impact DESC LIMIT 10
            """), {"cut": trade_date - timedelta(days=3)})
            events = [(row[0], row[1], float(row[2] or 0), row[3]) for row in r.fetchall()]

        if events:
            lines = ["- 高影响事件:"]
            for direction, title, impact, created_at in events[:5]:
                if direction == "bullish":
                    dir_label = "[利多]"
                elif direction == "bearish":
                    dir_label = "[利空]"
                else:
                    dir_label = "[中性]"
                # 截断过长的标题
                short_title = title[:50] + ("..." if len(title) > 50 else "")
                lines.append(f"  {dir_label} {short_title} (影响:{impact:.1f})")
            sections.append("\n".join(lines))
    except Exception as e:
        logger.debug(f"macro_briefing news: {e}")

    # ── 2. 市场体制 ──
    try:
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT phase, COALESCE(advance_pct, 0)
                FROM market_status_log
                WHERE trade_date = :d
                ORDER BY trade_date DESC LIMIT 1
            """), {"d": trade_date})
            row = r.fetchone()
        if row and row[0]:
            phase = row[0]
            adv_pct = row[1]
            section = f"- 市场体制: {phase}"
            if adv_pct:
                section += f", 涨跌比 {adv_pct:.0f}%"
            sections.append(section)
    except Exception as e:
        logger.debug(f"macro_briefing regime: {e}")

    # ── 3. 大盘走势 (000300.SH 近5日) ──
    try:
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT trade_date, close, volume
                FROM daily_kline
                WHERE ts_code = '000300.SH' AND trade_date >= :cut
                ORDER BY trade_date DESC LIMIT 6
            """), {"cut": trade_date - timedelta(days=10)})
            rows = [(row[0], float(row[1] or 0), float(row[2] or 0)) for row in r.fetchall()]

        if len(rows) >= 2:
            latest = rows[0]
            prev = rows[1]
            chg = (latest[1] - prev[1]) / prev[1] * 100 if prev[1] > 0 else 0
            # 近5日均量 vs 前5日
            recent_vol = sum(r[2] for r in rows[:5]) / max(len(rows[:5]), 1)
            older_vol = sum(r[2] for r in rows[5:]) / max(len(rows[5:]), 1) if len(rows) > 5 else recent_vol
            vol_chg = (recent_vol - older_vol) / older_vol * 100 if older_vol > 0 else 0
            vol_label = "放量" if vol_chg > 15 else ("缩量" if vol_chg < -15 else "量稳")
            sections.append(
                f"- 沪深300: {latest[1]:.0f} ({chg:+.1f}%) | 近5日{vol_label}({vol_chg:+.0f}%)"
            )
    except Exception as e:
        logger.debug(f"macro_briefing index: {e}")

    # ── 4. 龙虎榜主线 (行业净买卖 Top3) ──
    try:
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT tm.ths_name, SUM(tl.l_buy - tl.l_sell) as net,
                       SUM(tl.l_buy) as buy_total, SUM(tl.l_sell) as sell_total
                FROM toplist_daily tl
                LEFT JOIN ths_member tm ON tm.ts_code = tl.ts_code AND tm.out_date IS NULL
                WHERE tl.trade_date >= :cut
                  AND tm.ths_name IS NOT NULL
                GROUP BY tm.ths_name
                ORDER BY net DESC
            """), {"cut": trade_date - timedelta(days=3)})
            sector_rows = [(row[0], float(row[1] or 0)) for row in r.fetchall()]

        if sector_rows:
            top_buy = [s for s in sector_rows if s[1] > 0][:3]
            top_sell = sorted(sector_rows, key=lambda x: x[1])[:3]
            parts = []
            if top_buy:
                parts.append("买入: " + ", ".join(f"{name}(+{net/1e4:.0f}万)" for name, net in top_buy))
            if top_sell:
                parts.append("卖出: " + ", ".join(f"{name}({net/1e4:.0f}万)" for name, net in top_sell))
            if parts:
                sections.append("- 龙虎榜主线: " + " | ".join(parts))
    except Exception as e:
        logger.debug(f"macro_briefing toplist: {e}")

    # ── 5. 融资情绪 ──
    try:
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT rzmre FROM margin_trading
                WHERE trade_date >= :cut ORDER BY trade_date DESC LIMIT 10
            """), {"cut": trade_date - timedelta(days=15)})
            vals = [float(row[0] or 0) for row in r.fetchall() if float(row[0] or 0) > 100]

        if len(vals) >= 6:
            recent_avg = sum(vals[:5]) / 5
            older_avg = sum(vals[5:10]) / 5 if len(vals) >= 10 else sum(vals[5:]) / len(vals[5:])
            chg_pct = (recent_avg - older_avg) / older_avg * 100 if older_avg > 0 else 0
            sentiment = "积极" if chg_pct > 15 else ("偏积极" if chg_pct > 5 else ("偏谨慎" if chg_pct < -10 else "中性"))
            sections.append(f"- 融资情绪: {sentiment} (近5日{recent_avg/1e4:.0f}亿, {chg_pct:+.0f}%)")
    except Exception as e:
        logger.debug(f"macro_briefing margin: {e}")

    # ── 组装 ──
    if not sections:
        return ""

    header = "【今日宏观环境】"
    body = "\n".join(sections)

    # 截断到 max_chars
    if len(header) + len(body) > max_chars:
        # 优先保留前4个section
        truncated = sections[:4]
        body = "\n".join(truncated)
        if len(header) + len(body) > max_chars:
            body = body[:max_chars - len(header) - 5] + "..."

    return header + "\n" + body


async def format_macro_for_prompt(trade_date: date) -> str:
    """生成适合嵌入 LLM 提示词的宏观段落。

    如果 build_macro_context 有数据，返回完整段落。
    如果无数据，返回空字符串 (不占提示词 token)。
    """
    ctx = await build_macro_context(trade_date)
    if not ctx:
        return ""
    return ctx + "\n"
