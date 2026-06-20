"""Tushare 公共调用封装."""
import asyncio, logging, httpx
from app.core.config import settings
logger = logging.getLogger(__name__)

async def call_tushare(api_name: str, params: dict, fields: str = "", retry: int = 2) -> list[dict]:
    for attempt in range(retry + 1):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                req = {"api_name": api_name, "token": settings.TUSHARE_TOKEN, "params": params}
                if fields: req["fields"] = fields
                resp = await client.post(settings.TUSHARE_API_URL, json=req)
                data = resp.json()
            if data.get("code") != 0:
                if attempt < retry: await asyncio.sleep(1); continue
                raise RuntimeError(f"Tushare {api_name}: {data.get('msg')}")
            items = data.get("data", {}).get("items", [])
            fields_list = data.get("data", {}).get("fields", [])
            return [dict(zip(fields_list, row)) for row in items]
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            if attempt < retry: await asyncio.sleep(1); continue
    await asyncio.sleep(0.3)  # QPS control
    return []
