# Stock Analyst 文档索引

> **版本**: v7.0.34 | **日期**: 2026-06-20 | **更新**: 踢出名单 (exclusion_list) 5 个 reason 整合 + 股票信息对齐按钮 + v2 Trainer 训练数据筛选
> **关联**: [Stock/AGENTS.md](../AGENTS.md) — 项目级 AI 理解文档

---

## 📁 目录结构

```
docs/
├── README.md                          ← 本文件 (文档索引)
├── AGENTS.md                          ← docs 目录 AI 理解文档
│
├── 核心文档 (已实现, 当前生效)
│   ├── architecture.md                ← 系统架构全景图 (v7.0.30)
│   ├── DEVELOPER_GUIDE.md             ← 开发施工手册 (v2.4)
│   ├── PHASE_PLAN.md                  ← Phase 执行计划 (v1.4)
│   ├── tushare.md                     ← Tushare Pro 接口速查 + 特色数据调研
│   ├── news.md                        ← 新闻事件分析系统
│   └── frontend-api-db-audit.md      ← 前端→后端→数据库 三重交叉审计
│
├── improvements/                      ← 改进意见管理系统
│   ├── 进行中/
│   │   └── 20260619-v2按regime训练.md
│   └── 已完成/
│       ├── 20260618-v7.0.30-铁三角死规则接入.md
│       ├── 20260618-v7.0.31-*.md
│       └── 20260619-v7.0.32-系统评分维度扩展.md
│
└── 未来构想/                          ← 未实现想法 + 旧设计方案
    ├── 未实现想法.md                   ← 汇总所有未实施的计划
    └── 旧设计方案/                     ← 历史的 v1.x 设计蓝图 (v6.0 已超出)
        ├── P0级基建顶层设计.md
        ├── 潜龙猎手.md
        ├── 自学习升级.md
        ├── 系统基建替换退役计划.md
        └── 系统基建与退役任务规划.md
```

---

## 📚 核心文档概览

| 文档 | 行数 | 状态 | 用途 |
|------|------|------|------|
| **[README.md](README.md)** | — | ✅ 索引 | 本文件 (文档导航) |
| **[AGENTS.md](AGENTS.md)** | ~120 | ✅ 索引 | docs 目录 AI 理解文档 |
| **[architecture.md](architecture.md)** | ~2050 | ✅ 完整 | 系统架构全景图 (v7.0.30, 含 v6.0→v7.0.33 变更日志) |
| **[DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md)** | ~700 | ✅ 完整 | 开发施工手册 (v2.6, 含 exclusion_list 5 reasons + v2 trainer 数据筛选) |
| **[PHASE_PLAN.md](PHASE_PLAN.md)** | ~280 | ✅ 准确 | Phase 执行计划 (v1.4 含 Phase 56-78) |
| **[tushare.md](tushare.md)** | ~2540 | ✅ 准确 | Tushare Pro 接口速查 + 特色数据调研 (v6.0.5 整合) |
| **[news.md](news.md)** | ~580 | ✅ 完整 | 新闻事件分析系统 (v3.1) |
| **[frontend-api-db-audit.md](frontend-api-db-audit.md)** | ~156 | ✅ 准确 | 前端→后端→数据库 三重交叉审计报告 |
| **[improvements/](improvements/)** | — | ✅ 完整 | 改进意见管理系统 |
| **[未来构想/](未来构想/)** | — | 🆕 汇总 | 未实施想法 + 旧设计方案 |

---

## 🗂️ AGENTS.md 层级索引 (AI 理解文档)

> 层级索引帮助 AI 快速定位代码结构，无需阅读全部代码。

| 层级 | 文件 | 内容 |
|------|------|------|
| **L0 根** | [Stock/AGENTS.md](../AGENTS.md) | 项目概览、技术栈、核心约束 |
| **L1 backend** | [backend/AGENTS.md](../backend/AGENTS.md) | 后端根目录、脚本、模型 |
| **L1 app** | [backend/app/AGENTS.md](../backend/app/AGENTS.md) | FastAPI 核心、core/utils 模块 |
| **L1 frontend** | [frontend/AGENTS.md](../frontend/AGENTS.md) | 前端根目录 |
| **L1 src** | [frontend/src/AGENTS.md](../frontend/src/AGENTS.md) | React 页面/组件、API 客户端 |
| **L2 services** | [backend/app/services/AGENTS.md](../backend/app/services/AGENTS.md) | 80+ 服务模块分类索引 |
| **L2 api** | [backend/app/api/AGENTS.md](../backend/app/api/AGENTS.md) | 17 路由模块、端点清单 |
| **L2 backend/docs** | [backend/docs/AGENTS.md](../backend/docs/AGENTS.md) | 后端补充文档 (宏观映射) |
| **L2 docs** | [docs/AGENTS.md](AGENTS.md) | 文档目录索引 |

### 快速定位指南

```
想找... → 查看...
──────────────────────────────────────────────────────
服务实现 → backend/app/services/AGENTS.md
API 端点 → backend/app/api/AGENTS.md
前端页面 → frontend/src/AGENTS.md
核心工具 → backend/app/AGENTS.md §Core Modules
数据库表 → docs/DEVELOPER_GUIDE.md §三、数据库表
开发约束 → Stock/AGENTS.md §For AI Agents
文档索引 → docs/README.md (本文件)
```

---

## 🔥 核心哲学

> **TG 扫描的真正目标,不是触发多少信号,是 TOP 20 是不是真的赚钱。**

### 核心信念

- **层层筛选,只为 TOP N**:TG 扫描从全市场 ~5000 只开始,经过技术指标过滤、L1/L2/L3/L4/L5 分级、周线共振、动量连续、买价偏、买涨幅偏、大单共振等等**层层筛选**——但所有这一切的**最终目的**,都是为了让**最后筛选出的 TOP 20(及少部分 TOP 30/50)** 是**对未来盈利最优的股票**。
- **系统要保证的是**: 通过层层筛选后的股票,对于未来盈利是最优的。这才是系统的目的。

### 演进原则

- **不怕开发新指标,我们并不保守。** 我们欢迎任何新思路、新维度、新算法。
- **我们只是从不同维度增强系统。** 新指标和老指标可以共存,先用历史回测和实战数据说话。
- **最终用实战数据(历史回测),来公平比对,每一次迭代。** 任何改动,必须用真实历史数据(A/B 测试)证明其价值。
- **让更优秀的指标、流程,留在当前的系统里。** 优胜劣汰,这是系统的演进机制。

### 实验方法论

- **多轮对比**:不只看一轮数据,跑多轮(反弹期/调整期/分化期)取汇总
- **公平基准**:新旧版本跑**同一份 K 线数据**,不偷换输入
- **实战视角**:测 TOP 10/20/30 by TG 动量的真实收益,这是用户最常用的策略
- **保留改进意见**:`docs/improvements/` 留完整对比报告,**失败案例也归档**(避免重蹈覆辙)
- **回退机制**:任何改动 git 单行可回退,不破坏系统稳定性

> **2026-06-18 实战案例**: 4 轮 A/B 对比(2026-03-02 / 04-15 / 05-15 / 06-11,共 5514 vs 340 只信号)显示,旧版"动量连续"算法 T+5 平均 +1.20%,新版 6+ 子条件算法 T+5 平均 -0.50%。结论: **新版严格筛选反而是"高位接盘",旧版宽松跟随趋势更赚钱**。详见 [`improvements/已完成/20260618-TGIndicator-AB对比测试报告.md`](improvements/已完成/20260618-TGIndicator-AB对比测试报告.md)

---

## 🎯 快速导航

### 新开发者必读

```
1. [architecture.md] 系统全景 → 了解系统架构
2. [DEVELOPER_GUIDE.md] 开发手册 → 开始编码前必读
3. [tushare.md] 接口速查 → 了解 Tushare 接入
```

### 日常开发参考

```
1. [DEVELOPER_GUIDE.md] §零 统一工具库 → 查找全局工具函数
2. [tushare.md] → Tushare API 参数速查
3. [frontend-api-db-audit.md] → API 端点验证
```

### 系统升级参考

```
1. [PHASE_PLAN.md] → 了解已完成的 Phase
2. [architecture.md] §10 变更日志 → 历史版本变更
3. [improvements/已完成/](improvements/已完成/) → 已落地的改进意见
```

### 未实现想法

```
1. [未来构想/未实现想法.md](未来构想/未实现想法.md) → 汇总未实施的计划
2. [未来构想/旧设计方案/](未来构想/旧设计方案/) → 历史的 v1.x 设计蓝图 (v6.0 已超出)
```

---

## 📊 系统当前状态 (v7.0.34)

### 核心能力

| 能力 | 状态 | 版本 |
|------|------|------|
| 潜龙池动态监控（首板 → 龙抬头） | ✅ 运行中 | v6.0.5 |
| TG 全市场扫描 | ✅ 运行中 | v7.0.31 |
| AlphaFlow 主升浪捕获 | ✅ 运行中 | v4.9 |
| **v2 学习链路 (regime 训练)** | ✅ **生产** | **v7.0.33** |
| DNA 个性化模型 | ✅ 运行中 | v4.5 |
| 大神仙空卖出信号 | ✅ 运行中 | v4.7 |
| 周线双周期共振 | ✅ 运行中 | v4.2 |
| 新闻分类去重 | ✅ 运行中 | v4.8 |
| 龙虎榜精细化 | ✅ 运行中 | v4.8 |
| **系统评分 27 维 (含 v7.0.32 新 5 维)** | ✅ **运行中** | **v7.0.32** |
| **DeepSeek 接收 22 字段 (技术+筹码)** | ✅ **运行中** | **v7.0.32** |
| **Exclusion 踢出名单 5 reasons + UI 按钮** | ✅ **运行中** | **v7.0.34** |
| **v2 Trainer 训练数据筛选 (排除踢出票)** | ✅ **运行中** | **v7.0.34** |

### 潜龙池 v6.0+ 核心特性

| 特性 | 状态 | 实现 |
|------|------|------|
| 10 交易日无涨停 → 首板候选 | ✅ 完成 | `first_limit_scanner.py` (v6.0.3: 改用 prev-based 涨跌幅) |
| S/A/B 级首板动态入池 | ✅ 完成 | `dragon_pool_service.join_pool_from_first_limit` |
| **5 触发踢出** (任一即踢) | ✅ 完成 | v6.0.4: consecutive_board + not_first_limit + ATR + fatigue + 10d |
| 浮出二板信号（waveback>0.3 + 分时验真） | ✅ 完成 | `detect_emerging` 强制调 verify_signals_with_minute_bars |
| 5 个 API 端点 + 4 个 SSE 事件 | ✅ 完成 | `api/dragon.py` + scan.py 阶段 4 |
| 每日收盘后调度 | ✅ 完成 | `task_update_dragon_pool` 加入 scheduler |
| **前端"二波型"→"龙抬头"** | ✅ 完成 | v6.0.2 |
| **前端"首板监控"显示监控天数 + 回调** | ✅ 完成 | v6.0.2 (X/10 天进度条) |
| **A 股惯例红涨绿跌** | ✅ 完成 | v6.0.5 全系统颜色统一 |

### v7.0.32 系统评分维度扩展 (NEW)

| 维度 | 类型 | 阈值 | 评分规则 |
|------|------|------|----------|
| **MACD** | 技术 | DIF 0轴 | 0轴上=多头 (5-10), 下=空头 (0-5) |
| **KDJ** | 技术 | J 值 0-100 | <20 超卖 (8-10), >80 超买 (2-5), 中=5 |
| **RSI_24** | 技术 | 0-100 | <30 超卖, >70 超买, 中=5 |
| **BOLL** | 技术 | boll_pos 0-1 | 0.3-0.7 中位, <0.1 下轨外 (9), >0.9 上轨外 (3) |
| **CCI** | 技术 | -300~300 | ±100 阈值, 极端值反向 |
| **筹码** | 分布 | 6 字段 | 成本中位+宽度+主力成本+获利盘+价差 |

**训练样本 (T+5 verified, 1915 条)**:
- v7.0.32 升级前: macd 字段 6.5% 覆盖率 (124 条)
- v7.0.32 升级后 (回填): macd 字段 **64.4%** (1233 条) — **+57.9pt**

**regime 训练样本** (回填后, 730d lookback):
- bull: 145, bear: 428, range: 228 (全部 ≥30, 不会触发降级)

### v7.0.33 v2 Trainer 按 Regime 训练 (NEW)

| 组件 | 实现 | 状态 |
|------|------|------|
| `market_gate.regime_to_market_style()` | 6种市场状态 → 3种训练风格 | ✅ 完成 |
| `market_gate.get_current_regime_simple()` | 异步获取当前 regime | ✅ 完成 |
| `load_training_data_v2(market_style=)` | 700001.TI LAG(10) + ±2% 打 phase 标签 | ✅ 完成 |
| `train_single()` 缺样本降级 | n<30 → fallback to 'all' | ✅ 完成 |
| `train_4x2(market_style=None)` | 默认 auto-detect 当前市场 | ✅ 完成 |
| `get_4x2_status(market_style=)` | 加 market_style 参数 + 修复 multi-regime 覆盖 bug | ✅ 完成 |

**生产权重 (32 套)**:
- all 8 套: 1307 样本, cv_auc 0.4645-0.6788
- bull 8 套: 145 样本, cv_auc 0.4667-0.5505
- bear 8 套: 211-643 样本, cv_auc 0.2988-0.6519 (跨周期核心)
- range 8 套: 176-519 样本, cv_auc 0.4463-0.6662

**v1 vs v2 PK 结果 (3 天 × 3 组)**:
| 组 | v1 T+5 | **v2 T+5** | v1 T+10 | **v2 T+10** |
|----|--------|-----------|---------|------------|
| Top 5 | +0.01% | **+0.98%** | -0.94% | **+1.73%** |
| Rank 5-10 | -0.32% | **+0.54%** | -1.01% | -0.67% |

**结论**: v2 在 Top 5 显著优于 v1 (T+5 +0.97pt, T+10 +2.67pt)。`feature_flag.learning_v2_active=true` 已生产启用。

### P0 基建状态

| 模块 | 状态 | 实现 |
|------|------|------|
| M1 名称缓存 | ✅ 完成 | name_resolver.py 三级缓存 |
| M2 除权因子 | ✅ 完成 | adj_factor_chain + resync_all_kline.py |
| M3 筹码分布 | ✅ 完成 | daily_chip_perf (Tushare cyq_perf, 6 月起) |
| M4 资金流向 | ✅ 完成 | moneyflow_service.py |
| M5 分时数据 | ✅ 完成 | minute_on_demand.py (按需计算, 2026-06-19 修复) |
| M6 行业标签 | ✅ 完成 | sw_sector_index + stock_sector_registry |

---

## 🔗 文档关联

```
architecture.md (系统架构)
    ├── 系统全景 (两大管线、核心能力矩阵)
    ├── 模块架构 (10 个逻辑模块)
    ├── 数据流 (TG扫描、AlphaFlow、学习闭环)
    ├── 数据库架构
    └── 变更日志 (v6.0 → v6.0.5)

DEVELOPER_GUIDE.md (开发手册)
    ├── 核心文件连带影响表
    ├── DNA 个性化模型
    ├── 数据库表说明
    ├── 统一工具库 (§零)
    └── 常见补丁原因速查

PHASE_PLAN.md (执行计划)
    ├── Phase 执行清单 (56-78)
    ├── 执行状态总览
    └── 施工规则

tushare.md (API手册)
    ├── 沪深股票 API
    ├── 指数 API
    ├── 期货/期权 API
    ├── 宏观经济 API
    └── 特色数据接口 (cyq_*/moneyflow_hsgt/limit_list)

news.md (新闻系统)
    ├── 数据流
    ├── LLM 提示词
    ├── 去重引擎
    ├── 评分体系
    └── 事件衰减模型

frontend-api-db-audit.md (审计报告)
    ├── 页面健康度总览
    ├── API 端点列表
    └── 数据库一致性

improvements/ (改进意见)
    ├── 进行中/ (实施中)
    └── 已完成/ (已归档)

未来构想/ (未实现)
    ├── 未实现想法.md (汇总)
    └── 旧设计方案/ (历史 v1.x 蓝图)
```

---

## 📅 版本历史

| 版本 | 日期 | 更新内容 |
|------|------|----------|
| **v7.0.34** | **2026-06-20** | **🗂️ Exclusion 踢出名单 5 reasons 整合 (Tushare stock_st + income_vip + balancesheet_vip) + 前端"股票信息对齐"按钮 + v2 Trainer 训练数据筛选 (NOT EXISTS exclusion_list + close_price>=5 + 非涨停)** |
| **v7.0.33** | **2026-06-19** | **🧠 v2 Trainer 按 regime 训练: bull/bear/range 3 套独立权重 + 缺样本降级 + 自动检测当前市场 + 32 套生产权重激活 + v1 vs v2 PK 验证 Top 5 收益 +0.97pt** |
| **v7.0.32** | **2026-06-19** | **📊 系统评分维度扩展: 加 MACD/KDJ/RSI/BOLL/CCI/筹码 6 维 (22 字段) + 数据回填 5676 条 (macd 覆盖率 6.5%→64.4%) + DeepSeek 接收 22 字段 + ResultPage SQL 加 22 字段 + CuratedRankingView 加 6 列显示 + 金过滤判定** |
| v7.0.31 | 2026-06-18 | 🐉 5 触发踢出 + Dragon 端点补全 + 路由统一 + 数据一致性 bug 修复 + OSError64 稳定性修复 + MonitorPage 升级 |
| v7.0.30 | 2026-06-18 | 🚦 铁三角死规则接入 (5 条硬规则 + R6/R7/R8 软规则) |
| **v6.0.5** | **2026-06-17** | **🎨 A 股惯例：全系统涨跌幅颜色统一为红涨绿跌 + 📁 文档整理 (按已实现/未实现分类)** |
| **v6.0.4** | **2026-06-17** | **🐉 5 触发踢出：新增 `consecutive_board` (入池后涨停) + `not_first_limit` (入池前漏判) 规则** |
| **v6.0.3** | **2026-06-17** | **🩺 涨跌幅修复：`first_limit_scanner` 用 prev-based 替代 open-based + 修复 6 行 close_price=21.11 历史数据 + 名称修复 1666 行** |
| **v6.0.2** | **2026-06-17** | **🐉 前端改造：tab 名 "二波型→龙抬头" + "首板猎人→首板监控" + 加监控天数列 (X/10 天进度条 + 起点列 + 涨幅列)** |
| **v6.0.1** | **2026-06-17** | **🩺 冒烟测试：修复 `limit_cpt_list_service.py` Windows GBK 编码 bug（hot-sectors 端点从 500 → 200）** |
| **v6.0** | **2026-06-16** | **🐉 潜龙池动态监控：dragon_pool 表 + 4 个核心函数 + 5 端点 + 删连板天梯 + 10d 强制清理** |
| v5.5 | 2026-06-15 | 页面导航优化 + 分钟线防伪并发优化 |
| v5.4 | 2026-06-15 | ✅分钟线防伪优化：只检测高分股票(134→219)+并发降至10 |
| v5.3 | 2026-06-15 | 分钟线防伪流程改造+L3股票优先检测+潜伏猎手/形态识别待优化 |
| v5.2 | 2026-06-15 | P0-1批量查询优化+P1-4特征选择+v4.9 |
| v5.1 | 2026-06-15 | 添加 AGENTS.md 层级索引 (5 个 AI 理解文档) |
| v5.0 | 2026-06-14 | 整合所有文档，统一索引 |
| v4.8 | 2026-06-13 | DNA自动化+新闻改造+扫描重组 |
| v4.7 | 2026-06-09 | 大神仙空+AlphaFlow重构 |
| v4.6 | 2026-06-05 | 影子训练升级+宏观数据扩展 |
| v4.5 | 2026-06-07 | 系统级P0升级+DNA实验室 |
| v4.2 | 2026-06-03 | 周线共振+质量控制 |

---

## 📝 文档维护规则

1. **系统升级后必须更新**:
   - `architecture.md` §10 变更日志
   - `DEVELOPER_GUIDE.md` 全局约定
   - `PHASE_PLAN.md` 执行状态

2. **Phase 完成后更新**:
   - `PHASE_PLAN.md` 标记 ✅

3. **开始新想法时**:
   - 写入 `未来构想/未实现想法.md` (不要散落到 README 或其他文件)
   - 实施时移入 `improvements/进行中/`

4. **基础设施/旧设计变更后**:
   - 旧设计移到 `未来构想/旧设计方案/` (不要删除, 留作历史参考)

5. **新文件加 README 索引**:
   - 在本文件 `📚 核心文档概览` 表中添加一行
   - 在 `🔗 文档关联` 中添加章节引用

---

## 🔥 v7.0.34 变更要点 (本次升级)

### 🗂️ Exclusion 踢出名单 (5 reasons 整合)
- **核心问题**: TG 扫描前需要稳定可维护的踢出名单. 之前散落在 `tg_engine.py` (ST/涨停) + 数据库 (exclusion_list 之前是 PE_TTM 粗略)
- **5 个 reason**:
  | reason | 数据源 | 周期 | 数量 (6/20) |
  |--------|--------|------|------------|
  | TECH_BOARD | 688 开头 (全市场代码) | 每次刷新 | ~599 |
  | BJ_BOARD | 920 开头 (全市场代码) | 每次刷新 | ~318 |
  | ST_NAME | Tushare stock_st | 每次刷新 | ~211 |
  | PE_LOSS | Tushare income_vip (n_income<0) | 季度末过期 | ~733 |
  | INSOLVENT | Tushare balancesheet_vip (total_liab > total_assets) | 永久 | ~2 (跨 reason) |
- **tg_engine 集成**: ST/涨停过滤**前**加 exclusion_list 加载 (一次 SELECT, O(1) 查找)
- **前端按钮**: ScanPage "🗂️ 股票信息对齐" → `POST /api/admin/refresh-exclusion` → 一次性刷断链
- **API 路径**: `/api/admin/refresh-exclusion` (POST) + `/api/admin/exclusion-stats` (GET)
- **数据库表**:
  - `exclusion_reasons` (字典表, 5 条): code, name, category, auto_refresh
  - `exclusion_list` (踢出名单): symbol (PK), reason_code, added_at, expires_at, note
- **脚本**:
  - `scripts/refresh_exclusion_list.py`: 整合所有 5 reason 刷新逻辑 (季度初跑, 按钮触发)
  - `scripts/init_exclusion_list.py`: 一次性初始化 TECH/BJ (现在已被 refresh 包含)

### 🧠 v2 Trainer 训练数据筛选 (v7.0.34)
- **核心改进**: trainer SQL 加 3 个过滤, 避免学习"被踢出票"的模式
- **过滤条件**:
  ```sql
  -- 1. 排除 exclusion_list (5 reasons 全部)
  AND NOT EXISTS (
      SELECT 1 FROM exclusion_list ex
      WHERE ex.symbol = rt.symbol
        AND (ex.expires_at IS NULL OR ex.expires_at > NOW())
  )
  -- 2. 排除股价 < 5 元
  AND rt.close_price >= 5.0
  -- 3. 排除当日涨停
  AND NOT EXISTS (
      SELECT 1 FROM daily_kline dk
      WHERE dk.ts_code = rt.symbol AND dk.trade_date = rt.scan_date
        AND dk.close/open - 1 >= 9.5%/19.5%/29.5% (按板)
  )
  ```
- **lookback_days = 880** (~2024-02-01 到 2026-06-20), 64 套权重覆盖重训 (8 全局 + 4 archetype)
- **OBV 仍 Top 10 有效特征**: T+2 win OBV=-0.171 排名 #8/29; T+10 win OBV=+0.151 排名 #12/23

### 潜龙猎手不依赖 TG 扫描 (重要设计)
- **澄清**: 之前担心"tg_engine 屏蔽涨停后, 潜龙猎手会无数据"
- **答案**: ambush_scanner 和 alphaflow_pool **直接读 daily_kline 表**, 跟 tg_engine 的 scan_results 互不干扰
- **数据流独立**:
  - TG 扫描: scan_results (受 exclusion_list 影响)
  - 潜龙猎手: daily_kline (自己算 `(close-open)/open` 找涨停)
  - AlphaFlow: daily_kline (主升浪特征独立算)
- **业务影响**: 0 (3 个阶段数据源完全独立, 屏蔽涨停股符合 TG 阶段"提前埋伏"逻辑)

---

## 🔥 v7.0.33 变更要点 (本次升级)

### 🧠 v2 Trainer 按 Regime 训练 (v7.0.33)
- **核心问题**: 旧 `train_4x2(market_style="all")` 不按市场状态分组, 牛市训的权重在熊市完全失效 (胜率 74.8% → 24.3%, cv_auc 0.45)
- **核心方案**: 700001.TI LAG(10) ±2% 打 phase 标签 (bull/bear/range), v2 trainer 按 phase 分组训练
- **4 步流水线**:
  1. `market_gate.regime_to_market_style()`: 6 种状态 → 3 种训练风格
  2. `load_training_data_v2(market_style=...)`: 加 phase 过滤 SQL
  3. `train_4x2(market_style=None)`: 默认 auto-detect 当前市场, 缺样本降级到 all
  4. 32 套生产权重 (8 all + 8 bull + 8 bear + 8 range)
- **关键修复**:
  - `_backfill_tech_chip.py` 缺 commit bug (executemany 后没 COMMIT, 数据回滚)
  - `get_4x2_status()` multi-regime 覆盖 bug (用 (h, mt) 作 key, 32 套互相覆盖)
- **验证**: v1 vs v2 PK 3 天 × 3 组, Top 5 名 v2 收益 +0.98% vs v1 +0.01% (+0.97pt)
- **生产状态**: `feature_flag.learning_v2_active=true` 已激活, 扫描 100% 走 v2

### 📊 系统评分维度扩展 (v7.0.32)
- **新加 22 字段**: macd_dif/dea/bar, kdj_k/d/j, rsi_6/12/24, boll_upper/mid/lower/width/pos, cci, cost_5/50/95pct, weight_avg, winner_rate, cost_spread, price_vs_cost
- **数据回填**: `_backfill_tech_chip.py` 修复 commit bug + 5676 条回填 (2024-01 ~ 2026-05)
- **覆盖率提升**:
  - T+5 verified (1915 条) macd 字段: 6.5% → 64.4% (+57.9pt)
  - 筹码字段: 16.6% (受 Tushare cyq_perf 限制, 仅 6 月起)
- **传导路径** (全链路修复):
  1. `deep_scorer.py` dimension_scores 加 5 维 (macd/kdj/boll/cci/rsi_24/chip_winner_rate) 让 v2 trainer 能训练
  2. `_batch_get_stock_contexts` + `get_stock_context` SQL 加 22 字段
  3. `llm_deep_analyzer.build_analysis_prompt` 加 2 个格式化函数 (`_build_tech_section` + `_build_chip_extended_section`)
  4. `/result/final` base_select 加 22 字段 (line 87-95), dict 输出 索引重排
  5. `CuratedRankingView` 加 7 列 (MACD/KDJ/RSI/BOLL/CCI/成本/信号) + 1 行展开区 + 金过滤判定
- **金过滤逻辑**:
  - ✓ 金过滤: 至少 4 维度 + 全 isGold + 没有 isWarn
  - ⚠ 风险: 任何 KDJ/RSI/BOLL/CCI 超买 OR price_vs_cost > 20%
  - 条件: MACD 多头 + KDJ 20-80 + RSI 30-70 + BOLL 0.2-0.8 + CCI ±100 + 成本贴近

### 🐉 5 触发踢出 (v6.0.4, 历史)
| # | reason | 触发条件 |
|---|--------|---------|
| 1 | `atr_stop` | exit_signal_detector critical/high |
| 2 | `fatigue_broken` | fatigue_detector broken/capitulation |
| 3 | `time_decay_10d` | days_in_pool >= 10 |
| 4 | `not_first_limit` | added_at 前 10 天内 prev-based 涨幅 ≥9.9% |
| 5 | `consecutive_board` | added_t 后任何一天 prev-based 涨幅 ≥9.9% |

### 🩺 涨跌幅 prev-based 修复 (v6.0.3, 历史)
- `first_limit_scanner.py` `check_first_limit` + `get_today_limit_list` 改用 `LAG(close)` 算 prev-based 涨幅
- 修复 6 行 close_price=21.11 历史数据 + 同步 dragon_pool.first_limit_close
- 修复 1666 个 first_limit_up.name 错误

### 📁 文档整理 (v6.0.5, 历史)
- **已实现归档** → 删除: `db_recovery_report.md`, `post_deployment_monitoring.md`, `二次涨停` (空), `待处理问题清单.md`, `improvements/residual-issues.md`
- **未实现汇总** → `未来构想/未实现想法.md`: 持仓管理交易记录导入 + NM 算法优化 + 新闻利空过滤扩展
- **旧设计归档** → `未来构想/旧设计方案/`: P0级基建顶层设计 / 潜龙猎手 / 自学习升级 / 系统基建替换退役计划 / 系统基建与退役任务规划
- **特色数据整合** → `tushare.md` 第九/十/十一章 (原 `tushare_special_data.md`)

---

📌 **待清理** (1 周系统稳定后):
- `services/ambush_scanner.py` (旧"潜伏猎手", 仍被 14 维评分使用)
- `api/ambush.py` (/hot-sectors 端点保留)
- `services/limit_cpt_list_service.py` (v6.0.1 修复完成, 业务保留)

---

**维护**: 每次代码提交时检查是否需要更新文档 (按 §文档维护规则)。
