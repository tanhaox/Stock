"""端到端测试: 提交JSON反哺 → 验证解析 + experience_replay + check-batch."""
import asyncio, json, time, sys
sys.path.insert(0, 'C:/AI-Agent-Local/Stock/backend')
import httpx

TEST_JSON = json.dumps({
    "stock_code": "600660.SH",
    "positive_signals": [
        {"type": "financial", "description": "营收增长毛利修复", "confidence": 0.9},
        {"type": "opportunity", "description": "PE历史低位", "confidence": 0.85},
    ],
    "negative_signals": [
        {"type": "technical_risk", "description": "MACD死叉均线空头", "confidence": 0.85},
        {"type": "fund_flow", "description": "大单资金流出", "confidence": 0.8},
    ]
})
PAYLOAD = "```json\n" + TEST_JSON + "\n```"

async def main():
    # 1. 提交
    async with httpx.AsyncClient() as c:
        r = await c.post("http://127.0.0.1:8000/api/feedback/submit-raw", json={
            "ts_code": "600660.SH", "trade_date": "2026-05-24",
            "raw_response": PAYLOAD,
        }, headers={"X-User-ID": "browser-extension"})
        print(f"[1] Submit: {r.status_code} {r.json()['status']}")

    await asyncio.sleep(3)

    from app.core.database import async_session_factory
    from sqlalchemy import text
    async with async_session_factory() as s:
        # 2. 验证 stock_deep_feedback
        r = await s.execute(text("SELECT ts_code, jsonb_array_length(hidden_risks), jsonb_array_length(catalysts) FROM stock_deep_feedback WHERE ts_code='600660.SH' AND trade_date='2026-05-24' ORDER BY generated_at DESC LIMIT 1"))
        row = r.fetchone()
        print(f"[2] Feedback: {row[1]} risks, {row[2]} catalysts" if row else "[2] NO DATA")

        # 3. 验证 experience_replay
        r = await s.execute(text("SELECT COUNT(*), SUM(reward) FROM experience_replay WHERE created_at > NOW() - INTERVAL '2 minutes'"))
        cnt, total = r.fetchone()
        print(f"[3] experience_replay: {cnt} entries, total_reward={total}")

        # 4. 显示条目详情
        if cnt > 0:
            r = await s.execute(text("SELECT event_type, reward, category_tags FROM experience_replay WHERE created_at > NOW() - INTERVAL '2 minutes' LIMIT 5"))
            for row in r.fetchall():
                print(f"    {row[0]}: reward={row[1]} tags={row[2]}")

    # 5. verify check-batch
    async with httpx.AsyncClient() as c:
        r = await c.get("http://127.0.0.1:8000/api/feedback/check-batch", params={"symbols": "600660.SH", "trade_date": "2026-05-24"})
        data = r.json()["data"]["600660.SH"]
        print(f"[5] check-batch: received={data['received']} risks={len(data.get('hidden_risks',[]))} cats={len(data.get('catalysts',[]))} has_raw={'raw_response' in data}")

    if cnt == 4:  # 2 pos + 2 neg
        print("\nALL TESTS PASSED")
    else:
        print(f"\nEXPECTED 4 experience_replay entries, got {cnt} — server may be running old code")

asyncio.run(main())
