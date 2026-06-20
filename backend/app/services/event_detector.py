"""新闻事件分析引擎 v4.6 — 仅保留公司级事件分析.

宏观/政策/商品分析已由 macro_data + factor_exposure + fut_daily 接管.
Stage 1: LLM 打标签 (公司事件分类, 5类)
Stage 2: 公司事件深度分析 (LLM 结构化提取)
Stage 3: 去重+入库 stock_events

设计原则: LLM标签并行(DeepSeek 500并发), 每步可追踪, 失败自动重试.
"""

import asyncio, json, logging, re
from datetime import date, datetime, timedelta, timezone
from collections import defaultdict
from sqlalchemy import text
from app.core.database import async_session_factory
from app.services.deepseek import call_deepseek
from app.core.config import settings

logger = logging.getLogger(__name__)

# ── 分类体系 (M-5: 仅保留公司级, 宏观/政策/商品已退役由 macro_data 接管) ─────────────────

TAG_TO_SYSTEM = {
    "company_announcement": "stock", "stock_market": "stock",
    "leaderboard": "stock", "tech_innovation": "stock",
    # M-5: 以下已退役 — "energy"/"commodity_metal"/"macro_policy"/"trade_agreement"/
    #   "regulation_legal"/"infrastructure"/"geopolitics"/"currency_bond"/
    #   "climate_environment"/"society_health"/"market_summary"
}

_STANDARD_SECTORS = {
    "传媒","公用事业","基础化工","家用电器","建筑材料","建筑装饰",
    "房地产","有色金属","机械设备","汽车","煤炭","电子","石油石化",
    "社会服务","综合","计算机","通信","钢铁","银行","食品饮料",
}

_SECTOR_ALIAS = {
    "AI":"计算机","人工智能":"计算机","芯片":"电子","半导体":"电子",
    "新能源":"公用事业","光伏":"公用事业","风电":"公用事业",
    "新能源车":"汽车","白酒":"食品饮料","消费":"食品饮料",
    "养殖":"农林牧渔","养殖业":"农林牧渔","农业":"农林牧渔",
    "养殖板块":"农林牧渔","农产品":"农林牧渔","农产品板块":"农林牧渔",
    "农产品加工":"农林牧渔","油脂":"农林牧渔",
    "油服":"石油石化","油服板块":"石油石化","石油开采":"石油石化",
    "石油开采板块":"石油石化","石油化工":"石油石化","石油化工板块":"石油石化",
    "石化":"石油石化",
    "煤炭开采":"煤炭","煤炭板块":"煤炭","钢铁板块":"钢铁",
    "化工":"基础化工","化工板块":"基础化工","化肥":"基础化工",
    "磷肥板块":"基础化工",
    "电力":"公用事业","电力设备":"电气设备","氢能":"公用事业",
    "医药":"医药生物","创新药":"医药生物","医疗器械":"医药生物",
    "橡胶":"基础化工","橡胶板块":"基础化工",
    "铝":"有色金属","铝业":"有色金属","铝板块":"有色金属",
    "铜":"有色金属","铜业":"有色金属","铜板块":"有色金属",
    "贵金属":"有色金属","黄金板块":"有色金属","白银板块":"有色金属",
    "铂金板块":"有色金属","天然气":"石油石化","天然气板块":"石油石化",
    "黄金":"有色金属","白银":"有色金属","钯金":"有色金属",
    "铅锌":"有色金属","锡":"有色金属","多晶硅":"有色金属",
    "能源":"石油石化","航运":"交通运输",
    "玻璃":"建筑材料","建材":"建筑材料","玻璃陶瓷":"建筑材料",
    "水泥建材":"建筑材料",
    "纺织":"纺织服饰","油脂":"农林牧渔",
    "汽车零部件":"汽车","锂电":"电气设备","锂电板块":"电气设备",
    "交通运输":"交通运输","房地产":"房地产","银行":"银行",
}

def _norm_sector(s: str) -> str:
    if s in _STANDARD_SECTORS: return s
    return _SECTOR_ALIAS.get(s, s)


def _repair_json(raw: str) -> dict | list:
    """修复常见的 LLM JSON 格式错误."""
    raw = raw.strip()
    # 提取JSON块
    if raw.startswith('['):
        m = re.search(r'\[.*\]', raw, re.DOTALL)
    else:
        m = re.search(r'\{.*\}', raw, re.DOTALL)
    if not m: raise ValueError("No JSON found")
    fixed = m.group(0)
    # 修复
    fixed = re.sub(r',\s*]', ']', fixed)
    fixed = re.sub(r',\s*}', '}', fixed)
    fixed = re.sub(r'}\s*{', '},{', fixed)
    fixed = re.sub(r'\]\s*\[', '],[', fixed)
    fixed = re.sub(r'"\s*"', '","', fixed)  # 缺失逗号: "" → ","
    return json.loads(fixed)


# ── Stage 1: 标签 ────────────────────────────

STAGE1_PROMPT = """分析以下财经新闻, 为每条新闻打1个最匹配的分类标签.

标签(5类):
company_announcement(公司公告:合同/财报/增减持/重组)
stock_market(股票市场:股指涨跌/资金流向)
tech_innovation(科技创新:AI/芯片/5G/航天)
leaderboard(龙虎榜:席位/资金)
garbage(垃圾:无关)
    
输出JSON数组: [{{"idx":序号,"tag":"标签名","codes":["600660.SH"]}}]
只输出JSON. 新闻中的股票代码(.SZ/.SH或(代码)格式)提取到codes."""
# ↑ 已迁移到 app.prompts.event_detection — 保留副本以兼容, 后续统一改为:
# from app.prompts.event_detection import STAGE1_PROMPT, STAGE2_PROMPTS


async def _tag_news_batch(news_items: list[dict]) -> list[dict]:
    """Stage 1: LLM 为每条新闻打标签 (自动重试, 优化输入长度)."""
    if not news_items: return []
    # 缩短输入: 150字足够LLM判断分类, 减少token消耗和响应时间
    lines = [f"[{i}] {item['content'][:150]}" for i, item in enumerate(news_items)]
    prompt = STAGE1_PROMPT + "\n\n新闻:\n" + "\n".join(lines)

    logger.info(f"Stage1: tagging {len(news_items)} items, {len(prompt)} chars")
    for attempt in range(2):  # 调用方重试: 处理 JSON 解析失败 (网络重试由 deepseek.py 处理)
        try:
            raw = await call_deepseek(prompt, max_tokens=16384)
            if raw.startswith('[LLM'): raise ValueError(raw)
            data = _repair_json(raw)
            if not isinstance(data, list): raise ValueError("Not an array")
            tag_map = {item.get("idx", -1): item for item in data if isinstance(item, dict)}
            for i, item in enumerate(news_items):
                tag_info = tag_map.get(i, {})
                item["tag"] = tag_info.get("tag", "garbage")
                # 验证codes: LLM可能幻觉默认值600660.SH, 只保留原文中出现的代码
                raw_codes = tag_info.get("codes", [])
                content = item["content"]
                validated = [c for c in raw_codes if c[:6] in content or c in content]
                # 也检查裸代码格式 e.g. (600660)
                for c in raw_codes:
                    if c not in validated:
                        bare = c[:6]
                        if f"({bare})" in content:
                            validated.append(c)
                item["codes"] = validated
            logger.info(f"Stage1 batch done: {len(data)} tags")
            return news_items
        except Exception as e:
            logger.warning(f"Stage1 attempt {attempt+1}/2 failed: {e}")
            if attempt < 1: await asyncio.sleep(2)  # 缩短重试间隔
    logger.error("Stage1 batch failed after 2 attempts, returning untagged")
    for item in news_items: item["tag"] = "garbage"
    return news_items


async def _tag_all_batches_parallel(batches: list[list[dict]], progress_cb=None) -> list[dict]:
    """并行打标签: semaphore(8) 控制并发, DeepSeek并发上限500绰绰有余."""
    sem = asyncio.Semaphore(8)
    total = len(batches)
    all_tagged = []

    async def _tag_one(bi: int, batch: list[dict]) -> list[dict]:
        async with sem:
            logger.info(f"Stage1 batch {bi+1}/{total} ({len(batch)} items)...")
            if progress_cb:
                await progress_cb("stage1_tag", bi+1, total, f"LLM打标签 {bi+1}/{total}批")
            try:
                # 单批超时 60s, 超时则跳过该批
                result = await asyncio.wait_for(_tag_news_batch(batch), timeout=120)
                logger.info(f"Stage1 batch {bi+1}/{total} done: {len(result)} tagged")
                return result
            except asyncio.TimeoutError:
                logger.error(f"Stage1 batch {bi+1}/{total} TIMEOUT after 60s, skipping")
                for item in batch: item["tag"] = "garbage"
                return batch
            except Exception as e:
                logger.error(f"Stage1 batch {bi+1}/{total} failed: {e}")
                for item in batch: item["tag"] = "garbage"
                return batch

    tasks = [_tag_one(i, batch) for i, batch in enumerate(batches)]
    results = await asyncio.gather(*tasks)
    for r in results:
        all_tagged.extend(r)
    return all_tagged


# ── Stage 2: 深度分析 ────────────────────────

STAGE2_PROMPTS = {
    # M-5: 仅保留 stock 深度分析 (policy/commodity/macro 已由 macro_data 接管)
    "stock": """分析个股新闻, 提取结构化事件。每只股票最多一条, 同股冲突以最新消息为准, 只从含代码的新闻提取。
格式: {{"events":[{{"ts_code":"600660.SH","direction":"bullish|bearish|neutral","scores":{{"materiality":0-5,"immediacy":0-5,"certainty":0-5,"scope":0-5}},"composite_impact":0.0-5.0,"title":"标题","summary":"影响","related_sectors":["板块"]}}]}}
板块限选: 传媒,公用事业,基础化工,家用电器,建筑材料,建筑装饰,房地产,有色金属,机械设备,汽车,煤炭,电子,石油石化,社会服务,综合,计算机,通信,钢铁,银行,食品饮料
只输出JSON。新闻:""",
}


async def _analyze_category(category: str, items: list[dict]) -> dict:
    """Stage 2: 对同一分类新闻进行深度分析 (串行, 自动重试)."""
    if not items: return {}
    # 去重
    seen = set()
    unique = []
    for item in items:
        key = item["content"][:100]
        if key not in seen: seen.add(key); unique.append(item)

    prompt_key = category
    if prompt_key not in STAGE2_PROMPTS: return {}

    # 限制条目 + 缩短输入: 100条×150字 = 15k字符 (原来200条×300字=60k)
    lines = [item["content"][:150] for item in unique[:100]]
    prompt = STAGE2_PROMPTS[prompt_key] + "\n" + "\n".join(lines)

    logger.info(f"Stage2 [{category}]: {len(unique)} items (using {len(lines)}), {len(prompt)} chars")
    for attempt in range(2):  # 调用方重试: 处理 JSON 解析失败 (网络重试由 deepseek.py 处理)
        try:
            raw = await call_deepseek(prompt, max_tokens=8192, model=settings.DEEPSEEK_PRO_MODEL if settings.DEEPSEEK_PRO_MODEL else None)
            if raw.startswith('[LLM'): raise ValueError(raw)
            result = _repair_json(raw)
            if not isinstance(result, dict): raise ValueError("Not a dict")
            ev_count = len(result.get("events", []))
            si_count = len(result.get("sector_impacts", []))
            ci_count = len(result.get("commodity_impacts", []))
            mf_count = len(result.get("macro_factors", []))
            logger.info(f"Stage2 [{category}] done: events={ev_count} si={si_count} ci={ci_count} mf={mf_count}")
            return result
        except Exception as e:
            logger.warning(f"Stage2 [{category}] attempt {attempt+1}/2 failed: {e}")
            if attempt < 1: await asyncio.sleep(3)
    logger.error(f"Stage2 [{category}] failed after 2 attempts")
    return {}


# ── Stage 3: 入库 ────────────────────────────

async def _store_results(today: date, stage2_results: dict) -> dict:
    """Stage 3: 去重+入库, 每条独立事务. HK股票自动映射为A股代码."""
    import re as _re
    event_count = 0
    sector_count = 0

    # 预加载A股名称映射 (用于HK→A股匹配)
    a_stock_names = {}
    try:
        async with async_session_factory() as s:
            r = await s.execute(text(
                "SELECT symbol, name FROM stock_name_cache WHERE symbol NOT LIKE '%.HK'"))
            a_stock_names = {row[0]: row[1] for row in r.fetchall()}
    except Exception:
        pass

    def _hk_to_a(hk_code: str, title: str) -> str | None:
        """通过公司名匹配HK代码到A股代码."""
        m = _re.match(r'([一-鿿]{2,4})', title)
        if not m: return None
        company = m.group(1)
        skip = {'今日','昨日','明天','本周','上周','早盘','午盘','收盘','开盘','涨停','跌停','市场','大盘','上证','深证','创业板','科创','北向','主力','游资','机构','多家','部分','近期','今年','去年'}
        if company in skip: return None
        for a_code, a_name in a_stock_names.items():
            if company in a_name:
                return a_code
        return None

    # ── 个股事件 ──
    for cat in ["stock"]:
        data = stage2_results.get(cat, {})
        for e in data.get("events", []):
            ts_code = e.get("ts_code", "UNKNOWN")
            # 过滤境外代码: 仅保留A股 (.SZ/.SH/纯6位数字0/3/6开头)
            if not (ts_code.endswith('.SZ') or ts_code.endswith('.SH') or
                    (len(ts_code) == 6 and ts_code.isdigit() and ts_code[0] in '036')):
                continue
            # HK→A股转换: 如 00914.HK → 600585.SH
            if ts_code.endswith('.HK'):
                a_code = _hk_to_a(ts_code, e.get("title", ""))
                if a_code:
                    ts_code = a_code
            impact = float(e.get("composite_impact", 1.0))
            decay = 5 if impact >= 4.0 else (3 if impact >= 2.0 else 1)
            try:
                async with async_session_factory() as s:
                    await s.execute(text("""
                        INSERT INTO stock_events (ts_code,event_date,category,direction,
                            materiality,immediacy,certainty,scope,composite_impact,
                            title,summary,related_sectors,decay_days)
                        VALUES (:ts,:d,:cat,:dir,:mat,:imm,:cer,:sco,:imp,:t,:sum,:sec,:dec)
                        ON CONFLICT (ts_code, event_date, title) DO NOTHING
                    """), {
                        "ts": ts_code, "d": today, "cat": "company",
                        "dir": e.get("direction", "neutral"),
                        "mat": int(e.get("scores", {}).get("materiality", 0)),
                        "imm": int(e.get("scores", {}).get("immediacy", 0)),
                        "cer": int(e.get("scores", {}).get("certainty", 0)),
                        "sco": int(e.get("scores", {}).get("scope", 0)),
                        "imp": impact, "t": e.get("title", "")[:300],
                        "sum": e.get("summary", "")[:500],
                        "sec": [_norm_sector(x) for x in e.get("related_sectors", [])],
                        "dec": decay,
                    })
                    await s.commit()
                event_count += 1
            except Exception as exc:
                logger.warning(f"Insert stock event failed: {exc}")

    # ── 板块影响 (去重: 同名板块保留第一条) ──
    deduped = {}
    for cat in ["policy", "commodity", "macro"]:
        data = stage2_results.get(cat, {})
        for si in data.get("sector_impacts", []):
            sec = _norm_sector(si.get("sector", ""))
            if not sec or sec in deduped: continue
            deduped[sec] = si
        for ci in data.get("commodity_impacts", []):
            for sn in ci.get("affected_sectors", []):
                sec = _norm_sector(sn)
                if not sec or sec in deduped: continue
                deduped[sec] = {"sector": sec, "direction": ci.get("direction","neutral"),
                    "composite_impact": ci.get("composite_impact",1.0),
                    "drivers": [ci.get("commodity","")], "prediction": ci.get("summary","")}

    for sec, si in deduped.items():
        try:
            async with async_session_factory() as s:
                await s.execute(text("""
                    INSERT INTO sector_events (sector,event_date,direction,composite_impact,drivers,prediction)
                    VALUES (:sec,:d,:dir,:imp,:drv,:pred)
                    ON CONFLICT (sector, event_date) DO NOTHING
                """), {
                    "sec": sec, "d": today, "dir": si.get("direction", "neutral"),
                    "imp": float(si.get("composite_impact", 1.0)),
                    "drv": si.get("drivers", []),
                    "pred": si.get("prediction", "")[:200],
                })
                await s.commit()
            sector_count += 1
        except Exception as exc:
            logger.warning(f"Insert sector failed: {exc}")

    # ── 宏观因子 (存入sector_events, sector='宏观') ──
    macro_factors = []
    macro_summary = ""
    for cat in ["macro"]:
        data = stage2_results.get(cat, {})
        for mf in data.get("macro_factors", []):
            factor_name = mf.get("factor", "宏观因素")
            impact = float(mf.get("composite_impact", 1.0))
            macro_factors.append({
                "factor": factor_name,
                "direction": mf.get("direction", "neutral"),
                "composite_impact": impact,
                "summary": mf.get("summary", "")[:200],
            })
            # 存入sector_events (sector=宏观, 每个因子一条)
            try:
                async with async_session_factory() as s:
                    await s.execute(text("""
                        INSERT INTO sector_events (sector,event_date,direction,composite_impact,drivers,prediction)
                        VALUES (:sec,:d,:dir,:imp,:drv,:pred)
                        ON CONFLICT (sector, event_date) DO NOTHING
                    """), {
                        "sec": f"宏观-{factor_name}", "d": today,
                        "dir": mf.get("direction", "neutral"),
                        "imp": impact,
                        "drv": [mf.get("factor", "")],
                        "pred": mf.get("summary", "")[:200],
                    })
                    await s.commit()
            except Exception:
                pass
        macro_summary = data.get("macro_summary", "")

    return {"stock_events": event_count, "sector_events": sector_count,
            "macro_factors": macro_factors, "macro_summary": macro_summary}


# ── 主入口 ────────────────────────────────────

async def analyze_all_sources(hours_back: int = 48, progress_cb=None) -> dict:
    """3阶段流水线: Tag -> Group -> Analyze -> Store. 完全串行, 每步可追踪.

    progress_cb(phase, current, total, extra) -- 可选进度回调.
    """
    # 加载新闻 (断点续传: 跳过已分析过的旧数据)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours_back)
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT MAX(created_at) FROM stock_events"))
        last_analysis = r.scalar()
    if last_analysis:
        # 确保UTC时区 (DB的TIMESTAMPTZ返回aware, 防御性处理naive情况)
        if getattr(last_analysis, 'tzinfo', None) is None:
            last_analysis = last_analysis.replace(tzinfo=timezone.utc)
        cutoff = max(cutoff, last_analysis - timedelta(hours=2))

    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT content, pub_time FROM news_raw WHERE pub_time >= :cutoff ORDER BY pub_time DESC"
        ), {"cutoff": cutoff})
        all_items = [{"content": row[0], "pub_time": str(row[1]) if row[1] else ""} for row in r.fetchall()]

    if not all_items:
        return {"status": "empty", "message": "No recent news"}

    # ── v4.7: 商品/宏观关键词过滤 ──
    # 期货、汇率、宏观指标已由 macro_data + fut_daily + fx_daily 精确覆盖，
    # 无需 LLM 重复分析（LLM 易虚构叙事，污染 sector_events 质量）。
    MACRO_COMMODITY_FILTER = [
        # 期货/商品 (macro_data 已通过 fut_daily 精确同步)
        '期货', '原油', '沪铜', '沪铝', '沪锌', '沪铅', '沪镍', '沪锡', '沪金', '沪银',
        '螺纹钢', '铁矿石', '焦煤', '焦炭', '动力煤', '碳酸锂', '工业硅',
        '甲醇', 'PVC', '聚丙烯', '聚乙烯', 'PTA', '乙二醇', '纯碱', '玻璃期货',
        '橡胶', '纸浆', '豆粕', '菜粕', '豆油', '棕榈油', '棉花', '白糖',
        '原油期货', '商品期货', '期货主力', '主力合约', '夜盘',
        'LME', 'COMEX', 'CBOT', 'NYMEX', 'ICE',
        # 汇率/外汇 (macro_data 已通过 fx_daily 同步)
        '人民币中间价', '人民币兑美元', '美元指数', '美元兑', '在岸人民币',
        '离岸人民币', 'CNY', 'USDCNY', '外汇储备', '结售汇',
        # 宏观指标 (macro_data 已同步)
        'SHIBOR', 'LPR利率', 'LPR报价', 'MLF利率', '逆回购利率',
        '国债收益率', '国债期货', '中美国债',
        'PMI数据', 'PMI指数', 'CPI数据', 'CPI同比', 'PPI数据', 'PPI同比',
        'GDP增速', 'GDP数据', 'M2增速', 'M2数据', '社融数据', '新增贷款',
        '融资余额', '北向资金',
        # 纯商品/外汇行情播报 (无公司/行业信息)
        'OPEC', 'EIA原油库存', 'API原油库存', '贝克休斯',
    ]
    # 保留白名单: 即使匹配关键词, 仍保留（含具体公司名/政策/行业举措）
    KEEP_WHITELIST = [
        '中标', '签约', '投产', '开工', '获批', '公告', '减持', '增持', '回购',
        '重组', '并购', '定增', 'IPO', '上市', '涨停', '跌停', '连板',
        '业绩', '营收', '净利润', '分红', '派息',
    ]

    def _should_skip(content: str) -> bool:
        """Check if content is pure commodity/macro noise (not company news)."""
        for kw in MACRO_COMMODITY_FILTER:
            if kw in content:
                # 如果同时含公司级白名单关键词 → 保留
                if any(w in content for w in KEEP_WHITELIST):
                    return False
                return True
        return False

    original_count = len(all_items)
    all_items = [it for it in all_items if not _should_skip(it["content"])]
    filtered = original_count - len(all_items)
    if filtered:
        logger.info(f"v4.7 filter: dropped {filtered}/{original_count} commodity/macro items "
                    f"(remaining: {len(all_items)})")

    if not all_items:
        return {"status": "empty", "message": "No non-commodity recent news"}

    logger.info(f"Pipeline start: {len(all_items)} items (since {cutoff})")

    # ── Stage 1: 并行标签 (250条/批, 8批并发) ──
    batch_size = 250
    batches = [all_items[i:i+batch_size] for i in range(0, len(all_items), batch_size)]
    logger.info(f"Stage1: {len(batches)} batches, {batch_size}/batch, 2 concurrent")

    all_tagged = await _tag_all_batches_parallel(batches, progress_cb)

    # 分组
    groups = defaultdict(list)
    for item in all_tagged:
        tag = item.get("tag", "garbage")
        sys_cat = TAG_TO_SYSTEM.get(tag, "macro")
        if tag != "garbage": groups[sys_cat].append(item)

    logger.info(f"Stage1 done: {len(all_tagged)} tagged → groups: {dict((k,len(v)) for k,v in groups.items())}")

    # ── Stage 2: 串行分析 (4类) ──
    stage2_results = {}
    stage2_cats = ["stock", "policy", "commodity", "macro"]
    for ci, cat in enumerate(stage2_cats):
        items = groups.get(cat, [])
        min_items = 1 if cat == "macro" else 3  # 宏观分析门槛低(宏观信息稀缺但重要)
        if len(items) >= min_items:
            if progress_cb:
                await progress_cb("stage2_analyze", ci+1, len(stage2_cats), f"LLM深度分析 [{cat}] {len(items)}条")
            result = await _analyze_category(cat, items)
            if result: stage2_results[cat] = result

    logger.info(f"Stage2 done: {list(stage2_results.keys())}")

    # ── Stage 3: 入库 ──
    today = date.today()
    stored = await _store_results(today, stage2_results)
    logger.info(f"Pipeline done: {stored}")

    return {
        "status": "success",
        "groups": dict((k, len(v)) for k, v in groups.items()),
        "analyzed_categories": list(stage2_results.keys()),
        "stored": stored,
    }


# ── M-5: 退役接口 (宏观/政策/商品已由 macro_data.py 接管) ────

async def score_event_impact(ts_code: str, target_date: date = None) -> float:
    """个股新闻事件影响 (连续值, 非离散桶).

    修复 #1: 直接使用 composite_impact × 衰减系数, 输出 -10~+10 连续值.
    """
    if target_date is None: target_date = date.today()
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT direction,composite_impact,event_date,decay_days FROM stock_events WHERE ts_code=:ts AND event_date>=:cutoff"
        ), {"ts": ts_code, "cutoff": target_date - timedelta(days=7)})
        rows = r.fetchall()
    if not rows: return 0.0
    total = 0.0
    for row in rows:
        days_ago = (target_date - row[2]).days if row[2] else 0
        if days_ago > int(row[3] or 3): continue
        decay = 1.0 - (days_ago / max(int(row[3] or 3), 1)) * 0.8
        mult = 1 if row[0] == "bullish" else (-1 if row[0] == "bearish" else 0)
        total += float(row[1] or 0) * mult * decay
    # 连续值输出, 钳制在 -10~+10
    return round(max(-10, min(10, total * 1.5)), 1)


async def score_sector_news(symbol: str, sector_events_cache: dict = None) -> float:
    """M-5: 退役, 由 factor_exposure.compute_sector_score() 接管."""
    return 0.0

async def get_macro_adjustment() -> float:
    """M-5: 退役, 由 macro_data.score_macro_impact() 接管."""
    from app.services.macro_data import score_macro_impact
    macro_adj, _ = await score_macro_impact()
    return macro_adj

async def generate_morning_report() -> dict:
    """M-5: 委托给 macro_data.generate_morning_brief() (零 LLM 成本)."""
    from app.services.macro_data import generate_morning_brief
    return await generate_morning_brief()


async def cleanup_expired_events():
    async with async_session_factory() as s:
        await s.execute(text("DELETE FROM stock_events WHERE event_date + decay_days * INTERVAL '1 day' < CURRENT_DATE"))
        await s.execute(text("DELETE FROM sector_events WHERE event_date + INTERVAL '7 days' < CURRENT_DATE"))
        await s.commit()
    logger.info("Expired events cleaned")
