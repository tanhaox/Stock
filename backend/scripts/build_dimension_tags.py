"""9维标签体系构建 — 从 ths_member 迁移标签到 stock_dimension_tags."""
import asyncio, sys
sys.path.insert(0, 'C:/AI-Agent-Local/Stock/backend')
from app.core.database import async_session_factory
from sqlalchemy import text

TAG_RULES = [
    # 维度一: 交易市场
    ('上海主板', 'market', 1, '上海主板'), ('深圳主板', 'market', 1, '深圳主板'),
    ('创业板', 'market', 1, '创业板'), ('科创板', 'market', 1, '科创板'),
    ('北交所', 'market', 1, '北交所'), ('ST板', 'market', 1, 'ST板'),
    # 维度二: 市值规模与风格
    ('超大盘', 'size_style', 2, '超大盘'), ('大盘', 'size_style', 2, '大盘'),
    ('中盘', 'size_style', 2, '中盘'), ('小盘', 'size_style', 2, '小盘'),
    ('微盘', 'size_style', 2, '微盘'), ('百元', 'size_style', 2, '百元股'),
    ('低价', 'size_style', 2, '低价股'), ('仙股', 'size_style', 2, '仙股'),
    ('高贝塔', 'size_style', 2, '高贝塔'), ('低波动', 'size_style', 2, '低波动'),
    ('近期新高', 'size_style', 2, '近期新高'), ('近期新低', 'size_style', 2, '近期新低'),
    ('连续上涨', 'size_style', 2, '连续上涨'), ('均线多头', 'size_style', 2, '均线多头'),
    ('破净', 'size_style', 2, '破净股'), ('低市盈率', 'size_style', 2, '低市盈率'),
    ('等权', 'size_style', 2, '等权重'),
    # 维度三: 指数成分
    ('上证50', 'index', 3, '上证50'), ('沪深300', 'index', 3, '沪深300'),
    ('中证500', 'index', 3, '中证500'), ('中证1000', 'index', 3, '中证1000'),
    ('国证2000', 'index', 3, '国证2000'), ('科创50', 'index', 3, '科创50'),
    ('创业板指', 'index', 3, '创业板指'), ('中证红利', 'index', 3, '中证红利'),
    ('MSCI', 'index', 3, 'MSCI成分'), ('富时', 'index', 3, '富时成分'),
    ('标普', 'index', 3, '标普成分'), ('罗素', 'index', 3, '罗素成分'),
    # 维度四: 申万行业
    ('农林牧渔', 'sector', 4, '农林牧渔'), ('基础化工', 'sector', 4, '基础化工'),
    ('钢铁', 'sector', 4, '钢铁'), ('有色金属', 'sector', 4, '有色金属'),
    ('电子', 'sector', 4, '电子'), ('汽车', 'sector', 4, '汽车'),
    ('家用电器', 'sector', 4, '家用电器'), ('食品饮料', 'sector', 4, '食品饮料'),
    ('纺织服饰', 'sector', 4, '纺织服饰'), ('轻工制造', 'sector', 4, '轻工制造'),
    ('医药生物', 'sector', 4, '医药生物'), ('公用事业', 'sector', 4, '公用事业'),
    ('交通运输', 'sector', 4, '交通运输'), ('房地产', 'sector', 4, '房地产'),
    ('商贸零售', 'sector', 4, '商贸零售'), ('社会服务', 'sector', 4, '社会服务'),
    ('银行', 'sector', 4, '银行'), ('非银金融', 'sector', 4, '非银金融'),
    ('建筑材料', 'sector', 4, '建筑材料'), ('建筑装饰', 'sector', 4, '建筑装饰'),
    ('电力设备', 'sector', 4, '电力设备'), ('国防军工', 'sector', 4, '国防军工'),
    ('计算机', 'sector', 4, '计算机'), ('传媒', 'sector', 4, '传媒'),
    ('通信', 'sector', 4, '通信'), ('机械设备', 'sector', 4, '机械设备'),
    ('煤炭', 'sector', 4, '煤炭'), ('石油石化', 'sector', 4, '石油石化'),
    ('环保', 'sector', 4, '环保'), ('美容护理', 'sector', 4, '美容护理'),
    ('综合', 'sector', 4, '综合'),
    # 维度六: 机构持仓
    ('QFII重仓', 'institution', 6, 'QFII重仓'), ('机构重仓', 'institution', 6, '机构重仓'),
    ('基金重仓', 'institution', 6, '基金重仓'), ('券商重仓', 'institution', 6, '券商重仓'),
    ('社保重仓', 'institution', 6, '社保重仓'), ('保险重仓', 'institution', 6, '保险重仓'),
    ('陆股通', 'institution', 6, '北向资金'), ('阳光私募', 'institution', 6, '阳光私募'),
    ('养老金', 'institution', 6, '养老金'),
    # 维度七: 地域
    ('北京', 'region', 7, '北京'), ('上海', 'region', 7, '上海'),
    ('天津', 'region', 7, '天津'), ('重庆', 'region', 7, '重庆'),
    ('广东', 'region', 7, '广东'), ('浙江', 'region', 7, '浙江'),
    ('江苏', 'region', 7, '江苏'), ('山东', 'region', 7, '山东'),
    ('福建', 'region', 7, '福建'), ('四川', 'region', 7, '四川'),
    ('湖北', 'region', 7, '湖北'), ('湖南', 'region', 7, '湖南'),
    ('安徽', 'region', 7, '安徽'), ('河南', 'region', 7, '河南'),
    ('河北', 'region', 7, '河北'), ('陕西', 'region', 7, '陕西'),
    ('辽宁', 'region', 7, '辽宁'), ('江西', 'region', 7, '江西'),
    ('长三角', 'region', 7, '长三角'), ('珠三角', 'region', 7, '珠三角'),
    ('京津冀', 'region', 7, '京津冀'), ('成渝', 'region', 7, '成渝'),
    ('海南', 'region', 7, '海南自贸港'), ('粤港澳', 'region', 7, '粤港澳'),
    # 维度八: 交易资格
    ('融资融券', 'trading', 8, '融资融券标的'), ('深股通', 'trading', 8, '深股通标的'),
    ('沪股通', 'trading', 8, '沪股通标的'), ('转融券', 'trading', 8, '转融券标的'),
    # 维度九: 公司属性
    ('央企', 'governance', 9, '央企'), ('地方国企', 'governance', 9, '地方国企'),
    ('中特估', 'governance', 9, '中特估'),
]


async def main():
    async with async_session_factory() as s:
        await s.execute(text('''
            CREATE TABLE IF NOT EXISTS stock_dimension_tags (
                id SERIAL PRIMARY KEY, ts_code varchar NOT NULL,
                dim_name varchar NOT NULL, dim_num int NOT NULL,
                tag_value varchar NOT NULL,
                UNIQUE (ts_code, dim_name, tag_value)
            )
        '''))
        await s.execute(text('CREATE INDEX IF NOT EXISTS idx_sdt_dim ON stock_dimension_tags (dim_name, tag_value)'))
        await s.execute(text('CREATE INDEX IF NOT EXISTS idx_sdt_code ON stock_dimension_tags (ts_code)'))
        await s.commit()

        r = await s.execute(text("SELECT ts_code, STRING_AGG(ths_name,',') as concepts FROM ths_member WHERE out_date IS NULL GROUP BY ts_code"))
        rows = r.fetchall()
        print(f'Processing {len(rows)} stocks...')

    inserted = 0
    tag_count = 0
    for i, row in enumerate(rows):
        ts_code = row[0]
        concepts = (row[1] or '').split(',')
        async with async_session_factory() as s2:
            await s2.execute(text('DELETE FROM stock_dimension_tags WHERE ts_code=:c'), {'c': ts_code})
            for concept in concepts:
                c = concept.strip()
                if not c: continue
                matched = False
                for keyword, dim_name, dim_num, tag_value in TAG_RULES:
                    if keyword in c:
                        await s2.execute(text('INSERT INTO stock_dimension_tags (ts_code, dim_name, dim_num, tag_value) VALUES (:c, :d, :n, :t) ON CONFLICT DO NOTHING'),
                                         {'c': ts_code, 'd': dim_name, 'n': dim_num, 't': tag_value})
                        tag_count += 1
                        matched = True
                if not matched and len(c) > 2:
                    await s2.execute(text('INSERT INTO stock_dimension_tags (ts_code, dim_name, dim_num, tag_value) VALUES (:c, :d, :n, :t) ON CONFLICT DO NOTHING'),
                                     {'c': ts_code, 'd': 'theme', 'n': 5, 't': c})
                    tag_count += 1
            await s2.commit()
        inserted += 1
        if inserted % 50 == 0:
            print(f'  {inserted}/{len(rows)} stocks, {tag_count} tags')

    async with async_session_factory() as s:
        r = await s.execute(text('SELECT dim_name, dim_num, COUNT(DISTINCT tag_value) as tags, COUNT(*) as total FROM stock_dimension_tags GROUP BY dim_name, dim_num ORDER BY dim_num'))
        print(f'\nDone: {inserted} stocks, {tag_count} tags\n')
        for row in r.fetchall():
            print(f'  D{row[1]}: {row[0]:15s} {row[2]:4d} unique, {row[3]:6d} total')

asyncio.run(main())
