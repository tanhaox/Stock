# 新闻事件分析系统 v3.1

> 基于 2026-05-26 实际测试数据更新。4 源分开发送 + 跨源去重 + JSON 修复。
> **v3.1 (2026-06-14)**: event_aggregator 同一股票内相似标题去重 (SimHash)
> **v3.0 (2026-06-13)**: v4.8 改造 - SimHash 跨源去重 + 三级分类 + macro_data 替代 + 融资融券重写
> **v2.2 (2026-06-09)**: LLM前商品/宏观关键词过滤 (v4.7), LLM虚构叙事已净化
> **v2.1 (2026-06-07)**: 配合系统 v4.5 P0 升级，更新全局约定

---

## v3.0 重大变更 (2026-06-13) ⭐

### 新闻特征系统改造 (枯竭数据 → Tushare 宏观数据)

**问题**: `stock_events` / `news_aggregated` / `news_verify` 表数据稀疏, `score_event_impact()` 长期返回 0

**新方案**: 使用 `compute_sector_macro_score()` + Tushare 宏观数据 + 板块暴露系数

| 旧位置 | 新方案 |
|--------|--------|
| `deep_scorer.score_event_impact()` | `compute_sector_macro_score()` (板块宏观得分 × 3) |
| `news_aggregated/news_verify` 加权 | Tushare 宏观数据 (板块暴露) |
| `holdings.news_signal` (从空 details 取) | `sector_macro_cache` 预计算 |
| `LearningPage` 新闻验证标签 | `MacroSnapshotView` 宏观快照展示 |

### 新闻分类去重 (SimHash 指纹)

`app/services/news_classifier.py`:
- **SimHash**: 中文按2字切分, 英文按词, MD5 hash → 64-bit 指纹
- **三级分类**: `company` / `sector` / `macro` / `garbage`
- **macro_only 跳过 LLM**: 期货/利率/汇率/PMI/CPI 等由 `macro_data` 覆盖
- **公司级白名单**: 中标/签约/投产/减持/业绩 → 保留 (即使有宏观词)
- **跨源去重**: 汉明距离 < 10 视为相同
- **个股摘要保留**: `get_stock_news_summary()` 从 `news_raw` + `stock_events` 合并

### 新闻采集 API 优化

- **聚合接口**: `GET /api/scan/news-dashboard` (6 请求 → 1)
- **新鲜度 API**: `GET /api/scan/news-freshness` (skip/crawl_only/analyze_only/full 4 建议)
- **增量更新**: `news_pipeline.py` 智能跳过:
  - < 2h 前爬过 → 跳过爬取
  - < 6h 前分析过 → 跳过 LLM 分析
- **修复新闻速报按钮**: 浏览器启动加超时 (10s/15s), 防止按钮卡死

### LLM 模型明确化

- **Stage 1 (打标签)**: `DEEPSEEK_FLASH_MODEL` (deepseek-v4-flash) - 轻量任务
- **Stage 2 公司级深度分析**: `DEEPSEEK_PRO_MODEL` (deepseek-v4-pro) - 精确提取
- **Stage 2 行业/政策/商品**: `DEEPSEEK_FLASH_MODEL` - 简单分类

---

## 数据流 (v3.0)

```
Tushare News 爬虫 (手动触发: "新闻速报"按钮)
  ↓
news_raw 表 (48hr自动清)
  ↓
┌────────── 4 源独立 LLM 分析 ──────────┐
│                                         │
│  xq(300条) → LLM(32K tokens)           │
│    提示: 散户视角, 公司+商品为主        │
│  sina(300条) → LLM(16K tokens)         │
│    提示: 公告视角, 公司新闻多           │
│  jinrongjie(300条) → LLM(16K tokens)   │
│    提示: 机构视角, 行业+政策            │
│  fenghuang(300条) → LLM(16K tokens)    │
│    提示: 宏观视角, 国际+政策            │
│                                         │
│  每源限制: 最近300条, 40事件上限        │
└──────────────┬──────────────────────────┘
               ↓
┌────────── 合并去重引擎 ──────────┐
│  1. JSON 修复 (尾部逗号/截断)     │
│  2. 跨源去重 (title 相似度>80%)   │
│  3. 公司名→代码补全 (模糊匹配)    │
│  4. 板块名标准化 (映射到系统标签)  │
└──────────────┬──────────────────┘
               ↓
┌────────── 两个分析窗口 ─────────────┐
│                                     │
│  早报窗口 (15:00前一天 ~ 9:30今天)  │
│  龙虎榜窗口 (9:30今天 ~ 16:30今天)  │
│                                     │
│  早报由 LLM 从 events 表生成        │
│  (非手写模板)                       │
└──────────────┬──────────────────────┘
               ↓
stock_events / sector_events 表
  ↓
评分修正 + 板块热力 + LLM提示词增强
```

---

## 分源 LLM 提示词

**所有源共用的板块约束**（加在每段提示词末尾）：

```
板块名必须从以下标准列表选择:
传媒, 公用事业, 基础化工, 家用电器, 建筑材料, 建筑装饰, 房地产,
有色金属, 机械设备, 汽车, 煤炭, 电子, 石油石化, 社会服务, 综合,
计算机, 通信, 钢铁, 银行, 食品饮料
不在列表中的选最接近的。
```

### 雪球 (xq) — 散户公司视角

```
你是一个A股新闻分析引擎。分析雪球财经新闻(散户视角为主)。
关注: 热门个股、概念题材、大宗商品价格、龙虎榜动向。
重点提取公司级事件，尽量标注股票代码。
事件上限40条，同主题合并，无关新闻忽略。
只输出JSON。
```

### 新浪财经 (sina) — 公告视角

```
你是一个A股新闻分析引擎。分析新浪财经新闻(公司公告为主)。
关注: 上市公司公告、高管变动、重大合同、业绩预告。
重点提取公司级事件，尽量标注股票代码。
事件上限40条，同主题合并，无关新闻忽略。
只输出JSON。
```

### 金融界 (jinrongjie) — 机构视角

```
你是一个A股新闻分析引擎。分析金融界新闻(专业机构视角)。
关注: 行业研报、机构动向、政策解读、板块轮动。
重点提取行业和宏观事件，公司事件次之。
事件上限40条，同主题合并，无关新闻忽略。
只输出JSON。
```

### 凤凰财经 (fenghuang) — 宏观视角

```
你是一个A股新闻分析引擎。分析凤凰财经新闻(宏观国际视角)。
关注: 产业政策、国际形势、宏观情绪、地缘政治。
重点提取宏观和行业事件，公司事件次之。
事件上限40条，同主题合并，无关新闻忽略。
只输出JSON。
```

---

## LLM 输出规范

```json
{
  "events": [
    {
      "ts_code": "300209.SZ",
      "category": "company|industry|macro|commodity",
      "direction": "bullish|bearish|neutral",
      "scores": {
        "materiality": 4,
        "immediacy": 5,
        "certainty": 5,
        "scope": 0
      },
      "composite_impact": 4.5,
      "title": "签订3.22亿AI服务器SSD合同",
      "summary": "重大合同，直接利好股价",
      "related_sectors": ["AI算力", "存储芯片"]
    }
  ],
  "sector_impacts": [
    {
      "sector": "人工智能",
      "direction": "bullish",
      "composite_impact": 3.5,
      "drivers": ["发改委政策支持", "阿里RISC-V适配安卓"],
      "prediction": "预计今日AI板块受政策+技术双重催化上涨"
    }
  ],
  "macro_summary": "全球股市创新高+美伊局势缓和，整体偏正面。"
}
```

### 四维评分体系

| 维度 | 含义 | 0 | 3 | 5 |
|------|------|---|---|---|
| materiality | 实质性 | 纯口头 | 具体政策 | 明确金额/合同 |
| immediacy | 即时性 | 长期趋势 | 本周内 | 明天就反应 |
| certainty | 确定性 | 传闻 | 官方渠道 | 已签约/已公告 |
| scope | 影响范围 | 单公司 | 细分行业 | 全市场 |

composite_impact = 加权均值

---

## JSON 修复层 (生产必备)

```python
def repair_json(raw: str) -> dict:
    """修复 LLM 输出的常见 JSON 问题."""
    import re, json
    
    # 1. 提取 JSON 块
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if not m: raise ValueError("No JSON found")
    fixed = m.group(0)
    
    # 2. 去尾部逗号: ,} 和 ,]
    fixed = re.sub(r',\s*}', '}', fixed)
    fixed = re.sub(r',\s*]', ']', fixed)
    
    # 3. 去尾部不完整内容 (截断的 JSON)
    if not fixed.endswith('}') and not fixed.endswith(']'):
        last_brace = max(fixed.rfind('}'), fixed.rfind(']'))
        if last_brace > 0:
            fixed = fixed[:last_brace+1]
    
    return json.loads(fixed)
```

---

## 跨源去重引擎

```python
def dedup_events(all_events: list[dict], threshold: float = 0.8) -> list[dict]:
    """按 title 相似度去重 (简单子串匹配)."""
    seen = []
    result = []
    for e in sorted(all_events, key=lambda x: x.get("composite_impact", 0), reverse=True):
        title = e.get("title", "")
        is_dup = False
        for s in seen:
            if title in s or s in title:
                is_dup = True
                break
            # 简单 Jaccard 相似度
            common = len(set(title) & set(s))
            if common / max(len(title), len(s), 1) > threshold:
                is_dup = True
                break
        if not is_dup:
            seen.append(title)
            result.append(e)
    return result
```

---

## 公司名→代码映射

```python
# 预加载 (启动时或首次使用时)
async def load_name_code_map():
    """从 stock_basic 和已有 scan_results 构建映射."""
    name_to_code = {}
    # 优先从 Tushare stock_basic 加载
    rows = await call_tushare('stock_basic', {'list_status': 'L'}, 'ts_code,name')
    for r in (rows or []):
        name_to_code[r['name']] = r['ts_code']
    return name_to_code

def fill_missing_codes(events, name_to_code):
    """对 ts_code=null 的 company 事件, 用名称模糊匹配补全."""
    for e in events:
        if e.get("ts_code") or e.get("category") != "company":
            continue
        title = e.get("title", "")
        for name, code in name_to_code.items():
            if name in title:
                e["ts_code"] = code
                break
```

---

## 板块名标准化

LLM 输出的板块名必须映射到系统已有的 `stock_dimension_tags` 表（dim_name='sector'）中的标准板块名。

**系统现有 20 个标准板块**：
```
传媒, 公用事业, 基础化工, 家用电器, 建筑材料, 建筑装饰, 房地产,
有色金属, 机械设备, 汽车, 煤炭, 电子, 石油石化, 社会服务, 综合,
计算机, 通信, 钢铁, 银行, 食品饮料
```

**方案**：不给 LLM 自由发挥空间。在提示词中直接要求 LLM 使用上述标准板块名：

```
## 板块名必须从以下标准列表中选择:
传媒, 公用事业, 基础化工, 家用电器, 建筑材料, 建筑装饰, 房地产,
有色金属, 机械设备, 汽车, 煤炭, 电子, 石油石化, 社会服务, 综合,
计算机, 通信, 钢铁, 银行, 食品饮料

如果新闻涉及的板块不在上述列表中，选择最接近的标准板块名。
```

**后端容错**：对于 LLM 偶尔输出的非标准板块名，用简单规则映射后查 `stock_dimension_tags` 表验证：

```python
async def normalize_sector(llm_sector: str) -> str:
    """将 LLM 输出板块名映射到系统标准板块."""
    # 先查是否已经是标准名
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT 1 FROM stock_dimension_tags WHERE dim_name='sector' AND tag_value=:v LIMIT 1"
        ), {"v": llm_sector})
        if r.fetchone():
            return llm_sector  # 已是标准名
    
    # 常见口语→标准映射（最小维护，只在 LLM 偶尔出界时使用）
    ALIAS = {
        "AI": "计算机", "人工智能": "计算机", "AI算力": "计算机",
        "芯片": "电子", "半导体": "电子", "存储芯片": "电子",
        "新能源": "公用事业", "光伏": "公用事业", "风电": "公用事业",
        "电网": "电力设备", "特高压": "电力设备", "电力": "公用事业",
        "新能源车": "汽车", "智能汽车": "汽车",
        "医药": "医药生物", "创新药": "医药生物", "医疗器械": "医药生物",
        "白酒": "食品饮料", "消费": "食品饮料",
        "军工": "国防军工",
    }
    return ALIAS.get(llm_sector, llm_sector)
```

不对接时打印 warning 日志，人工补映射规则。不另建 SECTOR_ALIAS_MAP 全局字典。

---

## 事件衰减模型（按类别差异化 + 影响级别）

### 公司级 (company)
- 无时间衰减，T+0 直接作用股价
- 按影响级别决定保留期：
  - composite≥4.0: 5 天 (重大合同/重组)
  - composite 2.0-4.0: 3 天
  - composite<2.0: 1 天 (小利好/传闻)

### 行业政策 (industry)
- composite_impact × decay_curve
- Day0=1.0, Day1=0.8, Day2=0.5, Day3=0.3, Day5=0.1, Day7=0

### 宏观 (macro)
- 不直接加减个股分数，只影响 market_correction 字段
- 保留 3 天

### 大宗商品 (commodity)
- composite_impact × decay_curve
- Day0=1.0, Day1=0.6, Day2=0.3, Day5=0
- 保留 7 天 (商品趋势持续较久)

---

## 评分集成: score_event_impact()

```python
async def score_event_impact(ts_code: str, today: date) -> float:
    """查询当日事件，返回对 composite 的加减分值."""
    events = await get_stock_events(ts_code, today)
    if not events: return 0.0
    
    total_impact = 0.0
    for e in events:
        direction_mult = 1 if e["direction"] == "bullish" else -1
        impact = e["composite_impact"] * direction_mult
        
        # 衰减
        days_ago = (today - e["event_date"]).days
        decay = get_decay(e["category"], e["composite_impact"], days_ago)
        total_impact += impact * decay
    
    # composite_impact 映射到分数调整
    # 4.0+ → ±8分, 3.0+ → ±5分, 2.0+ → ±2分, <2.0 → ±1分
    if abs(total_impact) >= 4: return 8 * (1 if total_impact > 0 else -1)
    if abs(total_impact) >= 3: return 5 * (1 if total_impact > 0 else -1)
    if abs(total_impact) >= 2: return 2 * (1 if total_impact > 0 else -1)
    return 1 * (1 if total_impact > 0 else -1)
```

---

## 早报机制 (8:30 AM)

**数据范围**: 前一天 15:00 → 今天 9:10 的新闻

**生成方式**: 由 LLM 从 stock_events + sector_events 表生成（非手写模板）

```
早报 Prompt: "以下是今日已分类的事件。生成早报，包含:
1. 持仓预警(重大利空) 2. 推荐榜关联 3. 板块预测(涨/跌/平) 4. 宏观一句话"
输入: {events_json} + {sector_impacts_json}
```

```markdown
📰 今日早报 (2026-05-26)

🔴 持仓预警:
  · 无重大利空

🟢 昨日推荐榜关联:
  · 深天马A(000050): 面板行业数据利好，OLED出货面积持续增长

📊 板块预测:
  ↑ AI/算力: 发改委政策+阿里RISC-V催化
  ↑ 电力设备: 5万亿电网投资预期
  ↓ 新能源车出口: 欧盟法案压力
  → 房地产: 广州新政发布会，等待细则

🌍 宏观: 全球股市创新高，情绪偏正面
```

---

## 龙虎榜+新闻联动 (16:30)

```
龙虎榜 麦格米特 六机构净买入6.27亿
  + 新闻: 数据中心电源概念 + 机构密集调研
  → 共振强度: ⭐⭐⭐⭐⭐ (机构+题材双确认)

龙虎榜 某游资股 买入2亿
  + 新闻: 无相关事件
  → 共振强度: ⭐⭐ (纯资金驱动)
```

前端展示：龙虎榜热门板块旁加"事件共振"列。

---

## 数据清理策略

| 数据 | 保留期 | 理由 |
|------|--------|------|
| news_raw | 48hr | 原始文本，分析完即无价值 |
| company 事件 (≥4.0) | 5天 | 重大事件持续影响 |
| company 事件 (2.0-4.0) | 3天 | 一般事件 |
| company 事件 (<2.0) | 1天 | 小事件快速消化 |
| industry 事件 | 7天 | 政策影响持续 |
| macro 事件 | 3天 | 情绪变化快 |
| commodity 事件 | 7天 | 商品趋势持续 |
| 无法映射的事件 | 3天 | 垃圾信息 |

---

## 数据库表

### news_raw
```sql
CREATE TABLE news_raw (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source VARCHAR(50),
  title VARCHAR(500),
  content TEXT,
  pub_time TIMESTAMPTZ,
  fetched_at TIMESTAMPTZ DEFAULT NOW()
);
-- 48hr TTL: DELETE WHERE fetched_at < NOW() - INTERVAL '48 hours'
```

### stock_events
```sql
CREATE TABLE stock_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ts_code VARCHAR(12),
  event_date DATE,
  category VARCHAR(20),       -- company/industry/macro/commodity
  direction VARCHAR(10),       -- bullish/bearish/neutral
  materiality SMALLINT,        -- 0-5
  immediacy SMALLINT,          -- 0-5
  certainty SMALLINT,          -- 0-5
  scope SMALLINT,              -- 0-5
  composite_impact DECIMAL(3,2),
  title VARCHAR(300),
  summary TEXT,
  related_sectors TEXT[],
  decay_days SMALLINT DEFAULT 3,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### sector_events
```sql
CREATE TABLE sector_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  sector VARCHAR(50),          -- 标准化后的板块名
  event_date DATE,
  direction VARCHAR(10),
  composite_impact DECIMAL(3,2),
  drivers TEXT[],
  prediction TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### name_code_map (新)
```sql
CREATE TABLE name_code_map (
  company_name VARCHAR(100) PRIMARY KEY,
  ts_code VARCHAR(12),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
-- 从 stock_basic 定期同步
```

---

## 系统对接点

| 模块 | 改动 |
|------|------|
| `news_crawler.py` | ✅ 已完成 — Playwright + undetected 爬虫 |
| `event_detector.py` | 从空壳 → 新闻引擎核心: 分源LLM+去重+入库 |
| `deep_scorer.py` | `score_event_impact()` 查当日事件加减分 |
| `llm_deep_analyzer.py` | 提示词附加近期事件 |
| `sector_heat_engine.py` | 事件驱动的板块热度加成 |
| `background_sync.py` | 新闻数据清理 (48hr TTL) |
| `deepseek.py` | `max_tokens` 参数化 ✅ 已完成 |

---

## 前端改造

### ScanPage
- 新增"📰 新闻速报"按钮（在"全市场扫描"之前）
- 早报卡片（折叠显示）
- 扫描结果保留，但股票行标注 ⚡ 有事件
- 板块预测区域

### AnalysisPage / ResultPage
- 有事件的股票显示 ⚡ 标记
- 精选卡片显示关联事件摘要

### SettingsPage
- Tushare Cookie 配置 ✅ 已完成

---

## v3.1 event_aggregator 相似标题去重 (2026-06-14) ⭐

`app/services/event_aggregator.py` 在查询展示前增加 SimHash 去重：

```python
def _dedup_similar_events(events: list[dict]) -> list[dict]:
    """同一股票内相似标题去重，每股同主题只保留最高 display_score 的一条."""
    # 1. 按 ts_code 分组
    # 2. 每组按 display_score 降序
    # 3. 计算 SimHash 指纹 (中文2字切分 + MD5 hash → 64-bit)
    # 4. 汉明距离 < 8 判定为相似
    # 5. 保留第一条 (最高分), 跳过相似标题
```

**修复效果**:
| 场景 | 修复前 | 修复后 |
|------|--------|--------|
| 300848.SZ 出现 3 次 | 全部显示 | 只显示最高分 1 条 |
| 300308.SZ 出现 2 次 | 全部显示 | 只显示最高分 1 条 |
| 相似标题（如"龙虎榜数据"） | 重复显示 | 自动去重 |

**根本原因**: LLM Stage2 对相似新闻可能产生不同 composite_impact 评分，导致同一股票多条记录。
**解决方案**: 在 event_aggregator 查询层增加 SimHash 相似度过滤。

---

## v2.2 商品/宏观预过滤 (2026-06-09) ⭐

在 LLM Stage 1 标签之前, `event_detector.py:analyze_all_sources()` 新增关键词过滤:

**过滤规则**:
- 原始新闻 (~2218条/天) → 匹配商品/宏观关键词 → 丢弃 (~457条)
- 例外: 命中关键词但含公司级白名单 (中标/签约/减持/业绩/公告/涨停/连板/重组/并购/定增/IPO/分红) → 保留
- 剩余 ~1761 条进入 LLM 分析

**过滤关键词**:
- 期货/商品: 期货/原油/沪铜/沪铝/螺纹钢/铁矿石/焦煤/碳酸锂/甲醇/PVC/橡胶/LME/COMEX/CBOT...
- 汇率: 人民币中间价/美元指数/在岸/离岸/CNY/USDCNY/外汇储备...
- 宏观: SHIBOR/LPR/MLF/国债收益率/PMI/CPI/PPI/GDP/M2/社融/融资余额/北向资金...
- 纯行情: OPEC/EIA/API原油库存/贝克休斯...

**效果**: sector_events 从 86→28 条 (删除 58 条 LLM 虚构叙事)。剩余 28 条为真正的行业/公司级事件。

## 实施顺序

1. ✅ 爬虫 (`news_crawler.py`)
2. ✅ 数据库表 (news_raw + stock_events + sector_events)
3. 🔲 event_detector.py — 分源 LLM + 去重 + 入库
4. 🔲 JSON 修复层 + 跨源去重引擎
5. 🔲 公司名→代码映射 (name_code_map 表)
6. 🔲 板块名标准化
7. 🔲 评分集成 (score_event_impact)
8. 🔲 早报 LLM 生成
9. 🔲 龙虎榜+新闻联动
10. 🔲 前端改造 — ScanPage 新闻模块
11. 🔲 数据清理定时任务

---

## v2.1 系统升级影响 (2026-06-07) ⭐

配合系统 v4.5 P0 升级，新闻模块可直接使用以下全局工具：

| 工具 | 用途 |
|------|------|
| `normalize_ts_code()` | 公司代码规范化 (替代内联 `startswith('6')→.SH`) |
| `get_stock_name()` | 统一名称查询 (替代直接查 scan_results) |
| `safe_float()` | 数值安全提取 (替代内联 `isnan` 守卫) |
| `sanitize_for_json()` | JSON 序列化安全 (LLM 输出的 NaN → null) |

新闻爬虫 `news_crawler.py` 的代码规范化 (line 61) 已知有 BJ 过匹配 bug (所有非 0/3/6 代码均被映射为 .BJ)。建议后续迁移到 `normalize_ts_code()`。
