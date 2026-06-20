# 数据库恢复总结

> **日期**: 2026-06-11 | **原因**: PostgreSQL 重启导致 stock_data 数据库丢失

---

## 恢复结果

### 已完全恢复 (✅)

| 表名 | 恢复前 | 恢复后 | 方法 |
|------|--------|--------|------|
| daily_kline | 0 | 3,211,498 | download_latest_kline + batch daily |
| macro_cache | 0 | 8,054 | 调 Tushare API: cn_m/cn_cpi/cn_ppi/shibor/margin/hk_hold/futures |
| daily_chip_perf | 0 | 49,604 | scripts/sync_chip_perf.py --backfill |
| scan_results | 0 | 4,992 | TG 全市场扫描 |
| analysis_scores | 0 | 61 | deep_analyze |
| recommendation_tracking | 0 | 61 | deep_analyze |
| news_raw | 0 | 7,837 | 新闻爬虫自动写入 |

### 待恢复 (⏳ 后台)

| 表名 | 当前 | 目标 | 方法 |
|------|------|------|------|
| signal_history | 0 | ~20,000+ | scripts/backfill_signal_labels.py --days 90 |
| bayesian_beliefs | 0 | 30 | 分段权重训练 |
| param_library | 0 | 15 | 影子权重重训 |

### 正常状态

| 表名 | 状态 | 说明 |
|------|------|------|
| alphaflow_pool | 0 | 市场当前无锁死股票 (正常) |

---

## 未修复

| 项目 | 原因 |
|------|------|
| bond_10y_yield (10年国债) | Tushare gz_index 接口不含 d10_rate 字段，需要从其他数据源补充 |

---

## 验证清单

- [x] 首页宏观数据: 货币/利率/通胀/资金/商品全部有值
- [x] 新闻采集: SSE 流式正常
- [x] TG 扫描 + 深度评分 + 推荐结果
- [x] 筹码评分: 14 维中的 chip_winner/chip_cost 正常
- [ ] 分段权重训练 (待 signal_history 回填)
- [ ] 影子权重重训 (待分段训练)
- [ ] AlphaFlow 池重建 (市场条件满足后)

---

## 本轮工作总结

1. 数据库恢复: macro_cache 同步、daily_chip_perf 同步、缺表补建、评分管道恢复
2. 评分管道修复: session 事务 abort 修复、缺失列补全、预加载阶段独立 session 隔离
3. 自学习: scoring_trainer 数据源从 recommendation_tracking 改为 signal_history、市场阶段用 700001.TI LAG(10) 自算
4. 首页功能: 宏观数据全部12项指标有值、新闻采集 SSE 流式正常、新增 /api/macro/sync 端点
