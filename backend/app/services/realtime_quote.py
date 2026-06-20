"""实时行情 — 东方财富免费接口，盘中即时可用."""
import httpx, logging, re

logger = logging.getLogger(__name__)

EM_BASE = "http://push2.eastmoney.com/api/qt/stock/get"


def _ts_code_to_em_code(ts_code: str) -> str:
    """000001.SZ → 0.000001 / 600000.SH → 1.600000"""
    parts = ts_code.split(".")
    market = "0" if parts[1] == "SZ" else "1"
    return f"{market}.{parts[0]}"


async def get_realtime_quote(ts_code: str) -> dict | None:
    """获取单只股票实时报价."""
    secid = _ts_code_to_em_code(ts_code)
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(EM_BASE, params={
                "secid": secid,
                "fields": "f43,f44,f45,f46,f47,f48,f50,f51,f52,f57,f58,f60,f170",
                "ut": "fa5fd1943c7b386f172d6893dbf28f38",
            })
            data = resp.json().get("data", {})
            if not data:
                return None
            return {
                "symbol": ts_code,
                "name": data.get("f58", ""),
                "price": data.get("f43", 0) / 100 if data.get("f43") else None,  # 当前价(分→元)
                "open": data.get("f46", 0) / 100 if data.get("f46") else None,
                "high": data.get("f44", 0) / 100 if data.get("f44") else None,
                "low": data.get("f45", 0) / 100 if data.get("f45") else None,
                "volume": data.get("f47", 0),       # 成交量(手)
                "amount": data.get("f48", 0),        # 成交额(元)
                "change_pct": data.get("f170", 0) / 100 if data.get("f170") else None,  # 涨跌幅%
                "change": data.get("f169", 0) / 100 if data.get("f169") else None,       # 涨跌额
                "pre_close": data.get("f60", 0) / 100 if data.get("f60") else None,
                "source": "eastmoney_realtime",
            }
    except Exception as e:
        logger.warning(f"Realtime quote failed for {ts_code}: {e}")
        return None


async def get_batch_realtime_quotes(ts_codes: list[str]) -> dict[str, dict]:
    """批量获取实时报价(并发单只请求)."""
    if not ts_codes:
        return {}
    import asyncio
    tasks = [get_realtime_quote(c) for c in ts_codes]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out = {}
    for code, r in zip(ts_codes, results):
        if isinstance(r, dict) and r:
            out[code] = r
    return out
