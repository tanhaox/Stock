<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-06-17 | Updated: 2026-06-20 -->

# Stock

## Purpose

**Stock Analyst — A股量化分析系统 v7.0.34**。提供 A 股市场扫描、深度分析、自学习闭环、个性化模型训练、新闻事件驱动等能力。核心子系统：AlphaFlow / Chip / Veteran / XGBoost V2 / DNA 实验室 / 自学习闭环 / 铁三角死规则 (v7.0.30) / v2 学习链路 (v7.0) / Exclusion 踢出名单 (v7.0.34, 5 reasons Tushare 集成) / 27 维评分 (含 v7.0.32 新增 5 维)。

## 关键文件

| File | Description |
|------|-------------|
| `README.md` | 项目说明（v7.0.34） |
| `package.json` | 前端配置（name=frontend, scripts: dev/build/preview/test） |
| `StockAnalyst.bat` | Windows 启动脚本 |
| `docs/architecture.md` | ⭐ **必读** - 完整架构、数据流、调用链、数据库、变更日志 |
| `docs/DEVELOPER_GUIDE.md` | ⭐ **必读** - 跨模块依赖、连带影响表、全局约定、统一工具库 API |
| `docs/news.md` | 新闻管线数据流、LLM 输出规范、跨源去重 |
| `docs/tushare.md` | Tushare Pro 接口参数速查 + v7.0.34 Exclusion 集成接口 (stock_st/income_vip/balancesheet_vip) |
| `docs/PHASE_PLAN.md` | DNA 实验室施工执行清单 |
| `docs/frontend-api-db-audit.md` | 前端/API/DB 审计报告 |
| `docs/improvements/` | 改进意见系统（CHECKPOINT 机制） |

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `backend/` | Python FastAPI 后端（API 路由、业务服务、核心工具） |
| `frontend/` | React/TypeScript 前端（Vite + 自研组件库） |
| `browser-extension/` | 浏览器扩展（行情/选股） |
| `docs/` | 架构与开发文档（见上） |
| `logs/` | 运行日志 |

## For AI Agents

### Stock 开发任务必读

1. **必读** `docs/DEVELOPER_GUIDE.md` §7 "统一工具库" — 先确认无全局实现
2. **必读** `docs/DEVELOPER_GUIDE.md` 对应文件的"连带影响"表
3. **必读** `docs/architecture.md` 数据流章节
4. **必读** `../CLAUDE.md` "Stock Analyst 量化系统" 章节的禁止事项

### 全局禁止事项（红线）

- ❌ 内联 NaN 守卫 → 用 `safe_float()` / `sanitize_array()`
- ❌ 自写 700001.TI SQL 查询 → 用 `get_benchmark_closes()`
- ❌ 日历日超额收益 → 用 `compute_excess_return()`（交易日计数）
- ❌ 2/3 参数 Progress 回调 → 4 参数标准 + `make_progress_adapter()`
- ❌ `startswith('6') → .SH` 内联 → 用 `normalize_ts_code()`
- ❌ 跳过 `stock_name_cache` → 用 `get_stock_name()`
- ❌ 新增任何除权检测代码 → 直接用 `daily_kline.adj_factor`（已前复权）
- ❌ 修改现有系统代码只为 DNA 实验室接入 → DNA 在独立 schema/API/前端
- ❌ **删除数据库或表**（400万+ K线数据永久丢失）→ 只用 SELECT/INSERT/UPDATE
- ❌ **DROP/TRUNCATE/DELETE 全表**（无法回滚）→ 用 UPDATE 逐行修改

### 核心子系统

| 子系统 | 位置 | 描述 |
|--------|------|------|
| AlphaFlow | `backend/app/services/alphaflow*.py` | 锁死→老兵→评估→XGBoost→策略→池 |
| Chip | `backend/app/services/chip_*.py` | 筹码分析 |
| Veteran | `backend/app/services/veteran*.py` | 老兵模型（影子训练） |
| XGBoost V2 | `backend/app/services/xgboost*.py` | 第二代 XGBoost 训练 |
| DNA 实验室 | `backend/app/services/stock_dna/` | 10 模块 DNA 分析 |
| 新闻事件 | `backend/app/services/news_*.py` | 跨源新闻去重与 LLM 分析 |
| 自学习闭环 | `backend/app/services/learning_*.py` | 反馈驱动模型迭代 |
| **Exclusion 踢出** (v7.0.34) | `backend/scripts/refresh_exclusion_list.py` + `backend/app/api/admin.py` | 5 reasons (TECH_BJ / ST / PE / INSOLVENT) + 股票信息对齐按钮 |
| 统一工具库 | `backend/app/utils/` + `backend/app/core/` | `safe_float` / `normalize_ts_code` / `get_benchmark_closes` 等 |

### P0 工具（高频复用）

```
backend/app/utils/numpy_utils.py        # safe_float, sanitize_array
backend/app/core/market_data.py         # get_benchmark_closes, compute_excess_return
backend/app/core/progress.py            # make_progress_adapter
backend/app/utils/stock_code.py          # normalize_ts_code
backend/app/core/name_resolver.py        # get_stock_name (三级缓存)
```

### 前复权

`daily_kline` 表的 `adj_factor` 字段已前复权处理。**不要**新增除权检测代码，直接用 `daily_kline` 数据即可。

### Exclusion 踢出流程 (v7.0.34)

- 入口: 前端 ScanPage "🗂️ 股票信息对齐" 按钮
- API: `POST /api/admin/refresh-exclusion`
- 脚本: `backend/scripts/refresh_exclusion_list.py`
- 5 reasons: TECH_BOARD / BJ_BOARD / ST_NAME / PE_LOSS / INSOLVENT
- 集成位置: `tg_engine.scan_all_stocks` 在 ST/涨停过滤**前**加载 exclusion_list
- 调度: 季度初 (4/1, 7/1, 10/1, 1/1) 自动跑

### v2 Trainer 训练数据筛选 (v7.0.34)

`scoring_trainer_v2.py` SQL 加 3 个过滤:
```sql
-- 1. 排除 exclusion_list
AND NOT EXISTS (SELECT 1 FROM exclusion_list ex WHERE ex.symbol = rt.symbol ...)
-- 2. 排除股价 < 5 元
AND rt.close_price >= 5.0
-- 3. 排除当日涨停
AND NOT EXISTS (SELECT 1 FROM daily_kline dk WHERE dk.close/open - 1 >= 9.5%/19.5%/29.5%)
```

## 依赖

- Python 3.13 + FastAPI 0.110 + asyncpg
- PostgreSQL 15 (127.0.0.1:15432, db=stock_data)
- React 19 + Vite 8 + Ant Design 6
- DeepSeek API + Tushare Pro (8000 积分)
- XGBoost 2.x + scikit-learn