#!/usr/bin/env python3
"""Build stock_sector_map — 固化的股票→板块映射表 (Phase 28).

三级填充策略按优先级:
  Level 1: ths_member 直接查询 (覆盖 ~374 只股票的 THS 概念)
  Level 2: 关键词匹配兜底 (stock_name_cache → KEYWORDS → SW)
  Level 3: 默认 SSW 代码 (000034.SH 上证工业)

同时写入 SSE 代码映射 (28 SW → SSE 全覆盖).

Usage: PYTHONPATH=. python scripts/build_stock_sector_map.py
"""
import asyncio, logging, re
from datetime import date, datetime
from collections import Counter
from app.core.database import async_session_factory
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("build_sector_map")

# SW → SSE 行业指数映射 (28 行业全覆盖)
SW_TO_SSE = {
    "801010.SI": "000034.SH",  # 农林牧渔 → 上证工业 (无直接农业指数)
    "801020.SI": "000034.SH",  # 采掘 → 上证工业
    "801030.SI": "000034.SH",  # 化工 → 上证工业
    "801040.SI": "000033.SH",  # 钢铁 → 上证材料
    "801050.SI": "000033.SH",  # 有色金属 → 上证材料
    "801080.SI": "000039.SH",  # 电子 → 上证信息
    "801110.SI": "000035.SH",  # 家用电器 → 上证可选
    "801120.SI": "000036.SH",  # 食品饮料 → 上证消费
    "801130.SI": "000035.SH",  # 纺织服饰 → 上证可选
    "801140.SI": "000034.SH",  # 轻工制造 → 上证工业
    "801150.SI": "000037.SH",  # 医药生物 → 上证医药
    "801160.SI": "000041.SH",  # 公用事业 → 上证公用
    "801170.SI": "000034.SH",  # 交通运输 → 上证工业
    "801180.SI": "000006.SH",  # 房地产 → 地产指数
    "801200.SI": "000005.SH",  # 商贸零售 → 商业指数
    "801210.SI": "000035.SH",  # 社会服务 → 上证可选
    "801230.SI": "000008.SH",  # 综合 → 综合指数
    "801710.SI": "000034.SH",  # 建筑材料 → 上证工业
    "801720.SI": "000034.SH",  # 建筑装饰 → 上证工业
    "801730.SI": "000034.SH",  # 电力设备 → 上证工业
    "801740.SI": "000034.SH",  # 国防军工 → 上证工业
    "801750.SI": "000039.SH",  # 计算机 → 上证信息
    "801760.SI": "000040.SH",  # 传媒 → 上证电信
    "801770.SI": "000040.SH",  # 通信 → 上证电信
    "801780.SI": "000038.SH",  # 银行 → 上证金融
    "801790.SI": "000038.SH",  # 非银金融 → 上证金融
    "801880.SI": "000035.SH",  # 汽车 → 上证可选
    "801890.SI": "000034.SH",  # 机械设备 → 上证工业
}

# 关键词→SW 映射 (与 sanxian.py _find_sector 保持一致)
KEYWORDS = {
    "汽车": "801880.SI", "比亚迪": "801880.SI", "新能源": "801730.SI",
    "电池": "801730.SI", "宁德": "801730.SI", "半导体": "801080.SI",
    "芯片": "801080.SI", "医药": "801150.SI", "医疗": "801150.SI",
    "银行": "801780.SI", "证券": "801790.SI", "保险": "801790.SI",
    "钢铁": "801040.SI", "煤炭": "801950.SI", "有色": "801050.SI",
    "化工": "801030.SI", "地产": "801180.SI", "建筑": "801720.SI",
    "建材": "801710.SI", "食品": "801120.SI", "饮料": "801120.SI",
    "酒": "801120.SI", "家电": "801110.SI", "电力": "801160.SI",
    "交通": "801170.SI", "运输": "801170.SI", "航空": "801170.SI",
    "军工": "801740.SI", "船舶": "801740.SI", "软件": "801750.SI",
    "计算机": "801750.SI", "传媒": "801760.SI", "通信": "801770.SI",
    "5G": "801770.SI", "环保": "801970.SI", "机械": "801890.SI",
    "设备": "801890.SI", "石油": "801960.SI", "农业": "801010.SI",
    "牧渔": "801010.SI", "旅游": "801210.SI", "零售": "801200.SI",
    "商贸": "801200.SI", "服装": "801130.SI", "纺织": "801130.SI",
    "港口": "801170.SI", "物流": "801170.SI", "铁路": "801170.SI",
    "高速": "801170.SI", "机场": "801170.SI", "采掘": "801020.SI",
    "矿业": "801020.SI", "燃料": "801020.SI", "家居": "801110.SI",
    "包装": "801140.SI", "造纸": "801140.SI", "印刷": "801140.SI",
    "园林": "801720.SI", "装修": "801720.SI", "检测": "801890.SI",
    "仪器": "801890.SI", "电气": "801730.SI", "燃气": "801160.SI",
    "水务": "801160.SI", "供热": "801160.SI",
}

SW_NAMES = {
    "801010.SI": "农林牧渔", "801020.SI": "采掘", "801030.SI": "化工",
    "801040.SI": "钢铁", "801050.SI": "有色金属", "801080.SI": "电子",
    "801110.SI": "家用电器", "801120.SI": "食品饮料", "801130.SI": "纺织服饰",
    "801140.SI": "轻工制造", "801150.SI": "医药生物", "801160.SI": "公用事业",
    "801170.SI": "交通运输", "801180.SI": "房地产", "801200.SI": "商贸零售",
    "801210.SI": "社会服务", "801230.SI": "综合", "801710.SI": "建筑材料",
    "801720.SI": "建筑装饰", "801730.SI": "电力设备", "801740.SI": "国防军工",
    "801750.SI": "计算机", "801760.SI": "传媒", "801770.SI": "通信",
    "801780.SI": "银行", "801790.SI": "非银金融", "801880.SI": "汽车",
    "801890.SI": "机械设备", "801950.SI": "煤炭", "801960.SI": "石油石化",
    "801970.SI": "环保", "801980.SI": "美容护理",
}


def _match_sw(name: str) -> str | None:
    """关键词匹配 → SW 行业代码."""
    if not name:
        return None
    # Exact keyword match (longest first)
    sorted_kw = sorted(KEYWORDS.keys(), key=len, reverse=True)
    for kw in sorted_kw:
        if kw in name:
            return KEYWORDS[kw]
    return None


def _match_sw_from_list(names: list[str]) -> str | None:
    """从名称列表中匹配 SW 代码."""
    for name in names:
        if not name:
            continue
        # Direct keyword
        code = _match_sw(name)
        if code:
            return code
        # Substring match against SW_NAMES
        clean = re.sub(r'[（(].*|[ⅢⅡⅠA-Z0-9]', '', name)
        for sw_code, sw_name in SW_NAMES.items():
            if clean[:2] in sw_name or sw_name[:2] in clean:
                return sw_code
    return None


async def main():
    today = date.today()

    # ── Ensure table exists ──
    async with async_session_factory() as s:
        await s.execute(text("""
            CREATE TABLE IF NOT EXISTS stock_sector_map (
                ts_code VARCHAR(20) PRIMARY KEY,
                stock_name VARCHAR(50),
                sw_code VARCHAR(20),
                sw_name VARCHAR(20),
                sse_code VARCHAR(20),
                ths_code VARCHAR(20),
                source VARCHAR(20),
                updated_at TIMESTAMPTZ
            )"""))
        await s.commit()
    logger.info("Table stock_sector_map ready")

    # ── Level 1: ths_member direct mapping ──
    level1: dict[str, dict] = {}
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT ts_code, ths_code, ths_name FROM ths_member WHERE out_date IS NULL"
        ))
        for row in r.fetchall():
            code, ths_code, ths_name = row[0], row[1], row[2]
            if not code:
                continue
            # Best SW match from THS name
            sw = _match_sw(ths_name or "") if ths_name else None
            if not sw:
                sw = _match_sw_from_list([ths_name or ""])
            if sw and code not in level1:
                level1[code] = {
                    "sw_code": sw,
                    "sw_name": SW_NAMES.get(sw, ""),
                    "ths_code": ths_code,
                    "source": "ths_member",
                }
    logger.info(f"Level 1 (ths_member): {len(level1)} stocks mapped")

    # ── Level 2: stock_name_cache keyword matching ──
    level2: dict[str, dict] = {}
    names_map: dict[str, str] = {}
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT symbol, name FROM stock_name_cache"
        ))
        for row in r.fetchall():
            if row[0] and row[1]:
                names_map[row[0]] = row[1]

    for code in names_map:
        if code in level1:
            continue  # already covered
        name = names_map[code]
        sw = _match_sw(name)
        if not sw:
            sw = _match_sw_from_list([name])
        if sw:
            level2[code] = {
                "sw_code": sw,
                "sw_name": SW_NAMES.get(sw, ""),
                "ths_code": None,
                "source": "keyword",
            }
    logger.info(f"Level 2 (keyword): {len(level2)} stocks mapped")

    # ── Level 3: Cover stock_tags and daily_kline stocks ──
    level3: dict[str, dict] = {}
    all_codes: set[str] = set()

    async with async_session_factory() as s:
        r = await s.execute(text("SELECT ts_code FROM stock_tags"))
        all_codes.update(row[0] for row in r.fetchall() if row[0])

        r = await s.execute(text("SELECT DISTINCT ts_code FROM daily_kline"))
        all_codes.update(row[0] for row in r.fetchall() if row[0])

    for code in all_codes:
        if code in level1 or code in level2:
            continue
        name = names_map.get(code, code)
        sw = _match_sw(name)
        if not sw:
            sw = _match_sw_from_list([name])
        sw = sw or "801890.SI"  # default: 机械设备
        level3[code] = {
            "sw_code": sw,
            "sw_name": SW_NAMES.get(sw, "机械设备"),
            "ths_code": None,
            "source": "default",
        }
    logger.info(f"Level 3 (default): {len(level3)} stocks mapped")

    # ── Batch INSERT with SSE codes ──
    sources = Counter()
    async with async_session_factory() as s:
        for code, info in {**level1, **level2, **level3}.items():
            sw_code = info["sw_code"]
            sw_name = info["sw_name"]
            ths_code = info.get("ths_code")
            src = info["source"]
            sse = SW_TO_SSE.get(sw_code, "000034.SH")
            stock_name = names_map.get(code, code)

            await s.execute(text("""
                INSERT INTO stock_sector_map
                    (ts_code, stock_name, sw_code, sw_name, sse_code, ths_code, source, updated_at)
                VALUES (:c, :n, :sw, :swn, :sse, :ths, :src, NOW())
                ON CONFLICT (ts_code) DO UPDATE SET
                    stock_name=EXCLUDED.stock_name, sw_code=EXCLUDED.sw_code,
                    sw_name=EXCLUDED.sw_name, sse_code=EXCLUDED.sse_code,
                    ths_code=EXCLUDED.ths_code, source=EXCLUDED.source,
                    updated_at=NOW()
            """), {
                "c": code, "n": stock_name or code,
                "sw": sw_code, "swn": sw_name,
                "sse": sse, "ths": ths_code,
                "src": src,
            })
            sources[src] += 1
        await s.commit()

    total = sum(sources.values())
    logger.info(f"\n=== Complete: {total} stocks ===")
    for src, cnt in sources.most_common():
        logger.info(f"  {src}: {cnt} ({cnt/total*100:.0f}%)")

    # ── Verify BYD ──
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT ts_code, stock_name, sw_code, sw_name, sse_code FROM stock_sector_map WHERE ts_code='002594.SZ'"
        ))
        row = r.fetchone()
        if row:
            logger.info(f"  BYD: sw={row[2]} {row[3]} sse={row[4]}")
        else:
            logger.warning("  BYD: NOT FOUND!")


if __name__ == "__main__":
    asyncio.run(main())
