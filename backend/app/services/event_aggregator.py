"""Event aggregation service - extracted from scan.py (v4.3).

v4.9 新增: 同一股票相似标题去重
  - 基于 SimHash 指纹计算标题相似度
  - 同股同主题只保留最高 display_score 的一条
"""
import re
import logging
from datetime import date, datetime, timedelta, timezone
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("event_aggregator")


# ══════════════════════════════════════════════════════════════════════
# SimHash 相似度去重 (复用 news_classifier 的实现)
# ══════════════════════════════════════════════════════════════════════
import hashlib

def _tokenize(text: str) -> list[str]:
    """中文按2字切分 + 英文按词切分."""
    text = re.sub(r'[^一-龥a-zA-Z0-9]', ' ', text)
    tokens = []
    for segment in text.split():
        if not segment.strip():
            continue
        if re.match(r'^[一-龥]+$', segment):
            for i in range(len(segment) - 1):
                tokens.append(segment[i:i+2])
        else:
            tokens.append(segment.lower())
    return tokens


def _hash_token(token: str) -> int:
    """对token计算MD5 hash → 32-bit整数."""
    return int(hashlib.md5(token.encode()).hexdigest()[:8], 16)


def compute_simhash(text: str) -> int:
    """计算文本的 SimHash 指纹 (64-bit)."""
    tokens = _tokenize(text)
    if not tokens:
        return 0
    vector = [0] * 64
    for token in tokens:
        h = _hash_token(token)
        for i in range(64):
            if h & (1 << i):
                vector[i] += 1
            else:
                vector[i] -= 1
    fingerprint = 0
    for i in range(64):
        if vector[i] > 0:
            fingerprint |= (1 << i)
    return fingerprint


def hamming_distance(hash1: int, hash2: int) -> int:
    """计算两个 SimHash 的汉明距离 (0-64)."""
    xor = hash1 ^ hash2
    return bin(xor).count('1')


def _is_similar_title(hash1: int, hash2: int, threshold: int = 8) -> bool:
    """判断两标题是否相似 (汉明距离 < threshold)."""
    return hamming_distance(hash1, hash2) < threshold


def _dedup_similar_events(events: list[dict]) -> list[dict]:
    """同一股票内相似标题去重，每股同主题只保留最高 display_score 的一条.

    Args:
        events: 事件列表，每条含 ts_code, title, display_score

    Returns:
        去重后的事件列表
    """
    if not events:
        return []

    # 按股票分组
    from collections import defaultdict
    by_stock = defaultdict(list)
    for e in events:
        by_stock[e["ts_code"]].append(e)

    result = []
    for ts_code, stock_events in by_stock.items():
        # 按 display_score 降序
        stock_events.sort(key=lambda x: x["display_score"], reverse=True)
        kept: list[int] = []  # 保留的 SimHash 索引

        for e in stock_events:
            e_hash = compute_simhash(e["title"])
            is_dup = False
            for kept_hash in kept:
                if _is_similar_title(e_hash, kept_hash):
                    is_dup = True
                    logger.debug(f"dedup similar: [{ts_code}] '{e['title'][:30]}...' skip (simhash dup)")
                    break

            if not is_dup:
                kept.append(e_hash)
                result.append(e)
            # else: 跳过相似标题

    n_removed = len(events) - len(result)
    if n_removed > 0:
        logger.info(f"event dedup: {len(events)} → {len(result)} ({n_removed} similar titles removed)")
    return result


def _match_hk_to_a(hk_code: str, title: str, a_names: dict[str, str]) -> str | None:
    """Try matching HK stock event to A-stock code via company name."""
    name_match = re.match(r'([一-鿿]{2,4})', title)
    if not name_match:
        return None
    company = name_match.group(1)
    skip_words = {'今日','昨日','明天','本周','上周','早盘','午盘','收盘','开盘','涨停','跌停','市场','大盘','上证','深证','创业','科创','北向','主力','游资','机构'}
    if company in skip_words:
        return None
    for a_code, a_name in a_names.items():
        if company in a_name:
            return a_code
    return None


def _norm_sector(sec: str) -> str:
    """Normalize sector name."""
    if not sec:
        return "unknown"
    return sec.strip()


async def get_aggregated_events(hours: int = 24) -> dict:
    """HK->A matching + event decay + market classification + sector dedup + macro factors + last_analysis timestamp."""
    today = date.today()
    lookback = today - timedelta(days=7)

    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT ts_code, direction, composite_impact, title, summary, decay_days, created_at, event_date "
            "FROM stock_events WHERE event_date BETWEEN :lb AND :d "
            "AND (ts_code LIKE '%.SZ' OR ts_code LIKE '%.SH' OR (length(ts_code)=6 AND ts_code ~ '^[036]')) "
            "ORDER BY composite_impact DESC LIMIT 60"
        ), {"lb": lookback, "d": today})
        rows = r.fetchall()

        # Preload A-stock name map (v4.9: 三层兜底: stock_basic → toplist_daily → stock_name_cache)
        # stock_basic: 5528条, 覆盖最全
        r2 = await s.execute(text("SELECT ts_code, name FROM stock_basic"))
        a_stock_names = {row[0]: row[1] for row in r2.fetchall()}
        # toplist_daily: 补充 recent list_status 股票名称
        r3 = await s.execute(text("SELECT DISTINCT ts_code, name FROM toplist_daily"))
        for row in r3.fetchall():
            if row[0] not in a_stock_names:
                a_stock_names[row[0]] = row[1]
        # stock_name_cache: 兜底老代码 (注意: symbol 可能无后缀 .SH/.SZ)
        r4 = await s.execute(text("SELECT symbol, name FROM stock_name_cache"))
        for row in r4.fetchall():
            if row[0] not in a_stock_names:
                a_stock_names[row[0]] = row[1]

    stocks = []
    for row in rows:
        ts_code = row[0]
        impact = float(row[2] or 0)
        decay_days = int(row[5] or 3)
        created_at = row[6]
        if created_at:
            ct = created_at.replace(tzinfo=timezone.utc) if getattr(created_at, 'tzinfo', None) is None else created_at
            days_since = (datetime.now(timezone.utc) - ct).total_seconds() / 86400
        else:
            days_since = 0
        if days_since >= decay_days:
            continue
        freshness = round(max(0, 1 - days_since / max(decay_days, 1)), 2)
        display_score = round(impact * freshness, 2)
        entry = {"ts_code": ts_code, "name": a_stock_names.get(ts_code, ""),
                 "direction": row[1],
                 "impact": impact, "title": row[3], "summary": row[4],
                 "freshness": freshness, "display_score": display_score,
                 "decay_days": decay_days, "event_date": str(row[7]),
                 "days_ago": round(days_since, 1)}
        stocks.append(entry)
    stocks.sort(key=lambda x: x["display_score"], reverse=True)

    # v4.9: 同一股票内相似标题去重 (SimHash 指纹)
    stocks = _dedup_similar_events(stocks)

    # Market classification
    sme_stocks = [s for s in stocks if s["ts_code"].startswith('002') or s["ts_code"].startswith('003')]
    main_stocks = [s for s in stocks if not (s["ts_code"].startswith('300') or s["ts_code"].startswith('301') or s["ts_code"].startswith('688') or s["ts_code"].startswith('002') or s["ts_code"].startswith('003'))]
    chinext_stocks = [s for s in stocks if s["ts_code"].startswith('300') or s["ts_code"].startswith('301') or s["ts_code"].startswith('688')]

    # Sector events (dedup + normalize)
    sectors = []
    macro_factors = []
    last_analysis = None
    try:
        async with async_session_factory() as s:
            r_se = await s.execute(text(
                "SELECT sector, direction, composite_impact, prediction "
                "FROM sector_events WHERE event_date BETWEEN :lb AND :d "
                "ORDER BY composite_impact DESC"
            ), {"lb": lookback, "d": today})
            deduped = {}
            for row in r_se.fetchall():
                sec = _norm_sector(row[0])
                imp = float(row[2] or 0)
                if sec not in deduped or imp > deduped[sec]["impact"]:
                    deduped[sec] = {"sector": sec, "direction": row[1], "impact": imp, "prediction": row[3]}
            sectors = sorted(deduped.values(), key=lambda x: x["impact"], reverse=True)[:20]

            # Macro factors
            r_macro = await s.execute(text(
                "SELECT direction, composite_impact, prediction FROM sector_events "
                "WHERE event_date BETWEEN :lb AND :d AND sector LIKE 'macro-%' "
                "ORDER BY composite_impact DESC"
            ), {"lb": lookback, "d": today})
            for row in r_macro.fetchall():
                macro_factors.append({
                    "direction": row[0], "impact": float(row[1] or 0),
                    "summary": row[2] or "",
                })

            # Last analysis time
            r_ts = await s.execute(text("SELECT MAX(created_at) FROM stock_events"))
            last_ts = r_ts.scalar()
            if last_ts:
                if getattr(last_ts, 'tzinfo', None) is not None:
                    last_ts_utc = last_ts.astimezone(timezone.utc)
                else:
                    last_ts_utc = last_ts.replace(tzinfo=timezone.utc)
                delta = datetime.now(timezone.utc) - last_ts_utc
                hours_ago = round(delta.total_seconds() / 3600, 1)
                last_analysis = {"at": str(last_ts), "hours_ago": hours_ago,
                               "stale": hours_ago > 24}
    except Exception:
        pass

    # v4.9: 扁平结构, 去掉 status/data 嵌套, 直接返回业务数据
    return {
        "stock_events": stocks, "main_stock_events": main_stocks,
        "sme_stock_events": sme_stocks,
        "chinext_stock_events": chinext_stocks, "sector_events": sectors,
        "last_analysis": last_analysis,
    }
