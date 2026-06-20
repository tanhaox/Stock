# Stock Analyst Phase 执行计划 v1.4

> **用途**: 给开发窗口的 AI 使用。配合 `docs/DEVELOPER_GUIDE.md` 一起用。
> **工作流**: 每个 Phase 按顺序执行 → 读完"施工前检查"→ 改代码 → 跑自检 → 标记 ✅
> **更新**: v1.4 2026-06-17 添加 Phase 75-78 (前端改造 + 涨跌幅修复 + 5 触发踢出 + A 股颜色)

## 使用方式

```
步骤 0: 执行前先 grep "你要改的文件名" docs/DEVELOPER_GUIDE.md → 读连带影响
步骤 1: 选中下一个 ⏳ Phase，读完"施工前检查"
步骤 2: 改代码
步骤 3: 跑自检命令 → 通过后标记 ✅ 并写入执行日期
步骤 4: 回到步骤 1
```

---

## Phase 执行清单

### Phase 56 — signal_history enrichment 回填
- **状态**: ⚠️ 部分完成 (58/5851, 源数据不够)
- **文件**: `scripts/backfill_signal_enrichment.py`
- **施工前检查**: grep "signal_history" docs/DEVELOPER_GUIDE.md
- **自检**: `python scripts/backfill_signal_enrichment.py` → `SELECT COUNT(CASE WHEN relative_position IS NOT NULL THEN 1 END) FROM signal_history` → 预期 > 50%
- **已知限制**: 旧 analysis_scores 不含 enrichment, 回填只能覆盖 Phase 26e 之后产生的记录

### Phase 57 — T+5/T+15 验证扩展
- **状态**: ✅ 2026-06-06
- **文件**: `scripts/verify_recommendations.py`
- **执行结果**: verified_2d=318 (wr=65.7%), verified_5d=314 (wr=25.2%), verified_15d=0 (数据积累中)

### Phase 58 — 替换预测特征中的近似 sector gap 特征
- **状态**: ✅ (Phase 32a/34 已覆盖)
- **备注**: `x_real_sector_5d` 等 7 维真实板块特征已由 Phase 32a/34 实现, 无近似特征需替换

### Phase 59 — stk_mins 本地缓存 (sanxian 分时图加速)
- **状态**: ✅ 2026-06-06
- **文件**: `app/api/sanxian.py`
- **改动**: `get_intraday()` 个股优先从 `min_kline` 本地缓存读取 (≥8根bar=有足够数据), 缺才调 `stk_mins` API

### Phase 60 — 龙虎榜因子深度扩展
- **状态**: ✅ 2026-06-06
- **文件**: `app/services/predictive_features.py`
- **改动**: 69维 (63+6): `tl_inst_continuous`, `tl_seat_quality`, `tl_net_trend_10d`, `tl_consecutive_days`, `tl_avg_amount_ratio`, `tl_inst_net_streak`
- **AUC**: 0.6090 (69维, 无退化)
- **备注**: 6个新特征全未入 Top20 — 上榜样本占训练集 <5%, 任何 toplist 特征都稀疏。特征本身计算正确, 对实际命中龙虎榜的信号仍有辨识力

### Phase 61 — 三策略独立预测模型
- **状态**: ⚠️ 需要前置条件
- **备注**: 训练需要 strategy_label 字段, signal_history 当前只有 archetype。需先给 signal_history 加 tier 列或从 archetype 推导映射, 再分 tier 训练

### Phase 62 — 新闻验证结果前端展示 (Learning 页面)
- **状态**: ✅ 2026-06-06
- **文件**: `app/api/learning.py` (+`/news-verify-summary` 端点) + `frontend/src/pages/LearningPage.tsx` (新增"新闻验证"Tab)
- **执行结果**: 商品命中率表格展示, ≥5条信号的100+商品按总信号排序

### Phase 63 — 新闻验证闭环接入调度器
- **状态**: ✅ (Phase 50 已完成)
- **备注**: `task_verify_news_signals` 已在 scheduler_loop.py:23 + :50 注册

### Phase 64 — 持仓策略联动
- **状态**: ⏳
- **文件**: `app/api/holdings.py` + `frontend/src/pages/HoldingsPage.tsx`
- **依赖**: Phase 35 (三策略标签), Phase 39 (rec_index)
- **施工前检查**: grep "holdings\|HoldingsPage\|持仓" docs/DEVELOPER_GUIDE.md
- **任务**: 持仓页面展示推荐列表中对应该股的 rec_index、策略标签、新闻信号, 并生成 buy/sell/hold 建议
- **自检**: 打开 /holdings → 每只持仓可见推荐指数和策略标签

### Phase 65-67 — 前端展示补缺
- **状态**: ⏳
- **66**: `ResultPage.tsx` 展示 `predicted_return` + `rank_score`
- **67**: `HoldingsPage.tsx` 展示 `news_signal`

### Phase 68 — DNA 实验室自动化加入 (v4.8)
- **状态**: ✅ 2026-06-13
- **文件**: `app/services/stock_dna_auto_join.py`
- **机制**:
  - 机制1 (AlphaFlow): lock_state=breakout_up + TG买入 → 加入 DNA
  - 机制2 (TG扫描): 每日扫描完成后 L3 级股票 → 加入 DNA (异步, 不阻塞)
  - 机制3 (持仓): 持仓新增/清仓 → 相关股票加入 DNA

### Phase 69 — 新闻特征系统改造 (v4.8)
- **状态**: ✅ 2026-06-13
- **旧问题**: `stock_events`/`news_aggregated`/`news_verify` 表数据稀疏
- **新方案**: 使用 `compute_sector_macro_score()` + Tushare 宏观数据
- **改造位置**:
  - `deep_scorer.py`: `score_event_impact()` → `compute_sector_macro_score()`
  - `holdings.py`: `news_signal` 用 `sector_macro_cache` 预计算
  - `LearningPage.tsx`: 新闻验证标签 → 宏观快照展示

### Phase 70 — 新闻分类去重 + 龙虎榜精细化 (v4.8)
- **状态**: ✅ 2026-06-13
- **新闻分类** (`news_classifier.py`): SimHash 指纹 + 三级分类 (company/sector/macro/garbage)
- **个股摘要保留**: `get_stock_news_summary()` 从 `news_raw` + `stock_events` 合并
- **龙虎榜精细化** (`toplist_analyzer.py`):
  - 机构: 公募/北向/社保/QFII/私募
  - 游资: 顶级/一线/二线/三线
  - 共振: 5 级强度 + 标签
  - 净买持续性: 1/3/5 日
  - 智能缓存: 历史永久 / 当日交易 5min / 休市 1h
- **SSE 刷新接口**: `POST /api/scan/toplist-refresh`
- **新闻 SSE 接口**: `POST /api/scan/crawl-news` (浏览器加超时)
- **聚合接口**: `GET /api/scan/news-dashboard` (6 请求 → 1)
- **新鲜度 API**: `GET /api/scan/news-freshness` (skip/crawl/analyze/full 4 建议)
- **融资融券重写**: `get_margin_sentiment()` 改用 rzye (融资余额) 1.6/1.2 万亿阈值

### Phase 72 — 新闻页面重复标题修复
- **状态**: ✅ 06-14
- **文件**: `app/services/event_aggregator.py` (SimHash 汉明距离<8)

### Phase 74 — 🩺 v6.0.1 冒烟测试修复
- **状态**: ✅ 2026-06-17
- **触发**: 冒烟测试发现 `/api/ambush-signals/hot-sectors` 端点返回 500
- **Bug**: `services/limit_cpt_list_service.py` 在 Windows GBK (cp936) cmd 下报 SyntaxError
- **根因**:
  - 文件历史为 **CRLF 行尾** + Windows Python 3.13 默认 `cp936` locale
  - Python lexer 用 cp936 读 UTF-8 多字节字符 → 解码错误 → `"""` 配对失败
  - line 11 的 docstring 闭合符 `"""` 被错误转换为 `返回: ts_code, ...` 文字
- **修复**: 完全重写 `services/limit_cpt_list_service.py` (UTF-8 LF 编码) + 保留所有业务逻辑
- **自检结果**: 18/18 全过
  - 后端 API (GET 7 + POST 3): ✅ 全部 200
  - 前端页面 (8 个): ✅ 全部 200
  - 端到端业务流: ✅ join → update → evaluate 全部正常
- **预防**:
  - 未来创建 .py 文件一律用 LF 行尾 + Write 工具
  - 不在 Windows GBK cmd 下用 `open('r', encoding='utf-8')` 文本模式读写中文文件
- **关联文档**:
  - `docs/README.md` v6.0.1 章节
  - `docs/DEVELOPER_GUIDE.md` v2.2 头部
  - `docs/architecture.md` §10 v6.0.1 变更日志
- **改进意见归档**: `docs/improvements/进行中/20260616-潜龙池-v6.0.md` → `已完成/`

### Phase 75 — 🐉 v6.0.2 前端改造
- **状态**: ✅ 2026-06-17
- **需求**:
  - "二波型潜力" tab 改名"龙抬头"
  - "首板猎人" tab 改为显示"监控中"的首板股 (不是历史所有 S/A/B)
  - 加"监控天数"列 (10 天就要踢出, 必须显眼)
- **改动 `frontend/src/pages/AmbushPage.tsx`**:
  - tab 名字: "🐉 二波型潜力" → "🐉 龙抬头" / "🐉 首板猎人" → "🐉 首板监控"
  - "首板监控" 数据源: `/api/ambush-signals/first-limit` → `/api/dragon/pool?status=active`
  - FirstLimitView 完全重写: 加列标题 + 监控天数列 (X/10 天 + 进度条) + 起点列 (首板日) + 涨幅列
  - SYNC 按钮: 触发 `/dragon/pool/scan + update-state + evaluate`
  - "龙抬头" tab SYNC: 触发 `update-state + evaluate`
- **FirstLimitStock interface**: 从历史首板字段完全改为 dragon_pool 字段
- **关联文档**: `docs/README.md` v6.0.2 章节

### Phase 76 — 🩺 v6.0.3 涨跌幅修复
- **状态**: ✅ 2026-06-17
- **Bug 1**: 历史 `close_price=21.11` 数据污染 (6 行 6-15 首板)
  - 修复: 用 daily_kline 真实 close 覆盖 6 行 + 同步 dragon_pool.first_limit_close
- **Bug 2**: 10 天检查规则没起作用
  - 根因: `check_first_limit` 用 `(close - open) / open * 100` (**当日振幅**), 不是 `(close - prev_close) / prev_close` (**真实涨跌幅**)
  - 修复: `first_limit_scanner.py` `check_first_limit` + `get_today_limit_list` 改用 `LAG(close)` 算 prev-based 涨幅
- **Bug 3**: 1666 个 first_limit_up.name 错误 (全是 ts_code)
  - 修复: 批量 UPDATE, 用 `get_stock_name` 取真实名称
- **关联文档**: `docs/README.md` v6.0.3 章节

### Phase 77 — 🐉 v6.0.4 5 触发踢出
- **状态**: ✅ 2026-06-17
- **需求**:
  - 000777.SZ 中核科技 6-15+6-16 连续涨停 → 不应在池
  - 600226.SH 亨通股份 6-04/6-09 涨停 → 误判入池
- **`evaluate_exit` 5 规则**:
  - `atr_stop` (exit_signal_detector critical/high)
  - `fatigue_broken` (fatigue_detector broken/capitulation)
  - `time_decay_10d` (days_in_pool >= 10)
  - `not_first_limit` (added_at 前 10 天 prev-based 涨幅 >= 9.9%)
  - `consecutive_board` (added_at 后任何一天 prev-based 涨幅 >= 9.9%)
- **SQL 关键修复**: 规则 4 `trade_date < added_at`, 规则 5 `trade_date > added_at` (首板日本身不算)
- **数据修复**: 4 只误判股标记 exited
- **关联文档**: `docs/README.md` v6.0.4 章节

### Phase 78 — 🎨 v6.0.5 A 股惯例颜色统一
- **状态**: ✅ 2026-06-17
- **需求**: 用户报告 "+330% 当前是绿色", 违反 A 股惯例 (红涨绿跌)
- **改动 (6 文件)**:
  - `lib/signalColor.ts`: 新增 `getPnlColor()` 工具函数 + 修复 `getPnlRowStyle`
  - `pages/AmbushPage.tsx:279` `drawdownPct` 红涨绿跌
  - `pages/AlphaFlowPage.tsx:209` `breakout_pct` 红涨绿跌
  - `components/CuratedRankingView.tsx:134,185,326,333,340` 5 处红涨绿跌
  - `components/DnaLab.tsx:302` `avg_breakout_return` 红涨绿跌
  - `pages/ResultPage.tsx:207,263` `predicted_return` (C.red) 红涨绿跌
- **保留原样**: 市场情绪/评分高低/进度条/就绪状态 (非涨跌幅)
- **关联文档**: `docs/README.md` v6.0.5 章节

### Phase 73 — 🐉 潜龙池 v6.0 动态监控上线
- **状态**: ✅ 2026-06-16
- **文件**:
  - 新表: `dragon_pool` (migrations 120-122, 22 列 + 2 索引)
  - 新建: `backend/app/services/dragon_pool_service.py` (408 行, 4 核心函数 + 编排)
  - 新建: `backend/app/api/dragon.py` (116 行, 5 端点)
  - 修改: `first_limit_scanner.py:127` LIMIT 30→10
  - 修改: `api/scan.py:551-575` 新增阶段 4 (4 SSE 事件)
  - 修改: `scheduler/daily_tasks.py` + `scheduler_loop.py` 新增 `task_update_dragon_pool`
  - 修改: `frontend/src/pages/AmbushPage.tsx` 删连板天梯 tab + 二波型改用 /waveback-potential
  - 修改: `frontend/src/App.tsx:32` label "🐉 潜龙猎手"
  - 修改: `frontend/src/pages/ScanPage.tsx:144-146` 新增 4 个 dragon_pool SSE 事件标签
  - 删除: `services/limit_step_service.py` + `api/ambush.py` 的 /limit-step 系列端点
- **核心特性**:
  - 10 交易日无涨停 → 首板候选（原 30 天）
  - S/A/B 级首板动态入池
  - 踢出：3 触发任一（exit_signal_detector ATR / fatigue_detector 平台破位 / 10d 强制）
  - 浮出：waveback_prob > 0.3 + 强制分时验真（`verify_signals_with_minute_bars`）
- **施工前检查**: 已读 `docs/DEVELOPER_GUIDE.md` §7 统一工具库
- **自检结果**:
  - migration 120-122 全部 OK
  - 5 个 API 端点全部 200 OK
  - evaluate_all_active: total=6 exited=0 emerging=0 errors=0
  - 10 日强制清理: exit=True reason=time_decay_10d ✅
  - 前端 /ambush 200 OK, 3 tab 可见
- **待清理** (1 周稳定后):
  - `services/ambush_scanner.py` (旧"潜伏猎手", 仍被 14 维评分使用)
  - `api/ambush.py` (/hot-sectors 端点保留)
  - `services/limit_cpt_list_service.py`
- **关联文档**:
  - `docs/README.md` v6.0 章节
  - `docs/architecture.md` §10 v6.0 变更日志 + 扫描流程图
  - `docs/潜龙猎手.md` 标记为 ⚠️ 滞后（设计蓝图）

### Phase 71 — TG 扫描阶段重组 v4.8.2 (本次修复 15 项)
- **状态**: ✅ 2026-06-13
- **修复**:
  - P0-1: `setCurrentPhase` 类型扩展为 10 个 `ScanPhase`
  - P0-2: DNA auto-join 异步化 (`asyncio.create_task`)
  - P1-1: `toplist_sync` 合并到 `toplist` (去除重复)
  - P1-2: phaseMessages slice(-8) → slice(-20), maxHeight 120 → 280
  - P1-3: `market_filter` 后端真过滤 (用 `classify_board`)
  - P1-4: `skip_download` 同时控制龙虎榜 + DNA
  - P1-5: scan phase 5% 步长推送 (5000只 → 100 事件)
  - P1-6: `accuracy_feedback(isolated_meta=True)` 写独立列
  - P2-1: 14 维文案修正
  - P2-2: phase 异常信息统一 "异常: {e}"
  - P2-3: 覆盖率 < 95% 时回退 365 天
  - P2-4: ambush_scan 用 `scan_results` 最新日期
  - P2-5: ST 过滤正则修正 (支持中文 "ST")
  - P2-6: phaseLabel 新增 🧬DNA训练
- **数据库变更**: `param_library` 新增 `accuracy_feedback_factor`, `accuracy_feedback_at` 列
- **关联文档**: `docs/architecture.md` 变更日志, `docs/DEVELOPER_GUIDE.md` §7

### Phase 79 — 🚦 v7.0.30 铁三角死规则接入 (MA20+RSI+VOL)

- **状态**: ✅ 2026-06-18
- **文件**:
  - 修改: `backend/app/services/deep_scorer.py` `_apply_hard_rules` (line 132-203)
  - 新建: `backend/scripts/backfill_hard_rules.py` (24,279 行 backfill)
  - 新建: `backend/scripts/_v7_30_validate.py` + `_v7_30_validate2.py` + `_v7_30_verify_production.py`
- **核心规则**: 5 条硬规则 (MA20方向 + RSI底背离 + VOL放量 + ATR止损 + 持续时间) + R6/R7/R8 软规则
- **验证**: 1915 行 verified_5d 实测 wr 56.6% / E +102%
- **关联文档**: `docs/architecture.md` v7.0.30 变更日志

### Phase 80 — 🐉 v7.0.31 多 bug 修复 + 路由统一

- **状态**: ✅ 2026-06-18
- **修复**:
  - Dragon 端点补全 (5 端点)
  - 路由统一 (前端 /api/v1 → /api)
  - 数据一致性 bug 修复 (6 行 close_price=21.11 历史数据 + 1666 个 first_limit_up.name)
  - OSError64 稳定性修复 (Windows 文件锁)
  - MonitorPage v7.0.31 升级 (TG 流水线 bug 修复)
- **关联文档**: `docs/improvements/已完成/20260618-v7.0.31-*.md` (5 份)

### Phase 81 — 📊 v7.0.32 系统评分维度扩展 (27 维)

- **状态**: ✅ 2026-06-19
- **文件**:
  - 数据库: `analysis_scores` 加 22 字段 (macd/kdj/rsi/boll/cci + cost_5/50/95pct + weight_avg + winner_rate + cost_spread + price_vs_cost)
  - 修改: `backend/app/services/deep_scorer.py` `dims` dict 加 5 维评分函数 (line 770 后)
  - 修改: `backend/app/services/llm_deep_analyzer.py` SQL + 2 个格式化函数
  - 修改: `backend/app/api/result.py` `base_select` + dict 输出
  - 修改: `frontend/src/components/CuratedRankingView.tsx` 加 7 列 + 金过滤判定
  - 新建: `backend/scripts/_backfill_tech_chip.py` (5676 条回填, 含 commit bug 修复)
- **核心改动**:
  - T+5 verified (1915 条) macd 字段: 6.5% → 64.4% (+57.9pt)
  - DeepSeek 接收 22 字段 + 自动解读 (MACD 多空/KDJ 超买/CCI 阈值/成本贴近)
  - CuratedRankingView 加金过滤标签 (✓ 金过滤 / ⚠ 风险)
- **修复 bug**: `_backfill_tech_chip.py` 缺 COMMIT (commit 69d46c5b)
- **关联文档**: `docs/improvements/已完成/20260619-v7.0.32-系统评分维度扩展.md`

### Phase 82 — 🧠 v7.0.33 v2 Trainer 按 Regime 训练 (解决跨周期泛化失败)

- **状态**: ✅ 2026-06-19
- **文件**:
  - 修改: `backend/app/services/market_gate.py` +`regime_to_market_style()` +`get_current_regime_simple()`
  - 修改: `backend/app/services/scoring_trainer_v2.py` 3 处改 (load_training_data_v2 + train_single + train_4x2)
  - 修改: `backend/app/services/scoring_trainer_v2.py` `get_4x2_status()` 加 market_style 参数 (修 multi-regime 覆盖 bug)
  - 修改: `backend/app/api/scan.py` + `scheduler/daily_tasks.py` lookback 120 → 730
  - 修改: `backend/scripts/_backfill_tech_chip.py` 加 `await conn.execute('COMMIT')`
  - 新建: `backend/scripts/_pk_v1_v2.py` (v1 vs v2 PK 回归测试)
- **核心改动**:
  - 700001.TI LAG(10) ±2% 打 phase 标签 (bull/bear/range)
  - v2 trainer 按 phase 分组训练, 缺样本 (n<30) 自动降级到 all
  - train_4x2 默认 auto-detect 当前市场
  - 32 套生产权重激活 (8 all + 8 bull + 8 bear + 8 range)
  - feature_flag.learning_v2_active=true (扫描 100% v2)
- **关键修复 bug**:
  1. `_backfill_tech_chip.py` 缺 COMMIT (commit 69d46c5b)
  2. `get_4x2_status()` multi-regime 覆盖 (commit 15cf142c)
- **PK 验证 (3 天 × 3 组)**: Top 5 名 v2 T+5 +0.98% vs v1 +0.01% (+0.97pt), T+10 +1.73% vs -0.94% (+2.67pt)
- **关联文档**: `docs/improvements/进行中/20260619-v2按regime训练.md`

---

## 执行状态

| Phase | 状态 | 执行日期 | 备注 |
|-------|:--:|---------|------|
| 56 | ⚠️ | 06-06 | 1%覆盖, 源数据不足, 随扫描积累 |
| 57 | ✅ | 06-06 | T+5 verified=314 wr=25%, T+15待积累 |
| 58 | ✅ | 06-06 | Phase 32a/34 已覆盖, 7维真实板块特征替代近似 |
| 59 | ✅ | 06-06 | sanxian 优先读本地 min_kline, 缺才调 API |
| 60 | ✅ | 06-06 | 69维含17维龙虎榜, AUC 0.609 无退化 |
| 61 | ⚠️ | 06-06 | 需 signal_history 加 strategy_label 列 |
| 62 | ✅ | 06-06 | /learning/news-verify-summary + 前端Tab |
| 63 | ✅ | 06-06 | Phase 50 已完成调度器注册 |
| 64 | ✅ | 06-06 | 持仓页加入 rec_index + news_signal + recent_wins |
| 65-67 | ✅ | 06-06 | ResultPage 加入 predicted_return + rank_score 列 |
| 68 | ✅ | 06-13 | DNA 实验室 3 机制自动化加入 |
| 69 | ✅ | 06-13 | 新闻特征系统改造 (→ Tushare 宏观数据) |
| 70 | ✅ | 06-13 | 新闻分类去重 + 龙虎榜精细化 v2.0 + 融资融券重写 |
| 71 | ✅ | 06-13 | TG 扫描阶段重组 v4.8.2 (15 项 P0/P1/P2 修复) |
| 72 | ✅ | 06-14 | 新闻页面重复标题修复 (SimHash 去重) |
| 73 | ✅ | 06-16 | 🐉 潜龙池 v6.0 上线 (dragon_pool + 4 函数 + 5 端点 + 删连板天梯) |
| 74 | ✅ | 06-17 | 🩺 v6.0.1 冒烟测试修复 (limit_cpt_list_service.py Windows GBK bug) |
| 75 | ✅ | 06-17 | 🐉 v6.0.2 前端改造 (tab 改名龙抬头/首板监控 + 监控天数列) |
| 76 | ✅ | 06-17 | 🩺 v6.0.3 涨跌幅修复 (prev-based + 历史数据回填) |
| 77 | ✅ | 06-17 | 🐉 v6.0.4 5 触发踢出 (consecutive_board + not_first_limit) |
| 78 | ✅ | 06-17 | 🎨 v6.0.5 A 股惯例颜色统一 (红涨绿跌) |

---

## 施工规则

1. **先查 GUIDE, 再开工** — `grep "你的文件名" docs/DEVELOPER_GUIDE.md`
2. **一个 Phase 一次提交** — 不要跨 Phase 合并改动
3. **自检不通过 = 未完成** — 不要标记 ✅
4. **发现断链或新问题时** — 写在 Phase 备注栏, 不做额外修复 (留给架构师审查)
5. **主动写入执行日期** — 方便追踪进度
