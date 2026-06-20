"""扩展9维标签到全市场5500+股票 — 多数据源自动分类."""
import asyncio, sys
sys.path.insert(0, 'C:/AI-Agent-Local/Stock/backend')
from app.core.database import async_session_factory
from sqlalchemy import text

def classify_d1(ts_code):
    """D1 交易市场: 代码前缀自动判定."""
    if ts_code.endswith('.SH'):
        if ts_code.startswith('688'): return '科创板'
        return '上海主板'
    if ts_code.endswith('.SZ'):
        if ts_code.startswith('300') or ts_code.startswith('301'): return '创业板'
        if ts_code.startswith('002') or ts_code.startswith('003'): return '中小板'
        return '深圳主板'
    if ts_code.endswith('.BJ'): return '北交所'
    return None

def classify_d2(mkt_cap, close_price, pe, pb):
    """D2 市值规模与风格."""
    tags = []
    if mkt_cap:
        mv = float(mkt_cap) / 1e8  # 转亿元
        if mv > 10000: tags.append('超大盘')
        elif mv > 1000: tags.append('大盘')
        elif mv > 200: tags.append('中盘')
        elif mv > 50: tags.append('小盘')
        else: tags.append('微盘')
    if close_price:
        p = float(close_price)
        if p > 80: tags.append('百元股')
        elif p < 5: tags.append('低价股')
    if pe is not None:
        pe_v = float(pe) if pe else 0
        if pe_v < 0: tags.append('负市盈率')
        elif pe_v < 10: tags.append('低市盈率')
    if pb is not None:
        pb_v = float(pb) if pb else 0
        if 0 < pb_v < 1: tags.append('破净股')
    return tags

async def main():
    async with async_session_factory() as s:
        # 清理旧标签（保留ths_member已覆盖的374只）
        r = await s.execute(text("SELECT COUNT(DISTINCT ts_code) FROM stock_dimension_tags"))
        existing = r.scalar()
        print(f'Existing: {existing} stocks tagged')

        # 获取全市场股票列表 + 最新基本面数据
        r = await s.execute(text("""
            SELECT DISTINCT ON (k.ts_code) k.ts_code,
                   db.total_mv, db.pe, db.pb, k.close as close_price
            FROM daily_kline k
            LEFT JOIN daily_basic db ON k.ts_code = db.ts_code AND db.trade_date = k.trade_date
            WHERE k.trade_date = (SELECT MAX(trade_date) FROM daily_kline)
            ORDER BY k.ts_code
        """))
        stocks = {row[0]: {'total_mv': row[1], 'pe': row[2], 'pb': row[3], 'close_price': row[4]}
                  for row in r.fetchall()}
        print(f'Total stocks in daily_kline: {len(stocks)}')

        # 融资融券标的
        r = await s.execute(text("SELECT DISTINCT ts_code FROM margin_trading"))
        margin_stocks = {row[0] for row in r.fetchall()}

        # 北向资金标的
        r = await s.execute(text("SELECT DISTINCT ts_code FROM hk_hold"))
        northbound_stocks = {row[0] for row in r.fetchall()}

        tagged = 0
        new_tags = 0
        for ts_code, info in stocks.items():
            tags_to_insert = []

            # D1 市场
            market = classify_d1(ts_code)
            if market:
                tags_to_insert.append(('market', 1, market))

            # D2 规模
            size_tags = classify_d2(info['total_mv'], info['close_price'], info['pe'], info['pb'])
            for t in size_tags:
                tags_to_insert.append(('size_style', 2, t))

            # D8 交易资格
            if ts_code in margin_stocks:
                tags_to_insert.append(('trading', 8, '融资融券标的'))
            if ts_code in northbound_stocks:
                tags_to_insert.append(('trading', 8, '沪股通标的' if ts_code.endswith('.SH') else '深股通标的'))

            if not tags_to_insert:
                continue

            async with async_session_factory() as s2:
                for dim_name, dim_num, tag_value in tags_to_insert:
                    await s2.execute(text("""
                        INSERT INTO stock_dimension_tags (ts_code, dim_name, dim_num, tag_value)
                        VALUES (:c, :d, :n, :t) ON CONFLICT DO NOTHING
                    """), {'c': ts_code, 'd': dim_name, 'n': dim_num, 't': tag_value})
                    new_tags += 1
                await s2.commit()
            tagged += 1
            if tagged % 500 == 0:
                print(f'  {tagged}/{len(stocks)} stocks, {new_tags} tags')

        print(f'Tagged {tagged} new stocks, {new_tags} total new tags')

        # 统计
        r = await s.execute(text("""
            SELECT dim_name, dim_num, COUNT(DISTINCT ts_code) as stocks, COUNT(*) as tags
            FROM stock_dimension_tags GROUP BY dim_name, dim_num ORDER BY dim_num
        """))
        print('\nFinal coverage:')
        for row in r.fetchall():
            print(f'  D{row[1]}: {row[0]:15s} {row[2]:5d} stocks, {row[3]:6d} tags')

asyncio.run(main())
