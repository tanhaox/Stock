"""DeepSeek API client — 带指数退避重试和 Token 用量追踪."""
import httpx, logging, time
from app.core.config import settings

logger = logging.getLogger(__name__)

# 重试配置
MAX_RETRIES = 3
RETRY_BACKOFF = [2, 4, 8]  # 秒
RETRYABLE_ERRORS = (httpx.TimeoutException, httpx.ConnectError, httpx.ReadTimeout)

# Token 用量累计统计 (进程级)
_token_stats = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def get_token_stats() -> dict:
    """返回累计 Token 用量统计."""
    return dict(_token_stats)


async def call_deepseek(prompt: str, max_tokens: int = 4096, model: str = None) -> str:
    """调用 DeepSeek chat API.

    - 指数退避重试 (最多 3 次, 仅重试超时/连接错误)
    - 自动提取并记录 Token 用量
    - 返回 LLM 响应文本, 失败时返回 "[LLM调用失败: ...]"
    """
    if not settings.DEEPSEEK_API_KEY:
        return "[LLM未配置]"

    model = model or settings.DEEPSEEK_MODEL
    t0 = time.monotonic()

    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                resp = await client.post(
                    f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.2,
                        "max_tokens": max_tokens,
                    },
                )
                data = resp.json()

                # 提取 Token 用量
                usage = data.get("usage", {})
                pt = usage.get("prompt_tokens", 0)
                ct = usage.get("completion_tokens", 0)
                tt = usage.get("total_tokens", pt + ct)
                _token_stats["calls"] += 1
                _token_stats["prompt_tokens"] += pt
                _token_stats["completion_tokens"] += ct
                _token_stats["total_tokens"] += tt

                elapsed = time.monotonic() - t0
                logger.info(
                    f"DeepSeek {model}: {pt}+{ct}={tt} tokens, "
                    f"{elapsed:.1f}s, attempt={attempt+1}"
                )

                return data["choices"][0]["message"]["content"]

        except RETRYABLE_ERRORS as e:
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF[attempt]
                logger.warning(f"DeepSeek retry {attempt+1}/{MAX_RETRIES} in {wait}s: {e}")
                import asyncio
                await asyncio.sleep(wait)
            else:
                logger.error(f"DeepSeek failed after {MAX_RETRIES} attempts: {e}")
                return f"[LLM调用失败: {e}]"
        except Exception as e:
            logger.warning(f"DeepSeek call failed (non-retryable): {e}")
            return f"[LLM调用失败: {e}]"

    return "[LLM调用失败: max retries exceeded]"
