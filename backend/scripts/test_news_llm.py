"""测试: 新闻 → LLM 分类 → 结构化 JSON."""
import json, asyncio, re
from datetime import datetime
from app.core.database import async_session_factory
from sqlalchemy import text
from app.services.deepseek import call_deepseek

PROMPT_TEMPLATE = """你是一个A股新闻分析引擎。请分析以下财经新闻，输出结构化JSON。

## 输出格式
{{
  "events": [
    {{
      "ts_code": "股票代码(如300209.SZ), 找不到则填null",
      "category": "company|industry|macro|commodity",
      "direction": "bullish|bearish|neutral",
      "scores": {{
        "materiality": "0-5",
        "immediacy": "0-5",
        "certainty": "0-5",
        "scope": "0-5"
      }},
      "composite_impact": "0.0-5.0",
      "title": "事件简述(15字内)",
      "summary": "一句话影响分析(30字内)",
      "related_sectors": ["关联板块"]
    }}
  ],
  "sector_impacts": [
    {{
      "sector": "板块名",
      "direction": "bullish|bearish",
      "composite_impact": "0.0-5.0",
      "drivers": ["驱动事件"],
      "prediction": "预计影响(20字内)"
    }}
  ],
  "macro_summary": "一句话宏观总结(40字内)"
}}

## 分类规则
- company: 直接涉及上市公司(合同/财报/公告/增减持)
- industry: 行业政策/标准/供需变化
- macro: 货币政策/国际形势/市场情绪
- commodity: 大宗商品/期货/原材料价格

## 重要提示
1. 只输出JSON, 不要任何其他文字
2. 无法确定股票代码时填null, 但尽量从新闻中提取公司名→代码
3. 太多无关新闻可以忽略, 只输出有实质影响的事件
4. 同主题多条新闻请合并为一条事件

## 新闻列表
{news_text}
"""


async def run_test(limit: int = 200):
    """取最近N条新闻, 发送给LLM分析."""
    # 1. 获取新闻
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT source, title, content, pub_time FROM news_raw ORDER BY pub_time DESC LIMIT :lim"
        ), {"lim": limit})
        rows = r.fetchall()

    if not rows:
        print("No news data")
        return

    # 2. 构建新闻文本
    lines = []
    for row in rows:
        ts = str(row[3])[:16] if row[3] else "?"
        src = row[0].replace("tushare.pro/news/", "")
        lines.append(f"[{src}] {ts} {row[2][:300]}")

    news_text = "\n".join(lines)
    prompt = PROMPT_TEMPLATE.replace("{news_text}", news_text)

    print(f"News items: {len(rows)}")
    print(f"Prompt length: {len(prompt)} chars")
    print(f"Estimated tokens: ~{len(prompt)//2}")
    print()

    # 3. 调用 LLM
    print("Calling DeepSeek API...")
    raw = await call_deepseek(prompt)
    print(f"Response length: {len(raw)} chars")
    print()

    # 4. 解析 JSON
    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not json_match:
        print("FAILED: No JSON found in response")
        print(f"Response tail (500 chars):\n{raw[-500:]}")
        return

    try:
        data = json.loads(json_match.group(0))
    except json.JSONDecodeError as e:
        print(f"FAILED: JSON parse error: {e}")
        print(f"Raw JSON match: {json_match.group(0)[:500]}")
        return

    events = data.get("events", [])
    sectors = data.get("sector_impacts", [])
    macro = data.get("macro_summary", "")

    print(f"=== Results ===")
    print(f"Events: {len(events)}")
    print(f"Sector impacts: {len(sectors)}")
    print(f"Macro: {macro}")

    # 统计
    categories = {}
    directions = {}
    with_code = 0
    without_code = 0
    for e in events:
        cat = e.get("category", "unknown")
        categories[cat] = categories.get(cat, 0) + 1
        dir_ = e.get("direction", "unknown")
        directions[dir_] = directions.get(dir_, 0) + 1
        if e.get("ts_code"):
            with_code += 1
        else:
            without_code += 1

    print(f"\nCategories: {categories}")
    print(f"Directions: {directions}")
    print(f"With code: {with_code}, Without code: {without_code}")

    # 展示前 10 条
    print(f"\n=== Sample Events ===")
    for e in events[:10]:
        scores = e.get("scores", {})
        print(f"  [{e.get('category','?')}] {e.get('direction','?')} "
              f"impact={e.get('composite_impact','?')} "
              f"code={e.get('ts_code','null')} "
              f"title={e.get('title','?')}")

    # 保存到文件供分析
    with open("scripts/llm_news_test_result.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\nFull result saved to scripts/llm_news_test_result.json")


if __name__ == "__main__":
    import sys
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    asyncio.run(run_test(limit))
