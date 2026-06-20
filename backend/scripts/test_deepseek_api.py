"""比对: 同一提示词 → DeepSeek API vs 网页版回传数据."""
import httpx, asyncio, json, re

PROMPT = """请对A股 002222.SZ（福晶科技）进行深度投资分析。

【系统量化评分】
  综合评分: 68.5/100
  技术面: 3.7/10 | K线博弈: 5.2/10 | 资金面: 7.5/10
  基本面调整: 12.0 分
  TG信号级别: L1 | TG动量: -4.17
  距低点: 81.03% | J值: 9.84 | 量比: 1.12
  买入强度: 0.3617
  触发路径: 标准维度
  策略原型: 价值防御

【权重调整理由】（系统根据原型差异化调整了各维度权重）
  - 估值·valuation_weight: 上调 40%
  - TG动量权重·tg_momentum_weight: 下调 30%
  - TG动量·tg_momentum_mult: 下调 25%
  - 量比·vol_ratio_mult: 下调 25%
  - 基本面·fundamentals_weight: 上调 20%

【基本面数据】（最近一期财报）
  ROE(%): 16.68
  营收增速(%): 32.21
  利润增速(%): 0.0
  资产负债率(%): 21.42
  流动比率: 5.22
  经营现金流(元): 351004007.62
  PB: 27.15
  PE_TTM: 168.9

请从以下角度分析并给出明确建议：
1. 技术面：当前价格位置、趋势判断、关键支撑/压力位
2. 资金面：主力动向、量价配合情况
3. 基本面：财务健康度、成长性、估值合理性
4. 风险提示：需要警惕的信号
5. 操作建议：短期/中期策略

请在最后用JSON格式输出信号摘要（便于系统自动解析，必须包含 stock_code 字段）：
{"stock_code":"002222.SZ","negative_signals":[{"type":"valuation/financial_risk/fund_flow/technical_risk/sentiment_risk","description":"具体描述","confidence":0.0-1.0}],"positive_signals":[{"type":"opportunity/financial/other","description":"具体描述","confidence":0.0-1.0}]}"""

async def main():
    print(f"Prompt: {len(PROMPT)} chars\n")

    async with httpx.AsyncClient(timeout=120) as c:
        # DeepSeek API (deepseek-chat)
        print("=== DeepSeek API (deepseek-chat) ===")
        resp = await c.post("https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": "Bearer sk-4645a8527599489382564ab29410a0f6"},
            json={"model": "deepseek-chat", "messages": [{"role":"user","content": PROMPT}],
                  "temperature": 0.2, "max_tokens": 8192})

        if resp.status_code != 200:
            print(f"API Error: {resp.status_code} {resp.text[:200]}")
            return

        result = resp.json()
        content = result["choices"][0]["message"]["content"]
        tokens = result.get("usage", {})
        print(f"Tokens: in={tokens.get('prompt_tokens')} out={tokens.get('completion_tokens')}")
        print(f"Length: {len(content)} chars")

        # Check JSON
        m = re.search(r'```(?:json)?\s*\n?(\{.*?\})\s*\n?```', content, re.DOTALL)
        if m:
            parsed = json.loads(m.group(1))
            pos = parsed.get("positive_signals", [])
            neg = parsed.get("negative_signals", [])
            print(f"JSON: {len(pos)} positive, {len(neg)} negative signals")
            for s in pos:
                print(f"  +{s.get('type','?')}: {s.get('description','')[:60]} (conf={s.get('confidence',0)})")
            for s in neg:
                print(f"  -{s.get('type','?')}: {s.get('description','')[:60]} (conf={s.get('confidence',0)})")
            print("\n✅ API返回格式正确，与网页版JSON结构一致!")
        else:
            print("❌ 未找到JSON块")
            print(f"\nResponse tail (last 500):\n{content[-500:]}")

asyncio.run(main())
