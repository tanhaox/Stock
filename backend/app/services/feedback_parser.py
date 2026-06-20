"""DeepSeek 反馈解析器."""
import json, logging
logger = logging.getLogger(__name__)

# ★ 已迁移到 app.prompts.feedback — 保留副本以兼容, 后续统一改为:
# from app.prompts.feedback import EXTRACT_FEEDBACK_PROMPT
EXTRACT_FEEDBACK_PROMPT = """你是一个数据提取器。请从以下分析文本中提取字段，输出严格JSON。字段：business_stage, profit_quality, recurring_profit_pct, suggested_score(0-100), confidence_score(0-1), data_freshness, profit_attribution([{factor,amount,is_recurring}]), hidden_risks([{label,severity,detail}]), catalysts([{label,timeline,impact}]), data_corrections([]), capability_gaps([])。找不到填null或[]。"""

async def parse_feedback_text(raw_text: str) -> dict:
    empty = {"business_stage": None, "profit_quality": None, "recurring_profit_pct": None,
             "suggested_score": None, "confidence_score": None, "data_freshness": None,
             "profit_attribution": [], "hidden_risks": [], "catalysts": [],
             "data_corrections": [], "capability_gaps": []}
    if not raw_text or len(raw_text.strip()) < 20:
        return {**empty, "parse_error": "文本过短"}
    try:
        import httpx
        from app.core.config import settings
        full_prompt = EXTRACT_FEEDBACK_PROMPT + "\n" + raw_text[:8000]
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"},
                json={"model": settings.DEEPSEEK_MODEL, "messages": [{"role":"user","content":full_prompt}],
                      "temperature": 0.1, "max_tokens": 2000})
            text = resp.json()["choices"][0]["message"]["content"]
        if "```json" in text: text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text: text = text.split("```")[1].split("```")[0].strip()
        data = json.loads(text)
        result = {**empty, **{k: v for k, v in data.items() if k in empty}}
        for key in ["profit_attribution", "hidden_risks", "catalysts", "data_corrections", "capability_gaps"]:
            if not isinstance(result.get(key), list): result[key] = []
        return result
    except json.JSONDecodeError as e:
        return {**empty, "parse_error": f"JSON解析失败: {e}"}
    except Exception as e:
        return {**empty, "parse_error": f"API调用失败: {e}"}
