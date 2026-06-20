"""系统设置 API."""
import os, re, asyncio, tempfile
from pathlib import Path
from fastapi import APIRouter
from pydantic import BaseModel
from app.schemas.settings import SettingsUpdate

router = APIRouter(prefix="/settings", tags=["settings"])

ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
_env_lock = asyncio.Lock()


@router.put("")
async def update_settings(body: SettingsUpdate):
    if not ENV_PATH.exists():
        return {"status": "error", "detail": ".env file not found"}

    async with _env_lock:
        content = ENV_PATH.read_text(encoding="utf-8")
        if body.tushare_token:
            content = re.sub(r"(?<=TUSHARE_TOKEN=).*", body.tushare_token, content)
        if body.deepseek_key:
            content = re.sub(r"(?<=DEEPSEEK_API_KEY=).*", body.deepseek_key, content)
        if body.baidu_key:
            content = re.sub(r"(?<=BAIDU_QIANFAN_API_KEY=).*", body.baidu_key, content)
        if body.tushare_cookie:
            if "TUSHARE_COOKIE=" in content:
                content = re.sub(r"(?<=TUSHARE_COOKIE=).*", body.tushare_cookie, content)
            else:
                content += f"\nTUSHARE_COOKIE={body.tushare_cookie}"
            os.environ["TUSHARE_COOKIE"] = body.tushare_cookie  # 即时生效
        # 原子写入
        fd, tmp_path = tempfile.mkstemp(dir=ENV_PATH.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, str(ENV_PATH))
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
    return {"status": "success"}


@router.get("")
async def get_settings():
    from app.core.config import settings as s
    return {
        "status": "success",
        "data": {
            "tushare_token_set": bool(s.TUSHARE_TOKEN),
            "deepseek_key_set": bool(s.DEEPSEEK_API_KEY),
            "baidu_key_set": bool(s.BAIDU_QIANFAN_API_KEY),
            "tushare_cookie_set": bool(s.TUSHARE_COOKIE),
        },
    }


@router.post("/test-cookie")
async def test_cookie():
    """测试 Tushare Cookie 是否有效(尝试访问新闻页面)."""
    from app.core.config import settings as s
    cookie = s.TUSHARE_COOKIE
    if not cookie:
        return {"status": "error", "detail": "未配置 Cookie"}

    import httpx
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(
                "https://tushare.pro/news/sina",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Cookie": cookie,
                },
            )
            if r.status_code == 200 and len(r.text) > 10000:
                return {"status": "success", "valid": True, "message": "Cookie 有效"}
            return {"status": "success", "valid": False, "message": f"HTTP {r.status_code}, 响应长度 {len(r.text)}"}
    except Exception as e:
        return {"status": "error", "valid": False, "detail": str(e)}
