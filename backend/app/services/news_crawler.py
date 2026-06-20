"""Tushare News Crawler — undetected-playwright browser automation.

4 sources: xueqiu/fenghuang/jinrongjie/sina.
Pages are JS-rendered, requires headless browser with stealth.
"""
import re, json, logging, asyncio, os
from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("news_crawler")

BEIJING_TZ = timezone(timedelta(hours=8))

NEWS_SOURCES = {
    "xq": "https://tushare.pro/news/xq",
    "fenghuang": "https://tushare.pro/news/fenghuang",
    "jinrongjie": "https://tushare.pro/news/jinrongjie",
    "sina": "https://tushare.pro/news/sina",
}
COOKIE_STR = os.getenv("TUSHARE_COOKIE", "")
if not COOKIE_STR:
    try:
        from app.core.config import settings
        COOKIE_STR = settings.TUSHARE_COOKIE
    except Exception:
        pass


def _parse_cookies() -> list[dict]:
    cookies = []
    for part in COOKIE_STR.split("; "):
        if "=" in part:
            k, _, v = part.partition("=")
            cookies.append({"name": k.strip(), "value": v.strip().strip('"'), "domain": ".tushare.pro", "path": "/"})
    return cookies


def _extract_news_from_html(html: str) -> list[dict]:
    """提取新闻并分类: company(code)/market_summary/leaderboard/normal."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    news_list = []
    current_date = datetime.now().strftime("%Y-%m-%d")
    for el in soup.select(".news_day, .news_item"):
        cls = el.get("class", [])
        if "news_day" in cls:
            m = re.search(r'(\d{1,2})月(\d{1,2})日', el.get_text())
            if m: current_date = f"2026-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
            continue
        if "news_item" in cls:
            text = el.get_text().strip()
            m = re.match(r'^(\d{2}:\d{2})\s*\n?\s*(.*)', text, re.S)
            if not m: continue
            content = re.sub(r'\s+', ' ', m.group(2)).strip()
            
            # 提取股票代码: 600660.SH 或 (603256) 裸代码
            codes_full = re.findall(r'(\d{6}\.[A-Z]{2})', content)
            codes_bare = re.findall(r'\((\d{6})\)', content)
            all_codes = codes_full + [
                c+'.SZ' if c.startswith(('0','3')) else c+'.SH' if c.startswith('6') else c+'.BJ'
                for c in codes_bare
            ]
            
            # 分类
            is_summary = '收评' in content
            is_leaderboard = '竞价看龙头' in content or ('龙头' in content[:30] and '板' in content)
            is_company = bool(all_codes) or (content.startswith('【') and not is_summary)
            style = el.get("style", "")
            is_red = "230, 30, 30" in style or "230,30,30" in style
            
            news_list.append({
                "time_str": m.group(1), "date": current_date,
                "content": content, "codes": all_codes,
                "news_type": "market_summary" if is_summary else "leaderboard" if is_leaderboard else "company" if is_company else "normal",
                "priority": "high" if (is_red or all_codes or is_summary or is_leaderboard) else "normal",
            })
    return news_list


async def _init_browser():
    from playwright.async_api import async_playwright
    import undetected_playwright as upw
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
    context = await browser.new_context(viewport={"width": 1920, "height": 1080}, locale="zh-CN")
    if COOKIE_STR: await context.add_cookies(_parse_cookies())
    await upw.stealth_async(context)
    return pw, browser, context


def _parse_item_time(item: dict):
    try: return datetime.strptime(f"{item['date']} {item['time_str']}", "%Y-%m-%d %H:%M").replace(tzinfo=BEIJING_TZ)
    except: return None


async def crawl_all_sources() -> dict:
    if not COOKIE_STR: return {"error": "TUSHARE_COOKIE not set"}

    # 获取上次爬取时间，只爬取新内容
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT source, MAX(pub_time) FROM news_raw GROUP BY source"))
        last_times = {row[0].replace("tushare.pro/news/", ""): row[1]
                      for row in r.fetchall() if row[1]}

    pw, browser, context = await _init_browser()
    all_news = []
    results = {}
    try:
        for source_name, url in NEWS_SOURCES.items():
            last_time = last_times.get(source_name)
            logger.info(f"Crawling {source_name}" + (f" (since {last_time})" if last_time else ""))
            try:
                page = await context.new_page()
                await page.goto(url, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(3000)
                html = await page.content()
                items = _extract_news_from_html(html)
                # 只保留上次爬取之后的新内容
                if last_time:
                    items = [it for it in items if (pt := _parse_item_time(it)) and pt > last_time]
                logger.info(f"  {source_name}: {len(items)} items" + (f" (new since {last_time})" if last_time else ""))
                for item in items: item["source"] = source_name
                all_news.extend(items)
                results[source_name] = len(items)
                await page.close()
            except Exception as e:
                logger.error(f"  {source_name} failed: {e}")
                results[source_name] = 0
    finally:
        await browser.close()
        await pw.stop()

    seen = set()
    unique = []
    for n in all_news:
        key = (n["content"][:100], n["date"], n["time_str"])
        if key not in seen: seen.add(key); unique.append(n)

    new_count = 0
    async with async_session_factory() as s:
        for n in unique:
            try: pub_time = datetime.strptime(f"{n['date']} {n['time_str']}", "%Y-%m-%d %H:%M").replace(tzinfo=BEIJING_TZ)
            except ValueError: pub_time = datetime.now(BEIJING_TZ)
            r = await s.execute(text("SELECT 1 FROM news_raw WHERE title=:t AND pub_time=:pt LIMIT 1"), {"t": n["content"][:200], "pt": pub_time})
            if r.fetchone(): continue
            await s.execute(text("INSERT INTO news_raw (source, title, content, pub_time) VALUES (:src, :t, :c, :pt)"), {"src": f"tushare.pro/news/{n['source']}", "t": n["content"][:200], "c": n["content"][:2000], "pt": pub_time})
            new_count += 1
        await s.commit()

    logger.info(f"Crawl done: {len(unique)} unique, {new_count} new")
    return {"sources": results, "total_fetched": len(unique), "new_stored": new_count}


async def get_recent_news(hours_back: int = 24, source: str = None) -> list[dict]:
    async with async_session_factory() as s:
        q = "SELECT id, source, title, content, pub_time FROM news_raw WHERE pub_time >= :cutoff"
        params = {"cutoff": datetime.now(timezone.utc) - timedelta(hours=hours_back)}
        if source: q += " AND source LIKE :src"; params["src"] = f"%{source}%"
        q += " ORDER BY pub_time DESC"
        r = await s.execute(text(q), params)
        return [{"id": str(row[0]), "source": row[1], "title": row[2], "content": row[3], "pub_time": str(row[4]) if row[4] else None} for row in r.fetchall()]


async def get_news_for_llm(hours_back: int = 12) -> str:
    news = await get_recent_news(hours_back)
    if not news: return ""
    lines = []
    for n in news:
        ts = n["pub_time"][:16] if n["pub_time"] else "?"
        lines.append(f"{ts} [{n['source']}] {n['content']}")
    return "\n".join(lines)


async def cleanup_old_news(hours: int = 48):
    async with async_session_factory() as s:
        await s.execute(text("DELETE FROM news_raw WHERE fetched_at < NOW() - MAKE_INTERVAL(hours => :h)"), {"h": hours})
        await s.commit()
    logger.info(f"Cleaned news older than {hours}h")


if __name__ == "__main__":
    async def main():
        import sys
        if len(sys.argv) > 1 and sys.argv[1] == "--llm":
            text = await get_news_for_llm(24)
            print(f"News: {len(text)} chars\n{text[:500]}")
        else:
            result = await crawl_all_sources()
            print(json.dumps(result, ensure_ascii=False, indent=2))
    asyncio.run(main())
