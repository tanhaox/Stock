# Stock Analyst 开发施工手册 v2.6

> **用途**: 给开发窗口的 AI 使用。每次修改代码前，先查找你要改的文件/表，
> 然后看"连带影响"表格 + 全局约定。新增/修改任何模块前，先查阅第七章"统一工具库"。
> **最近更新**: 2026-06-20 — v7.0.34 Exclusion 踢出名单 (5 reasons) + 股票信息对齐按钮 + v2 Trainer 训练数据筛选

---

## 零、新增模块时必读 ⭐ v2.0 新增

**不要在模块内打补丁。** 以下全局能力已统一提供，新增模块时直接导入：

| 需求 | 导入 | 说明 |
|------|------|------|
| 数值安全 | `from app.utils.numpy_utils import safe_float, sanitize_array, sanitize_for_json` | 不要再写 `np.nan_to_num` 或 `if np.isnan(x)` |
| 超额收益 | `from app.core.market_data import compute_excess_return, get_benchmark_closes` | 不要再写自己的 700001.TI SQL |
| 进度回调 | `from app.core.progress import ProgressCallback, make_progress_adapter` | 统一 4 参数 `cb(phase, current, total, message)` |
| 代码规范 | `from app.utils.stock_code import normalize_ts_code, strip_suffix` | 不要再写 `startswith('6') → .SH` |
| 名称查询 | `from app.core.name_resolver import get_stock_name, batch_get_stock_names` | 不要再直接查 scan_results |
| 前复权K线 | `from app.services.kline_utils import get_adjusted_kline, get_ex_rights_dates` | K线已全局前复权, 除权日可精确识别 |
| **涨跌幅颜色 (A 股惯例)** | `import { getPnlColor } from '../../lib/signalColor'` (前端) | **红涨绿跌**, 涨=`#ef4444` 跌=`#10b981`, 不要再手写颜色 |

---

## 如何使用

```
步骤 0: 先查"统一工具库" → 需要的功能是否已有全局实现
步骤 1: 确定你要改的文件 → 在下方目录中找到它
步骤 2: 读该文件的"连带影响"表格 → 列出所有必须同步修改的文件
步骤 3: 做修改 → 自检 → 提交
```

**搜索方式**: grep 你要改的文件名或表名，只读命中的段落。

---

## 一、核心 Python 文件

### `app/services/deep_scorer.py`

**职责**: 深度评分主引擎。`deep_analyze()` 是 5 管线段的编排者 (v4.4: JSON 序列化已加边界守卫 sanitize_for_json)。

**v7.0.30 新增铁三角死规则**: `_apply_hard_rules()` (line 132-203), 5 条规则 (MA20+RSI+VOL) 后置过滤 TG 信号。详见 [docs/improvements/已完成/20260618-v7.0.30-铁三角死规则接入.md](../improvements/已完成/20260618-v7.0.30-铁三角死规则接入.md)

**连带影响** (改这个文件必须检查):

| 如果你改了 | 必须同步检查 |
|-----------|------------|
| `deep_analyze()` 函数签名 | `app/api/analysis.py:15,128` — trigger_analysis 调用 signature |
| | `app/api/scan.py:206,208` — trigger_scan SSE 调用 |
| `_deep_enrich_phase()` 新增字段 | `app/api/result.py:163-167` — result/final 透出字段 |
| | `frontend/src/pages/ResultPage.tsx` — 前端渲染该字段 |
| `_deep_persist_phase()` INSERT 列 | `app/models/data_models.py` — ORM 模型列对齐 |
| | 数据库 ALTER TABLE — 新列必须在 DB 中存在 |
| | ⚠️ **details JSON 新字段**: 必须在 `_deep_persist_phase` 第 1119-1124 行写库,否则 score_4h 覆辙再演 |
| `_apply_hard_rules()` 规则阈值/增删 | `backend/scripts/backfill_hard_rules.py` — 改完后必须重跑补历史 details |
| | ⚠️ **archetype 适配**: value_defensive / cyclical_resource 默认跳过 R2/R3,不要随便改 |
| `DEFAULT_WEIGHTS` | `app/services/shadow_trainer.py:75` — import 此常量 |
| `_deep_preload_phase()` 预加载调用 | `app/services/predictive_features.py` — 训练特征预加载 |

**进度回调**: ⭐ v4.5 已统一为 4 参数 `progress_cb(phase, current, total, message)`。不要再传 3 参数。

**⭐ v7.0.30 重要约定**:
- **`_apply_hard_rules` 永远 try/except 包裹**: 失败不影响主流程 (line 1386-1390)
- **字段写入不丢**: 任何 `r["xxx"] = ...` 都必须在 `_deep_persist_phase` 的 details dict 里序列化
- **archetype 适配**: R2/R3 跳过 value/cyclical 是数据驱动的结论,不能改回"全员剔除"

---

### `app/services/predictive_features.py`

**职责**: 特征工程。`build_features()` 构建单股特征, `build_training_data()` 构建训练集。

**连带影响**:

| 如果你改了 | 必须同步检查 |
|-----------|------------|
| `FEAT_NAMES` 列表增删特征 | `app/services/predictive_scorer.py:71` — `batch_predict` 用 FEAT_NAMES 顺序组装向量 |
| | `scripts/train_predictive_model.py` — 训练脚本读取 FEAT_NAMES |
| `build_training_data()` 返回值 | `scripts/train_predictive_model.py:23` — 解包 (X,y,weights,sources,groups) |
| 大盘基准 | `app/core/market_data.py` — ⭐ v4.5 统一使用 `compute_excess_return()` |

**关键约定**:
- 训练标签 = 超额收益 vs 700001.TI (⭐ v4.5 统一公式: 交易日计数, 非日历日)
- 特征缺失时填 0, 不要填 None 或 NaN (⭐ `app/utils/numpy_utils.safe_float` 统一处理)
- `FEAT_NAMES` 顺序 = `build_features` 返回 dict 的顺序 = `batch_predict` 读的顺序

---

### `app/services/shadow_trainer.py`

**职责**: 影子训练引擎 (Bayesian Optimization + Per-Stock 权重优化)。

**⭐ v4.5 重要变更**:
- `_bench_ret()` 日历日计数已修正为**交易日计数** (修复系统性偏误)
- `_bench_ret()` 函数已完全移除, 改为 `_index_ret()`
- 基准加载改用 `app.core.market_data.get_benchmark_closes()` (模块级缓存)
- ⚠️ **旧 shadow 训练结果需重训**: param_library 中 is_shadow=true 的权重基于旧 (日历日) 标签

---

### `app/services/dragon_pool_service.py` ⭐ v6.0 新增

**职责**: 潜龙池动态监控服务 (首板 → 二波型浮出)，4 核心函数 + 编排函数。

**核心方法**:

| 函数 | 输入 | 输出 | 用途 |
|------|------|------|------|
| `join_pool_from_first_limit(trade_date)` | 日期 | `[{ts_code, first_limit_id, relay_prob, waveback_prob}, ...]` | 从 `first_limit_up` 选 S/A/B 级入池 (UNIQUE 防重) |
| `update_pool_state(trade_date)` | 日期 | 更新行数 | 更新 current_price / min_price / days_in_pool (基于 daily_kline) |
| `get_active_pool_symbols()` | — | `[{ts_code, added_at, days_in_pool, waveback_prob, ...}]` | 列出所有 active 池中股 |
| `evaluate_exit(symbol, days_in_pool)` | symbol + 天数 | `{exit, reason, confidence}` | **5 触发任一即踢出** (v6.0.4): 见下方踢出规则表 |
| `detect_emerging(symbol)` | symbol | `{emerging, pattern, confidence, signal_quality, nm_score}` | 浮出二板信号: waveback > 0.3 + 强制分时验真 |
| `evaluate_all_active()` | — | `{total, exited_count, emerging_count, exited[], emerging[], errors[]}` | 全池评估编排 |

**踢出规则 (v6.0.4, 5 触发任一)**:

| # | reason | 触发条件 | v6.0 |
|---|--------|---------|------|
| 1 | `atr_stop` | exit_signal_detector critical/high | v6.0 |
| 2 | `fatigue_broken` | fatigue_detector broken/capitulation | v6.0 |
| 3 | `time_decay_10d` | days_in_pool >= 10 | v6.0 |
| 4 | `not_first_limit` | added_at 前 10 天内 prev-based 涨幅 ≥9.9% (v6.0.3: 用 prev_close 不用 open-close) | v6.0.3 |
| 5 | `consecutive_board` | added_at **后**任何一天 prev-based 涨幅 ≥9.9% (连板成功退出) | v6.0.4 |

**v6.0.3 关键修复**: 所有涨跌幅判定改用 `LAG(close) OVER (...)` 算 prev-based，不再用 `(close - open) / open` (后者是当日振幅，不是涨跌幅)

**业务常量** (语义标注，不参与判定):
- `EMERGING_WAVEBACK_THRESHOLD = 0.30` (二波型浮出门槛)
- `MAX_DAYS_IN_POOL = 10` (强制清理上限)
- `SIGNAL_QUALITY_MIN = 0.5` (分时验真质量分下限)
- `NM_SCORE_MIN = 0.0` (N 形分下限)

**复用** (不复制代码):
- `app.services.second_board_predictor.get_predictor().predict()` — 双模式二板概率
- `app.services.exit_signal_detector.detect_exit_signals()` — ATR 动态止损
- `app.services.fatigue_detector.detect_fatigue()` — 平台破位 5 阶段
- `app.services.signal_quality_scorer.verify_signals_with_minute_bars()` — 分时验真 (强制)
- `app.services.minute_nm_detector.detect_nm_pattern()` — N/M 形态 (verify 内部调用)

**连带影响** (改这个文件必须检查):
| 如果你改了 | 必须同步检查 |
|-----------|------------|
| `evaluate_exit()` 踢出逻辑 | `app/scheduler/daily_tasks.py:task_update_dragon_pool` 调度依赖 |
| `detect_emerging()` 浮出门槛 | `app/api/dragon.py:GET /waveback-potential` 前端期望格式 |
| `join_pool_from_first_limit()` 入选条件 | `app/api/scan.py:551-575` 阶段 4 调用方 |
| `dragon_pool` 表结构 | `scripts/migrations.py:120-122` migration |
| 业务常量 (阈值) | `docs/README.md` v6.0 章节 + `docs/architecture.md` §10 v6.0 变更日志 |

**数据库**: `dragon_pool` 表 (migrations 120-122) — 22 列 + 2 索引
- 主键: `id UUID`
- 唯一约束: `UNIQUE(ts_code, added_at)` (防止重复入池)
- 部分索引: `idx_dragon_pool_emerging WHERE emerging = TRUE`

**API 端点** (`app/api/dragon.py`):
- `GET  /api/dragon/pool?status=active|exited|all` — 池中股票列表
- `GET  /api/dragon/waveback-potential` — 仅 emerging 池中股 (二波型 tab)
- `POST /api/dragon/pool/scan?trade_date=YYYY-MM-DD` — 手动入池
- `POST /api/dragon/pool/evaluate` — 手动全池评估
- `POST /api/dragon/pool/update-state` — 手动状态更新

**扫描阶段集成** (`app/api/scan.py:551-575`):
- `/api/scan/all` 阶段 4 (v6.0 新增)
- 4 个 SSE 事件: `dragon_pool_join / dragon_pool_update / dragon_pool_evaluate / dragon_pool_done`
- ⚠️ `/api/scan/trigger` 旧路径不含阶段 4 (不破坏旧调用方)

**约束遵守** (DEVELOPER_GUIDE 铁律):
- ✅ 数值安全: 0.0 兜底，无内联 NaN 守卫
- ✅ 进度回调: 4 参数标准 (SSE `emit(phase, current, total, msg)`)
- ✅ 不复制代码: 全部 `from app.services.X import Y`
- ✅ 不用 DROP/TRUNCATE: `CREATE TABLE IF NOT EXISTS`
- ✅ 不写死硬指标 (除 2 个语义标注常量)

---

### `app/api/result.py`

**职责**: 推荐结果 API (`GET /result/final`)。

**连带影响**:

| 如果你改了 | 必须同步检查 |
|-----------|------------|
| `base_select` SQL 加列 | 列索引偏移 — `r[17]` 之后所有列的 index 都要 +1 |
| | 最安全做法: 新列放最后, `r[-1]` 取值 |
| `details` JSON 字段增删 | `frontend/src/pages/ResultPage.tsx` — 渲染该字段 |
| `rec_index` 权重 | 前端 `ResultPage.tsx:57` sortKey 默认排序 |

---

### `app/api/scan.py`

**职责**: TG 信号扫描 + SSE 进度回传 (POST /scan/trigger)。

**进度回调**: ⭐ v4.5 标准 `progress_cb(phase, current, total, message)` — 4 参数。如需适配旧代码用 `make_progress_adapter()`。

---

## 二、⭐ DNA 个性化模型 (v4.5 新增)

### `app/services/stock_dna/` 包 (10 个模块)

**包位置**: `app/services/stock_dna/`
**API 路由**: `app/api/dna.py` (`/api/dna/*`, 7 端点)
**数据库**: `stock_dna.*` schema (3 表, 独立于现有系统)
**前端**: `frontend/src/components/DnaLab.tsx` (4 Tab: 概览/单股档案/对比矩阵/表情历史)
**模型文件**: `backend/models/dna/{symbol}_model.json` (Per-Stock XGBoost)

**关键约定**:
- ⭐ DNA 系统**完全并行**，不修改任何现有代码
- 特征维度: 146 维 (73日线 + 15表情 + 15市场 + 12转移 + 8周期 + 15历史 + 8交互)
- Per-Stock XGBoost: 80树 × depth=3, Huber δ=3.0
- 日线伪表情降级: 无分时数据时用 OHLCV 计算简化表情
- 周期检测: 评分制 (≥2/3条件 + 5日滑动窗口), 非 AND 制

**连带影响**:

| 如果你改了 | 必须同步检查 |
|-----------|------------|
| `features.py` 增删维度 | `model.py` ALL_FEAT_NAMES 同步 |
| | `inference.py` `_build_today_features()` 同步 |
| | `data_builder.py` 特征组合同步 |
| `emotion.py` 表情逻辑 | `data_builder.py` 聚类调用 + `inference.py` 伪表情降级 |
| `cycle.py` 周期阈值 | `data_builder.py` 周期统计 + `inference.py` 周期特征 |
| `data_builder.py` 样本生成 | `model.py` 训练数据读取列名 |

---

## 三、数据库表

### `signal_history`

**列**: `symbol, scan_date, composite_score, archetype, market, push_count_30d, price_zone_*, ret_t1/2/3/5, max_gain/loss_pct, outcome_label, deception_type, relative_position, sector_direction, sector_lifecycle, sector_rank_5d, market_5d, predicted_return, predicted_win_prob, excess_return`

### `analysis_scores`

**列**: `scan_date, symbol, name, tech/kline/fund_score, sector_bonus, composite_score, fundamental_adjustment, market_correction, details(JSONB), archetype, weight_snapshot, adjustment_reasons, dimension_scores, win_probability, downside_risk`

**⭐ v4.5**: `details` JSONB 序列化已加 `sanitize_for_json()` 边界守卫，NaN/Inf 自动转为 null。

### `daily_kline`

**⭐ v4.5 新增列**: `adj_factor DOUBLE PRECISION DEFAULT 1.0` — 复权因子。全量数据已通过 `resync_all_kline.py` 前复权。

### `stock_dna.daily_samples` / `stock_dna.profiles` / `stock_dna.predictions`

**独立 schema**，与现有系统零交叉污染。详见 `architecture.md` §5.2。

---

## 四、前端页面

### `ResultPage.tsx`

**读取字段** (20+): `symbol, name, composite_score, rec_index, relative_position, strategy_label, peer_rank, sector_tier, resonance_type, ...`

A 股配色: 红涨绿跌: rec_index ≥ 80 → 红, < 40 → 绿。

### `DnaLab.tsx` ⭐ v4.5 新增

4 个子 Tab，集成在 `LearningPage.tsx` 的 "🧬 DNA实验室"。

---

## 五、跨模块依赖速查

### 改"评分→推荐"链路

```
deep_scorer._deep_enrich_phase
  → deep_scorer._deep_persist_phase (details JSON, ⭐ sanitize_for_json guarded)
    → ⭐ v7.0.30: _apply_hard_rules 后置过滤 (5 条铁三角规则) → 写 hard_rules_* 字段到 details
    → result.py get_final_results (SELECT → data dict)
      → ResultPage.tsx (渲染)
```

**v7.0.30 死规则字段透出** (前端可消费):
- `details->>'hard_rules_blocked'` (bool 字符串)
- `details->>'hard_rules_summary'` ("❌ R2_weak(价格低于 MA20 -5.2%)" 或 "✅ 通过 5/5 条")
- `details->>'hard_rules_passed'` (JSON list, 前端可解析)
- `details->>'hard_rules_failed'` (JSON list, 失败原因列表)

### 改"训练→预测"链路

```
predictive_features.build_training_data
  → train_predictive_model (fit + save)
    → predictive_scorer.batch_predict (load + infer)
      → deep_scorer._deep_enrich_phase (predict blend)
```

### 改"超额收益计算" ⭐ v4.5

```
任何地方需要超额收益 → 统一使用 app.core.market_data.compute_excess_return()
  → 内部调用 get_benchmark_closes() (模块级缓存, 全系统共享)
  → 交易日计数 (非日历日!)
```

### 改"数据库表结构"

```
① ALTER TABLE (DB)
② ORM model 更新
③ 所有 INSERT/UPDATE 语句更新
④ result/analysis API SELECT 更新
```

---

## 六、常见补丁原因速查

| 补丁类型 | 原因 | 预防 |
|---------|------|------|
| "字段透出缺失" | 后端加了字段但 result/analysis 没加 | 改 details JSON 时同步改 SQL SELECT |
| "前端不渲染" | 后端透出了但前端不读 | 改 API 响应时同步改前端 |
| "模型特征数不匹配" | FEAT_NAMES 加了但模型没重训 | 改 FEAT_NAMES 后立即跑 train |
| "progress_cb 参数不匹配" | 新模块传错参数数量 | ⭐ 使用 `make_progress_adapter()` |
| "NaN 导致 JSON 崩溃" | 评分维度产生 NaN → json.dumps 崩溃 | ⭐ 所有 json.dumps 前调用 `sanitize_for_json()` |
| "除权数据污染" | 新模块直接用 daily_kline 原始数据 | ⭐ 系统已全局前复权, 直接用即可 |
| "BJ 股票被丢弃" | `else: continue` 丢弃北交所代码 | ⭐ 使用 `normalize_ts_code()` |

---

## 七、全局约定 ⭐ v2.0

| 约定 | 值 |
|------|-----|
| 大盘基准 | `700001.TI` (同花顺全A等权), 不是 `000001.SH` |
| 训练标签 | 超额收益 = stock_ret - market_ret (700001) ⭐ 统一交易日计数 |
| 模型文件 | `models/predictive_scorer.json` (回归) + `predictive_ranker.json` (排序) |
| A 股配色 | 红涨绿跌 (rec_index ≥ 80→红, < 40→绿) |
| SSE 进度 | `progress_cb(phase, current, total, message)` — **必须 4 参数** |
| L1 过滤 | TG 扫描后 L1 级信号不进入 deep_analyze |
| 股票代码 | `normalize_ts_code()` — 支持 6xxxxx.SH / 0xxxxx.SZ / 8xxxxx.BJ / 920xxx.BJ |
| NaN 处理 | `safe_float()/sanitize_array()` — 不要再写 `np.nan_to_num` |
| JSON 序列化 | `sanitize_for_json()` — 在 json.dumps 之前调用 |
| 除权 | ⭐ 系统已全局前复权 (daily_kline.adj_factor 列)。**不要再加任何除权检测代码** |
| 代码规范 | `normalize_ts_code()` — 不要写 `startswith('6') → .SH` |
| 🔴 数据库安全 | **绝对禁止 DROP/TRUNCATE/DELETE 全表** — 只用 SELECT/INSERT/UPDATE |
| 🔴 数据库安全 | **修改表结构使用 ALTER TABLE**，不要重建表 |
| 🔴 数据库安全 | **daily_kline 表含 400万+ 条数据，删除后无法恢复** |

---

## 八、⭐ 统一工具库 (v2.0 新增)

### 数值安全 (`app/utils/numpy_utils.py`)

```python
from app.utils.numpy_utils import (
    safe_float,        # val → float, NaN/Inf/None → default (0.0)
    safe_auc,          # AUC NaN → 0.5 (随机基线)
    safe_rsi,          # RSI/KDJ NaN → 50 (中性)
    sanitize_array,    # arr NaN/Inf → fill (默认 0.0)
    sanitize_for_json, # dict/list/numpy → JSON-safe (NaN→null)
    div0,              # a/b, b≈0 → default
    safe_corrcoef,     # Pearson r, 常数序列→0.0
)
```

### 基准数据 (`app/core/market_data.py`)

```python
from app.core.market_data import (
    get_benchmark_closes,     # → dict[date, float] (700001.TI, 模块级缓存)
    compute_excess_return,    # 交易日计数超额收益 (数据不足→0.0)
    compute_excess_return_or_fallback,  # 数据不足→纯股票收益 (向后兼容)
)
```

### 进度回调 (`app/core/progress.py`)

```python
from app.core.progress import ProgressCallback, make_progress_adapter, NoopProgress
# ProgressCallback: cb(phase: str, current: int, total: int, message: str = "")
# make_progress_adapter(cb): 将任意回调包装为标准 4 参数
```

### 股票代码 (`app/utils/stock_code.py`)

```python
from app.utils.stock_code import normalize_ts_code, strip_suffix, classify_board
# normalize_ts_code('600519') → '600519.SH'
# normalize_ts_code('920123') → '920123.BJ'
# strip_suffix('002594.SZ')  → '002594'
```

### 名称解析 (`app/core/name_resolver.py`)

```python
from app.core.name_resolver import get_stock_name, batch_get_stock_names, ensure_name_cache
```

### 前复权K线 (`app/services/kline_utils.py`)

```python
from app.services.kline_utils import (
    get_adjusted_kline,        # 获取前复权K线 (含 adj_factor)
    get_ex_rights_dates,       # 从 adj_factor 精确识别除权日 (不再靠阈值猜测!)
    iter_non_exrights_chunks,  # 按除权日切分连续K线段
)
```

### 大神仙空 (`app/services/big_fairy.py`) ⭐ v4.7

```python
from app.services.big_fairy import (
    _big_fairy_from_arrays,  # 纯NumPy计算 (closes,highs,lows,volumes,symbol) → dict, 无DB I/O
    compute_big_fairy,        # DB查询版 (symbol, session) → dict
)
# 返回: {score(0-5), signal(normal/weak/sell/strong_sell), bearish(bool),
#         dimensions(list), k,d,j, macd_hist, rsi14, close, ma5,ma10,ma20, details}
# score≥2 = 卖出信号 (偏空), score≥3 = 强空
# 7 维度: KDJ + MACD + MA均线 + RSI + 量价关系 + 短期动量 + 超买综合
```

### 信号计算规则 (`app/services/alphaflow_pool_service.py`) ⭐ v4.7

```python
# 锁死判定 — lock_detector.py v2.3
from app.services.lock_detector import detect_lock_simple
# 返回包含 state 字段: "locked" | "breakout_up" | "breakout_down"

# AlphaFlow 信号优先级:
# 1. 锁死中 → watch (TG/BF 都不看)
# 2. 主升浪 + TG买入(10天延续) → buy
# 3. 主升浪 + BF卖出(10天延续) → sell  
# 4. TG+BF 同时活跃 → 按日期offset比较, 最新信号胜出
# 5. 破位下跌 → sell
```

### 事件过滤 (`app/services/event_detector.py`) ⭐ v4.7

```python
# LLM分析前已过滤: 商品期货/汇率/宏观指标类新闻
# 命中关键词但有公司级白名单(中标/签约/减持/业绩/公告/涨停) → 保留
# 过滤逻辑在 analyze_all_sources() 中, Stage 1 标签之前
```

### 新闻分类去重 (`app/services/news_classifier.py`) ⭐ v4.8

```python
from app.services.news_classifier import (
    compute_simhash,           # 文本 SimHash 指纹 (64-bit)
    hamming_distance,          # 汉明距离
    is_similar,                # 判断相似 (阈值10)
    dedup_news_list,           # 跨源去重
    classify_news,             # 三级分类 (company/sector/macro/garbage)
    should_skip_for_llm,       # 跳过 macro_only (避免与宏观数据重复)
    get_stock_news_summary,    # 个股新闻摘要 (含 title+summary)
    preprocess_for_llm,        # 批量预处理
)
# SimHash: 中文按2字切分, 英文按词, MD5 hash → 32-bit
# 公司级白名单: 中标/签约/投产/减持/业绩 → 保留
# 行业级: 半导体/新能源/医药 → sector
# 宏观级: 期货/利率/汇率/PMI/CPI → macro (跳过LLM)
```

### 龙虎榜精细化 (`app/services/toplist_analyzer.py`) ⭐ v2.1

```python
from app.services.toplist_analyzer import (
    _match_broker_tag_v2,      # 精细化席位匹配
    analyze_daily_all,         # 个股分析 (含 机构/游资 tier 拆分)
    analyze_sector_resonance,  # 板块共振 (5级强度)
    get_net_buy_persistence,   # 净买持续性 (3日/5日)
    get_cached_daily_toplist,  # v2.1: 智能缓存 (历史永久/当日动态)
    _is_trading_hours_now,     # 交易时段判断
    clear_toplist_cache,       # 手动清除缓存
)
# 机构: 公募/私募/QFII/北向/社保 (细分)
# 游资: 顶级(95-87分)/一线(80-60分)/二线(55-45分)/三线(40分)
# 共振强度: extreme/strong/moderate/weak/minimal
# 共振标签: 机构入场/顶级游资/一线游资/合力买入/净买普遍
# 缓存策略:
#   - 历史交易日 → 永久缓存
#   - 当日交易时段 (9:30-15:00) → 5分钟
#   - 当日休市时段 → 1小时
```

### 龙虎榜 SSE 接口 ⭐ v2.1

```python
# 强制刷新 (SSE 流式进度)
POST /api/scan/toplist-refresh
# 事件: {phase: sync/analyze/sector, current, total, msg}
# 完成: {done: true, data: {date, total_stocks, total_sectors}}

# 新鲜度检查
GET /api/scan/toplist-freshness
# 返回: {latest_trade_date, is_trading, is_historical, recommendation}

# v2.1: 交易时段已过则不刷新 (历史数据永久缓存)
```

### TG 扫描阶段 (`app/api/scan.py:trigger_scan`) ⭐ v4.8.2

```python
# 10 阶段 SSE 流式扫描
POST /api/scan/trigger?skip_download=true&market_filter=主板

# 阶段顺序:
#   ① toplist         → ensure_toplist_fresh() (skip_download 时跳过)
#   ② download        → tg_engine 内部: download_latest_kline()
#   ③ scan            → 本地 TG 计算 (5% 步长推送)
#   ④ ambush_scan     → 潜伏猎手 (用 scan_results 最新日期)
#   ⑤ pattern_scan    → 形态识别
#   ⑥ deep_score      → 14 维深度评分
#   ⑦ nm_defense      → 分钟线防伪
#   ⑧ toplist_sync    → ⛔ v4.8.2 移除 (与 ① 重复, 合并到 toplist)
#   ⑨ accuracy_feedback → isolated_meta=True 写独立列
#   ⑩ dna_auto_join   → asyncio.create_task 异步训练, 不阻塞 done
#   done 事件         → 前端 currentPhase='done' 触发 load()

# v4.8.2 修复的 15 项问题:
#   P0-1: ScanPage setCurrentPhase 类型扩展
#   P0-2: DNA auto-join 异步化 (不阻塞 done)
#   P1-1: toplist_sync 合并到 toplist (去除重复)
#   P1-2: phaseMessages slice(-8) → slice(-20), maxHeight 120 → 280
#   P1-3: market_filter 后端真过滤 (用 classify_board)
#   P1-4: skip_download 同时控制龙虎榜 + DNA
#   P1-5: scan phase 5% 步长推送 (5000只 → 100 事件)
#   P1-6: accuracy_feedback isolated_meta=True
#   P2-1: 14 维文案修正
#   P2-2: phase 异常信息统一 "异常: {e}"
#   P2-3: 覆盖率 < 95% 时回退 365 天
#   P2-4: ambush_scan 用 scan_results 最新日期
#   P2-5: ST 过滤正则修正
#   P2-6: phaseLabel 新增 🧬DNA训练
```

### 融资融券情绪 (`app/api/scan.py:get_margin_sentiment`) ⭐ v4.8

```python
# 改用 rzye (融资余额) 直接判断杠杆水平:
#   > 1.6万亿 = 亢奋 (注意风险) - 红色 #ef4444
#   1.2-1.6万亿 = 正常 - 绿色 #10b981
#   < 1.2万亿 = 谨慎 - 蓝色 #3b82f6
#
# 返回字段:
# {
#   "label": "融资余额 14,489亿",
#   "value": "14,489",
#   "unit": "亿",
#   "change": "+3247.2%",
#   "level": "正常",
#   "level_color": "#10b981",
#   "level_note": "杠杆水平正常",
#   "value_yi": 14489.0,
#   "short_balance_yi": 139.0,
# }

GET /api/scan/margin-sentiment
```

### 新闻事件 → Tushare 宏观数据 (v4.8 改造) ⭐

```python
# 旧方案 (已废弃): 依赖 stock_events/news_aggregated/news_verify 空白表
# 新方案: 统一用 Tushare 宏观数据 + 板块暴露系数

from app.services.macro_data import (
    compute_sector_macro_score,  # 板块宏观得分 (-3~+3)
    get_macro_snapshot,           # 当前宏观快照
    score_macro_impact,           # 大盘宏观得分
)
from app.services.factor_exposure import (
    get_sector_exposure,          # 板块对宏观因子的暴露系数
    get_commodity_affected_sectors,  # 商品→板块
)

# 改造位置:
# - deep_scorer.py: score_event_impact() → compute_sector_macro_score()
# - deep_scorer.py: news_aggregated/news_verify → 板块宏观暴露
# - holdings.py: news_signal → 预计算 sector_macro_cache
# - LearningPage: 新闻验证标签 → MacroSnapshotView (宏观快照展示)
```

### DNA 自动化加入 (`app/services/stock_dna_auto_join.py`) ⭐ v4.8

```python
from app.services.stock_dna_auto_join import (
    auto_join_for_alphaflow,  # 突破+TG买入 → 自动加入 DNA
    auto_join_for_scan,       # L3级股票 → 自动加入 DNA
    auto_join_for_holdings,   # 持仓新增/清仓 → 自动加入 DNA
    warmup_dna_samples,       # 轻量级批量预热 (仅生成样本,不训练)
)
# 自动加入机制已集成到:
#   - alphaflow_pool_service.py (池维护完成后)
#   - scan.py (扫描完成后, result_data.dna_auto_join)
#   - holdings.py (持仓新增/清仓后)
```

### 新闻采集优化 (`news_pipeline.py` + `scan.py`) ⭐ v4.8

```python
# 聚合接口 - 一次返回所有数据
GET /scan/news-dashboard  # 并行加载 events/margin/freshness/sector_heat/toplist

# 新鲜度检查
GET /scan/news-freshness  # 返回 should_crawl / should_analyze / recommendation

# 增量更新逻辑 (news_pipeline.py):
#   - < 2小时前爬取 → 跳过爬取
#   - < 6小时前分析 → 跳过 LLM 分析
# force=True → 完整执行所有步骤

# 前端分类工具 (NewsPage.tsx):
classifyMarket(ts_code) → 'main' | 'chinext' | 'sme'
filterByMarket(events, market) → filtered events
```

---

## 九、v7.0.32+ 系统评分 27 维 ⭐ v7.0.32 新增

### 9.1 评分维度清单 (27 维)

| # | 维度 | 字段名 (DB column) | dim_scores key | 类型 | 来源 |
|---|------|----------------------|-----------------|------|------|
| 1 | 技术面 | `tech_score` | `technical` | 老 | v3.0 |
| 2 | K线博弈 | `kline_score` | `kline_game` | 老 | v3.0 |
| 3 | 资金面 | `fund_score` | `fund_flow` | 老 | v3.0 |
| 4 | TG动量 | `tg_momentum` | `tg_momentum` | 老 | v3.0 |
| 5 | 量比 | `vol_ratio` | `vol_ratio` | 老 | v3.0 |
| 6 | ARBR情绪 | `arbr` | `arbr` | 老 | v3.0 |
| 7 | 行业Alpha | `sector_alpha` | `sector_alpha` | 老 | v3.0 |
| 8 | 大盘相对 | `market_relative` | `market_relative` | 老 | v3.0 |
| 9 | 估值 | `valuation` | `valuation` | 老 | v3.0 |
| 10 | 均线趋势 | `ma_trend` | `ma_trend` | 老 | v3.0 |
| 11 | 形态 | `pattern` | `pattern` | 老 | v3.0 |
| 12 | 趋势偏离 | `trend_deviation` | `trend_deviation` | 老 | v3.0 |
| 13 | BBI多空 | `bbi` | `bbi` | 老 | v3.0 |
| 14 | 箱体 | `multi_box` | `multi_box` | 老 | v3.0 |
| 15 | 趋势偏离 | `dist_low` | `dist_low` | 老 | v3.0 |
| 16 | J值 | `j_value` | `j_value` | 老 | v3.0 |
| 17 | 下跌风险 | `downside_risk` | `downside_risk` | 老 | v3.0 |
| 18 | 基本面 | `fundamentals` | `fundamentals` | 老 | v3.0 |
| 19 | 周线共振 | `weekly_resonance` | `weekly_resonance` | 老 | v4.2 |
| 20 | 龙虎榜板块 | `toplist_sector` | `toplist_sector` | 老 | v3.0 |
| 21 | 潜伏猎手 | `ambush` | `ambush` | 老 | v4.7 |
| 22 | 筹码胜率 | `chip_winner` | `chip_winner` | 老 | v4.5 |
| 23 | 筹码成本 | `chip_cost` | `chip_cost` | 老 | v4.5 |
| **24** | **MACD** | **`macd_dif/dea/bar`** | **`macd`** | **新 v7.0.32** | TDX 函数 |
| **25** | **KDJ** | **`kdj_k/d/j`** | **`kdj`** | **新 v7.0.32** | TDX 函数 |
| **26** | **RSI** | **`rsi_6/12/24`** | **`rsi_24`** | **新 v7.0.32** | TDX 函数 |
| **27** | **BOLL** | **`boll_upper/mid/lower/width/pos`** | **`boll`** | **新 v7.0.32** | TDX 函数 |
| **28** | **CCI** | **`cci`** | **`cci`** | **新 v7.0.32** | TDX 函数 |
| **29** | **筹码 winner_rate** | **`winner_rate`** | **`chip_winner_rate`** | **新 v7.0.32** | Tushare cyq_perf |

**注**: 27 = 23 老 + 5 新技术 (MACD/KDJ/BOLL/CCI + chip_winner_rate)

### 9.2 22 字段名清单 (v7.0.32 新增)

```python
# 技术指标 15 字段
macd_dif, macd_dea, macd_bar,
kdj_k, kdj_d, kdj_j,
rsi_6, rsi_12, rsi_24,
boll_upper, boll_mid, boll_lower, boll_width, boll_pos,
cci

# 筹码分布 7 字段
cost_5pct, cost_50pct, cost_95pct,
weight_avg, winner_rate,
cost_spread, price_vs_cost
```

### 9.3 全链路透传 (5 处必须改)

新加的字段必须穿透到:

| # | 文件 | 函数 | 作用 |
|---|------|------|------|
| 1 | `services/deep_scorer.py` | `_deep_normalize_phase` (line 770 后) | 写 `dimension_scores` dict |
| 2 | `services/llm_deep_analyzer.py` | `get_stock_context` + `_batch_get_stock_contexts` SQL | DeepSeek 接收 22 字段 |
| 3 | `services/llm_deep_analyzer.py` | `build_analysis_prompt` + `_build_tech_section` / `_build_chip_extended_section` | prompt 渲染 5 维技术 + 5 维筹码 |
| 4 | `api/result.py` | `get_final_results` `base_select` + dict 输出 | ResultPage SQL 加 22 字段 |
| 5 | `frontend/src/components/CuratedRankingView.tsx` | `checkGoldFilter` + `techCell` | 加 7 列 + 金过滤判定 |

### 9.4 dim_scores 兼容性

`_extract_score()` (scoring_trainer_v2.py:53) 兼容 3 种 schema:
- 嵌套: `{"macd": {"score": 7.5, "raw": 0.5}}` ✅ 优先
- 平铺: `{"macd": 7.5}` ✅ 回退
- 老 `_score` 后缀: `{"macd_score": 7.5}` ✅ 回退

新增 dim 字段时, 必须用嵌套格式 + score 子键。

### 9.5 v2 Trainer 调用 (v7.0.33)

```python
from app.services.scoring_trainer_v2 import train_4x2, train_single, load_training_data_v2
from app.services.market_gate import get_current_regime_simple, regime_to_market_style

# 自动检测当前市场训练 (默认行为)
result = await train_4x2(lookback_days=730)  # market_style=None → auto-detect

# 强制指定 regime 训练
result = await train_4x2(lookback_days=730, market_style='bull')
result = await train_4x2(lookback_days=730, market_style='bear')
result = await train_4x2(lookback_days=730, market_style='range')

# 加载训练数据 (按 regime 过滤)
X, y, syms, fns = await load_training_data_v2(
    lookback_days=730,
    horizon_days=5,
    model_type='win',
    market_style='bear',  # 只取 bear 段样本
)

# 单套训练 (含缺样本降级)
result = await train_single(
    horizon=5, model_type='win',
    lookback_days=730, archetype='__global__',
    market_style='bear',  # 自动降级: n<30 → 'all'
)
```

### 9.6 regime 标签 SQL (与 v1 一致)

```sql
-- 700001.TI LAG(10) close ±2%
WITH market_phases AS (
    SELECT trade_date,
           CASE
             WHEN LAG(close, 10) OVER (ORDER BY trade_date) IS NULL THEN 'range'
             WHEN (close - LAG(close, 10) OVER (ORDER BY trade_date))
                  / NULLIF(LAG(close, 10) OVER (ORDER BY trade_date), 0) * 100 > 2.0 THEN 'bull'
             WHEN (close - LAG(close, 10) OVER (ORDER BY trade_date))
                  / NULLIF(LAG(close, 10) OVER (ORDER BY trade_date), 0) * 100 < -2.0 THEN 'bear'
             ELSE 'range'
           END as phase
    FROM daily_kline WHERE ts_code = '700001.TI'
)
```

**注**: 必须用 700001.TI (唯一可用指数, 沪深300 在本项目没数据)。

### 9.7 数据回填脚本

```bash
# 回填 v7.0.32 新 22 字段 (含 commit bug 修复)
python -m scripts._backfill_tech_chip

# ⚠️ 该脚本需要 COMMIT, 已修复 (v7.0.33 commit 69d46c5b)
# ⚠️ 缺 COMMIT 时, executemany 在事务里执行, 连接 close 时回滚
#    表现: 脚本报告"实际更新 5676 条"但 DB 实际未变
```

### 9.8 /result/final API 加 22 字段 (索引)

`api/result.py` `base_select` 顺序:
- r[0..16]: 老字段 (含 details 在 r[17])
- r[17]: details
- r[18..39]: 22 个新字段 (macd_dif..price_vs_cost)
- r[40..44]: llm_score / hidden_risks / catalysts / resonance_type / weekly_tg_momentum

**新增字段时必须更新**: 1) base_select SQL 2) dict 输出索引 3) 总字段数 (现 82 字段)

### 9.9 Exclusion 踢出名单 (v7.0.34) ⭐ NEW

把分散的"踢出逻辑"统一到 `exclusion_list` 表 + 5 reasons.

#### 数据模型

```sql
exclusion_reasons (code PK, name, category, description, auto_refresh)
exclusion_list (symbol PK, reason_code, added_at, expires_at, note)
```

#### 5 个 reason

| reason | 数据源 | 周期 | 性质 |
|--------|--------|------|------|
| TECH_BOARD | 688 开头的 ts_code | 每次刷新 | 永久 |
| BJ_BOARD | 920 开头的 ts_code | 每次刷新 | 永久 |
| ST_NAME | Tushare stock_st | 每次刷新 | 永久 |
| PE_LOSS | Tushare income_vip (n_income<0) | 季度末过期 | 季度切换 |
| INSOLVENT | Tushare balancesheet_vip (total_liab > total_assets) | 每次刷新 | 永久 |

#### 涉及文件

- `backend/app/models/data_models.py` — `ExclusionReason` + `ExclusionList` ORM
- `backend/app/services/tg_engine.py` — exclusion_list 加载 (在 ST/涨停过滤**前**)
- `backend/scripts/refresh_exclusion_list.py` — 5 reason 整合刷新
- `backend/scripts/init_exclusion_list.py` — 一次性 TECH/BJ 初始化
- `backend/app/api/admin.py` — `POST /api/admin/refresh-exclusion` API
- `frontend/src/pages/ScanPage.tsx` — "🗂️ 股票信息对齐" 按钮

#### 手动触发刷新

```bash
# 季度初跑 (4/1, 7/1, 10/1, 1/1) 或按需手动
cd Stock/backend
python scripts/refresh_exclusion_list.py

# 或前端 ScanPage 点 "🗂️ 股票信息对齐" 按钮
```

#### 调试 API

```bash
# 看当前 exclusion_list 状态
curl http://localhost:8000/api/admin/exclusion-stats
# {
#   "by_reason": [
#     {"reason": "TECH_BOARD", "total": 599, "with_expires": 0, "permanent": 599, ...},
#     ...
#   ]
# }
```

### 9.10 v2 Trainer 训练数据筛选 (v7.0.34) ⭐ NEW

v2 trainer SQL 加 3 个过滤, 避免学习"被踢出票"的模式.

```sql
-- 1. 排除 exclusion_list (5 reasons 全部, 跨期自动过期)
AND NOT EXISTS (
    SELECT 1 FROM exclusion_list ex
    WHERE ex.symbol = rt.symbol
      AND (ex.expires_at IS NULL OR ex.expires_at > NOW())
)
-- 2. 排除股价 < 5 元
AND rt.close_price >= 5.0
-- 3. 排除当日涨停 (按板区分阈值)
AND NOT EXISTS (
    SELECT 1 FROM daily_kline dk
    WHERE dk.ts_code = rt.symbol AND dk.trade_date = rt.scan_date
      AND (
        ((dk.ts_code LIKE '6%' OR dk.ts_code LIKE '00%') AND dk.close / dk.open - 1 >= 0.095)
        OR ((dk.ts_code LIKE '30%' OR dk.ts_code LIKE '688%') AND dk.close / dk.open - 1 >= 0.195)
        OR ((dk.ts_code LIKE '8%' OR dk.ts_code LIKE '4%') AND dk.close / dk.open - 1 >= 0.295)
      )
)
```

#### 重训命令 (lookback_days=880, ~2024-02 到 2026-06-20)

```python
# 64 套权重覆盖重训 (4 全局 + 4 archetype)
from app.services.scoring_trainer_v2 import train_4x2

for ms in ["all", "bear", "bull", "range"]:
    await train_4x2(lookback_days=880, archetypes=["__global__"], market_style=ms, dry_run=False)
for arch in ["growth_tech", "large_bluechip", "small_speculative", "value_defensive"]:
    await train_4x2(lookback_days=880, archetypes=[arch], market_style="all", dry_run=False)
```

### 9.11 扫描阶段数据源独立性 (重要澄清, v7.0.34)

**问题**: "tg 扫描会屏蔽涨停股票, 潜龙猎手专门吃涨停股票, 会不会因为前面屏蔽了, 后面的潜龙猎手吃不到数据了?"

**答案**: 不会. 3 个扫描阶段数据源**完全独立**:

| 阶段 | 数据源 | 屏蔽涨停影响 |
|------|--------|-------------|
| TG 扫描 (`tg_engine.scan_all_stocks`) | scan_results (本次踢出) | ✅ 是 |
| 潜龙猎手 (`ambush_scanner.run_ambush_scan`) | **直接读 daily_kline** (自己算 close/open) | ❌ 不受影响 |
| AlphaFlow (`alphaflow_pool.daily_scan`) | **直接读 daily_kline** | ❌ 不受影响 |

**业务影响**: 0. 屏蔽涨停股符合 TG 阶段"提前埋伏"逻辑, 不会减少潜龙猎手的输入数据.

---

## 十、数据库迁移脚本 ⭐ v4.7 新增

### 迁移管理工具

| 脚本 | 用途 |
|------|------|
| `scripts/migrations.py` | 迁移脚本管理器 (集中管理所有 DDL) |
| `scripts/db_health_check.py` | 数据库健康检查 |
| `scripts/fix_missing_tables.py` | 修复缺失的表和列 |

### 使用方法

```bash
cd Stock/backend
set PYTHONPATH=.

# 列出所有迁移
python scripts/migrations.py --list

# 检查迁移状态
python scripts/migrations.py --check

# 执行待处理的迁移
python scripts/migrations.py --run

# 运行健康检查
python scripts/db_health_check.py
```

### 迁移脚本清单

| ID | 名称 | 描述 | Schema |
|----|------|------|--------|
| 1 | ai_insights | LLM 分析结果存储表 | public |
| 2 | idx_ai_type_date | ai_insights 索引 | public |
| 3 | daily_kline_adj_factor | 前复权因子列 | public |
| 10 | alphaflow_pool_history_micro_score | 微分维度分数 | public |
| 11 | idx_fingerprint_symbol_date | 指纹复合索引 | public |
| 20 | idx_param_library_arch_st | 影子训练索引 | public |
| 21 | idx_beliefs_archetype | 贝叶斯信念索引 | public |
| 22 | idx_experience_archetype | 经验回放索引 | public |
| 23 | idx_prediction_symbol_date | 预测记录索引 | public |
| 30 | news_verify_t5_columns | T+5 相关列 | public |
| 31 | stock_deep_feedback_trade_date | 交易日期列 | public |
| 40 | idx_signal_history_symbol | 信号历史索引 | public |
| 50-54 | stock_dna.* | DNA 个性化模型表 | stock_dna |

### 数据库健康检查

**检查项目**:
1. 关键表及其期望列
2. stock_dna schema 表
3. 关键索引
4. 数据完整性（孤儿数据）

**退出码**:
- 0 = HEALTHY（健康）
- 1 = WARNING（警告，可修复）
- 2 = ERROR（错误，必须修复）

### 误删库重建流程

```bash
# 1. 运行所有迁移
python scripts/migrations.py --run

# 2. 检查健康状态
python scripts/db_health_check.py

# 3. 如有问题，运行修复脚本
python scripts/fix_missing_tables.py
```
