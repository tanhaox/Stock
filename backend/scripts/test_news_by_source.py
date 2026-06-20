"""分源LLM测试: 每个新闻源独立调用DeepSeek, 对比效果."""
import json, asyncio, re
from app.core.database import async_session_factory
from sqlalchemy import text
from app.services.deepseek import call_deepseek

PROMPT_PER_SOURCE = """你是一个A股新闻分析引擎。请分析以下{sources_desc}的财经新闻，输出结构化JSON。

## 输出格式
{{
  "events": [
    {{
      "ts_code": "股票代码(如300209.SZ), 无法确定填null",
      "category": "company|industry|macro|commodity",
      "direction": "bullish|bearish|neutral",
      "scores": {{
        "materiality": 0-5,
        "immediacy": 0-5,
        "certainty": 0-5,
        "scope": 0-5
      }},
      "composite_impact": 0.0-5.0,
      "title": "事件简述(15字内)",
      "summary": "一句话影响(30字内)",
      "related_sectors": ["板块"]
    }}
  ],
  "sector_impacts": [
    {{
      "sector": "板块名",
      "direction": "bullish|bearish",
      "composite_impact": 0.0-5.0,
      "drivers": ["驱动事件"],
      "prediction": "预计影响(20字内)"
    }}
  ]
}}

## 本来源特点
{source_hint}

## 重要提示
1. 只输出JSON, 不要任何其他文字
2. 从公司名提取股票代码, 不确定填null
3. 同主题多条新闻合并为一条事件
4. 无法判断影响的无关新闻直接忽略
5. 公司级事件尽量填ts_code

## 新闻列表
{news_text}
"""

SOURCE_HINTS = {
    "xq": "雪球以散户视角为主，关注热门股、概念题材、大宗商品。重点提取公司级事件和商品价格变动。",
    "fenghuang": "凤凰财经偏宏观政策和国际视角。重点提取产业政策、国际形势、宏观情绪。",
    "jinrongjie": "金融界侧重专业机构视角。重点提取行业研报、机构动向、政策解读。",
    "sina": "新浪财经覆盖面广，公司新闻多。重点提取上市公司公告、高管变动、重大合同。",
}


async def test_by_source(source_name: str, limit: int = 500):
    """对单个新闻源进行LLM测试."""
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT content, pub_time FROM news_raw WHERE source LIKE :s ORDER BY pub_time DESC LIMIT :lim"
        ), {"s": f"%{source_name}%", "lim": limit})
        rows = r.fetchall()

    if not rows:
        print(f"[{source_name}] No data")
        return None

    lines = []
    for row in rows:
        ts = str(row[1])[:16] if row[1] else "?"
        lines.append(f"{ts} {row[0][:300]}")
    news_text = "\n".join(lines)
    prompt = PROMPT_PER_SOURCE.format(
        sources_desc=f"来自{source_name}",
        source_hint=SOURCE_HINTS.get(source_name, ""),
        news_text=news_text,
    )

    print(f"[{source_name}] {len(rows)} items, {len(prompt)} chars (~{len(prompt)//2} tokens)")
    print(f"  Calling LLM...")
    raw = await call_deepseek(prompt, max_tokens=32768)
    print(f"  Response: {len(raw)} chars")

    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not json_match:
        print(f"  FAILED: No JSON")
        return None

    try:
        data = json.loads(json_match.group(0))
    except json.JSONDecodeError as e:
        # Try to fix common JSON issues
        raw_json = json_match.group(0)
        # Fix 1: remove trailing incomplete content
        last_brace = raw_json.rfind('}')
        if last_brace > 0:
            fixed = raw_json[:last_brace+1]
            try:
                data = json.loads(fixed)
                print(f"  Fixed: truncated at last }}")
                return data
            except: pass
        # Fix 2: remove trailing commas before } or ]
        fixed = re.sub(r',\s*}', '}', raw_json)
        fixed = re.sub(r',\s*]', ']', fixed)
        try:
            data = json.loads(fixed)
            print(f"  Fixed: trailing commas removed")
        except:
            print(f"  FAILED: JSON error: {e}")
            return None

    events = data.get("events", [])
    sectors = data.get("sector_impacts", [])
    with_code = sum(1 for e in events if e.get("ts_code"))
    cats = {}
    for e in events: cats[e.get("category","?")] = cats.get(e.get("category","?"), 0) + 1

    print(f"  Events: {len(events)} (with_code={with_code}) cats={cats}")
    print(f"  Sectors: {len(sectors)}")
    for e in events[:3]:
        print(f"    [{e.get('category','?')}] {e.get('direction','?')} impact={e.get('composite_impact','?')} {e.get('title','?')[:40]}")
    return data


async def main():
    results = {}
    for src in ["xq", "fenghuang", "jinrongjie", "sina"]:
        print(f"\n{'='*60}")
        data = await test_by_source(src, limit=500)
        if data:
            results[src] = {
                "events": len(data.get("events", [])),
                "sectors": len(data.get("sector_impacts", [])),
                "with_code": sum(1 for e in data.get("events", []) if e.get("ts_code")),
            }

    print(f"\n{'='*60}")
    print("=== Summary ===")
    for src, r in results.items():
        print(f"  {src}: {r['events']} events ({r['with_code']} w/ code), {r['sectors']} sectors")

    with open("scripts/llm_news_by_source.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
