"""分钟线数据服务 — 统一的 5 分钟 K 线下载入口 (v4.3).

消除 chip_analyzer 和 minute_nm_detector 中的重复实现。
"""
import logging
from datetime import date, timedelta

logger = logging.getLogger("minute_data")


async def fetch_5min_bars(ts_code: str, lookback_days: int = 20) -> list[dict] | None:
    """从 Tushare 拉取 5 分钟 K 线数据.

    Args:
        ts_code: 股票代码 (如 000001.SZ)
        lookback_days: 回看天数

    Returns:
        [{time, open, close, high, low, vol, amount}, ...] 或 None (失败时)
    """
    import httpx, os
    from dotenv import load_dotenv

    load_dotenv('C:/AI-Agent-Local/Stock/backend/.env')
    TOKEN = os.getenv('TUSHARE_TOKEN')
    if not TOKEN:
        logger.warning("TUSHARE_TOKEN not configured")
        return None

    end_dt = date.today().strftime('%Y-%m-%d')
    start_dt = (date.today() - timedelta(days=lookback_days + 5)).strftime('%Y-%m-%d')

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post('https://api.tushare.pro', json={
                'api_name': 'stk_mins', 'token': TOKEN,
                'params': {
                    'ts_code': ts_code, 'freq': '5min',
                    'start_date': f'{start_dt} 09:00:00',
                    'end_date': f'{end_dt} 15:00:00',
                },
                'fields': 'ts_code,trade_time,open,close,high,low,vol,amount'
            })
            data = resp.json()
            if data.get('code') != 0:
                logger.warning(f"Tushare stk_mins failed for {ts_code}: {data.get('msg', 'unknown')}")
                return None

        items = data.get('data', {}).get('items', []) or []
        bars = [{
            "time": item[1], "open": float(item[2]), "close": float(item[3]),
            "high": float(item[4]), "low": float(item[5]),
            "vol": float(item[6]), "amount": float(item[7]),
        } for item in items if len(item) >= 8]

        return bars
    except Exception as e:
        logger.warning(f"fetch_5min_bars failed for {ts_code}: {e}")
        return None
