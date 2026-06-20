"""信号分布异常检测 (v4.3).

每日扫描后自动检查信号是否异常:
  - 当日买入信号数量偏离历史均值 > 3σ 时告警
  - 当日 win_probability 均值低于历史 50% 时告警
  - 异常写入 sync_log (level='anomaly')

由 background_sync.daily_task / accuracy_tracker 调用。
"""
import logging
import numpy as np
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("anomaly_detector")


async def check_signal_distribution(scan_date: date = None) -> dict:
    """检查当日信号分布是否异常.

    Args:
        scan_date: 扫描日期，默认取最新 scan_results 日期

    Returns:
        {"status": "ok"|"anomaly", "warnings": [...], "details": {...}}
    """
    if scan_date is None:
        scan_date = date.today()

    async with async_session_factory() as s:
        # 1. 当日买入信号数量
        r = await s.execute(text("""
            SELECT COUNT(*) FROM scan_results
            WHERE scan_date = :d AND level IN ('L2','L3')
        """), {"d": scan_date})
        today_count = r.scalar() or 0

        # 2. 过去 20 个交易日的均值 + 标准差
        r = await s.execute(text("""
            SELECT d, COUNT(*)
            FROM (
                SELECT scan_date as d
                FROM scan_results
                WHERE scan_date < :d AND scan_date >= :cut
                  AND level IN ('L2','L3')
                GROUP BY scan_date
            ) sub
            GROUP BY d ORDER BY d
        """), {"d": scan_date, "cut": scan_date - timedelta(days=60)})
        rows = r.fetchall()
        if len(rows) < 5:
            return {"status": "ok", "detail": "历史数据不足 (<5天)，跳过异常检测"}

        counts = [row[1] for row in rows]
        mean_count = float(np.mean(counts))
        std_count = float(np.std(counts)) if len(counts) >= 3 else max(mean_count * 0.3, 1)

        warnings = []
        details = {"today_count": today_count, "mean_20d": round(mean_count, 1),
                   "std_20d": round(std_count, 1), "history_days": len(counts)}

        # 异常1: 信号数量远超正常
        if std_count > 0 and today_count > mean_count + 3 * std_count:
            msg = (f"异常：今日买入信号数量 {today_count}，远超正常范围 "
                   f"(均值 {mean_count:.0f} ± {std_count:.0f})")
            warnings.append(msg)
            logger.warning(msg)

        # 异常2: 信号数量远低于正常 (可能是数据问题)
        if std_count > 0 and today_count > 0 and today_count < mean_count - 3 * std_count:
            msg = (f"异常：今日买入信号数量 {today_count}，远低于正常范围 "
                   f"(均值 {mean_count:.0f} ± {std_count:.0f})。可能是数据缺失。")
            warnings.append(msg)
            logger.warning(msg)

        # 3. 检查 win_probability 均值
        r = await s.execute(text("""
            SELECT AVG(a.win_probability)
            FROM analysis_scores a
            WHERE a.scan_date = :d AND a.win_probability IS NOT NULL
        """), {"d": scan_date})
        today_wp_mean = (r.scalar() or 0)

        r = await s.execute(text("""
            SELECT AVG(win_probability) FROM (
                SELECT AVG(a.win_probability) as win_probability
                FROM analysis_scores a
                WHERE a.scan_date < :d AND a.scan_date >= :cut
                  AND a.win_probability IS NOT NULL
                GROUP BY a.scan_date
            ) sub
        """), {"d": scan_date, "cut": scan_date - timedelta(days=60)})
        hist_wp_mean = (r.scalar() or 0.35)

        if hist_wp_mean > 0 and today_wp_mean < hist_wp_mean * 0.5:
            msg = (f"异常：今日 win_probability 均值 {today_wp_mean:.3f}，"
                   f"远低于历史 {hist_wp_mean:.3f} (<50%)。权重可能失效。")
            warnings.append(msg)
            logger.warning(msg)

        details["today_wp_mean"] = round(float(today_wp_mean), 4)
        details["hist_wp_mean"] = round(float(hist_wp_mean), 4)

        status = "anomaly" if warnings else "ok"

        # 4. 记录到 sync_log
        if warnings:
            try:
                import json as _json
                await s.execute(text(
                    """INSERT INTO sync_log (task_name, status, detail, started_at, completed_at)
                       VALUES ('anomaly_check', 'warning', CAST(:dt AS jsonb), NOW(), NOW())"""
                ), {"dt": _json.dumps({"scan_date": str(scan_date),
                                        "warnings": warnings, **details})})
                await s.commit()
            except Exception:
                pass  # sync_log 表可能不存在

    return {"status": status, "warnings": warnings, "details": details}
