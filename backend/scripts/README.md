# Stock Analyst Scripts (46个脚本)

> 工具脚本索引 — 按用途分为 6 类。路径：`backend/scripts/`

---

## 模型训练

| 脚本 | 用途 | 运行方式 |
|------|------|---------|
| `alphaflow_train_v2.py` | XGBoost V2 48维特征训练（主力） | `python alphaflow_train_v2.py` |
| `alphaflow_train.py` | XGBoost V1 训练（已过时，被V2替代） | — |
| `bootstrap_train.py` | 自学习引导训练（冷启动） | `python bootstrap_train.py` |
| `train_scoring.py` | Logistic Regression 评分权重训练 | `python train_scoring.py` |
| `transform_scores.py` | 评分变换/归一化工具 | `python transform_scores.py` |
| `alphaflow_label.py` | AlphaFlow 样本标注 | `python alphaflow_label.py` |

## 分钟线/蛋阶段

| 脚本 | 用途 | 运行方式 |
|------|------|---------|
| `mins_egg_train.py` | 分钟线"蛋"阶段特征提取 → `mins_train_samples` | `python mins_egg_train.py` |
| `mins_egg_vs_goose.py` | 蛋 vs 鹅 对比分析 | `python mins_egg_vs_goose.py` |
| `mins_label_eggs.py` | 分钟线蛋样本标注 | `python mins_label_eggs.py` |
| `mins_train_classifier.py` | 分钟线分类器训练（消费 `mins_train_samples`） | `python mins_train_classifier.py` |
| `tg_mins_experiment.py` | TG分钟线实验脚本 | `python tg_mins_experiment.py` |
| `alphaflow_mins.py` | AlphaFlow 分钟线数据处理 | `python alphaflow_mins.py` |

## 数据同步

| 脚本 | 用途 | 运行方式 |
|------|------|---------|
| `download_today.py` | 下载当日行情数据（K线/龙虎榜等） | `python download_today.py` |
| `sync_min_kline.py` | 同步分钟K线数据 | `python sync_min_kline.py` |
| `sync_toplist_detail.py` | 同步龙虎榜明细数据 | `python sync_toplist_detail.py` |
| `refresh_fundamental_snapshot.py` | 刷新基本面快照表 | `python refresh_fundamental_snapshot.py` |
| `phase0_sync.py` | Phase 0 数据同步（早期数据初始化） | `python phase0_sync.py` |

## 数据库迁移

| 脚本 | 用途 | 运行方式 |
|------|------|---------|
| `phase0_migrate.py` | Phase 0 数据库迁移（建表初始化） | `python phase0_migrate.py` |
| `phase1_migrate.py` | Phase 1 数据库迁移（增量字段） | `python phase1_migrate.py` |
| `add_weekly_columns.py` | 新增周线相关列到 scan_results | `python add_weekly_columns.py` |
| `backfill_cashflow.py` | 回填现金流量表数据 | `python backfill_cashflow.py` |
| `backfill_fingerprint_dims.py` | 回填指纹维度数据 | `python backfill_fingerprint_dims.py` |
| `backfill_history.py` | 回填历史推荐数据 | `python backfill_history.py` |
| `rebuild_archetypes.py` | 重建原型分类数据 | `python rebuild_archetypes.py` |
| `rebuild_archetypes_market.py` | 按市场分层重建原型 | `python rebuild_archetypes_market.py` |
| `build_dimension_tags.py` | 构建维度标签 | `python build_dimension_tags.py` |
| `extend_dimension_tags.py` | 扩展维度标签 | `python extend_dimension_tags.py` |

## 回测分析

| 脚本 | 用途 | 运行方式 |
|------|------|---------|
| `backtest_benchmark.py` | 回测基准对比 | `python backtest_benchmark.py` |
| `backtest_exit_signals.py` | 退出信号回测 | `python backtest_exit_signals.py` |
| `backtest_gate.py` | 市场门控回测（验证门控参数） | `python backtest_gate.py` |
| `backtest_weekly_resonance.py` | 周线共振方案回测 | `python backtest_weekly_resonance.py` |
| `grid_search_gates.py` | 门控参数网格搜索 | `python grid_search_gates.py` |
| `analyze_exit_timing.py` | 退出时机分析 | `python analyze_exit_timing.py` |
| `analyze_recommendations.py` | 推荐历史分析 | `python analyze_recommendations.py` |
| `analyze_stratification.py` | 分档分析（按评分分位统计胜率） | `python analyze_stratification.py` |
| `analyze_win_factors.py` | 赢家因子分析 | `python analyze_win_factors.py` |
| `alphaflow_stats.py` | AlphaFlow 池统计 | `python alphaflow_stats.py` |

## 测试

| 脚本 | 用途 | 运行方式 |
|------|------|---------|
| `test_deepseek_api.py` | DeepSeek API 连接测试 | `python test_deepseek_api.py` |
| `test_drill.py` | 个股历史钻探测试 | `python test_drill.py` |
| `test_e2e_feedback.py` | 端到端反哺流程测试 | `python test_e2e_feedback.py` |
| `test_feedback_parse.py` | 反哺解析测试 | `python test_feedback_parse.py` |
| `test_full_drill.py` | 完整钻探流程测试 | `python test_full_drill.py` |
| `test_ma_score.py` | 均线评分测试 | `python test_ma_score.py` |
| `test_news_by_source.py` | 按来源新闻分析测试 | `python test_news_by_source.py` |
| `test_news_llm.py` | LLM新闻分析测试 | `python test_news_llm.py` |
| `test_bootstrap.py` | 自学习引导测试 | `python test_bootstrap.py` |

---

**最后更新**: 2026-06-04 | **脚本总数**: 46
