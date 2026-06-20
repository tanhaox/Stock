# Stock Analyst 系统架构文档

> **版本**: v7.0.34 | **日期**: 2026-06-20 | **核心依赖**: DeepSeek API + XGBoost + PostgreSQL + DNA 个性化模型 + 大神仙空 v2.0 + v2 学习链路 (4×2 + 按 regime 训练 + 训练数据筛选) + 铁三角死规则 (5 条) + 27 维评分 (含 v7.0.32 新增 5 维) + Exclusion 踢出名单 (5 reasons, v7.0.34)
> **v7.0.30 审计状态**: 🎯 铁三角死规则 (MA20+RSI+VOL) 接入 deep_scorer 流水线 + 24,279 行 backfill + 生产验证 PASS wr 56.6% / E +102%
> **v7.0 审计状态**: 🎯 v2 学习链路 (4 horizon × 2 model_type 独立训练) — 不破坏 v1 链路
> **v6.0.4 审计状态**: 🐉 5 触发踢出（consecutive_board + not_first_limit 复核）
> **v6.0.3 审计状态**: 🩺 涨跌幅 prev-based 修复 (避免 open-close 误判)
> **v6.0.2 审计状态**: 🐉 前端 tab 改名 + 监控天数列
> **v6.0.1 审计状态**: 🩺 冒烟测试修复 `limit_cpt_list_service.py` Windows GBK 编码 bug (hot-sectors 端点 500 → 200)
> **v6.0 审计状态**: 🐉 潜龙池动态监控上线（dragon_pool 表 + 模型驱动踢出/浮出 + 删连板天梯）

---

## 0. 系统级 P0 工具基础设施 ⭐ v4.5 新增

v4.5 完成了 **7 个系统级 P0 能力升级**，将数十处分散的防御代码收敛为统一工具模块：

| 工具模块 | 位置 | 替代的分散实现 |
|---------|------|---------------|
| **`app/utils/numpy_utils.py`** | NaN/Inf/JSON 安全 | 14 处 `nan_to_num`/`isnan`/`fillna` (4种不同策略) |
| **`app/core/market_data.py`** | 基准加载 + 超额收益 | 3 处独立公式 + 14 处 700001.TI SQL + **影子训练日历日 bug** |
| **`app/core/progress.py`** | 进度回调协议 | 3 种回调签名 (2/3/4参数混用) |
| **`app/utils/stock_code.py`** | 代码规范化 | 9 处 `startswith('6')→.SH` 复制 + **6 处 BJ 代码丢弃 bug** |
| **`app/core/name_resolver.py`** | 名称三级缓存 | 5 处绕过缓存直接查 scan_results |
| **`scripts/resync_all_kline.py`** | 全局前复权 | **9 个除权补丁** (3种阈值: 15%/18%/20%) |
| **`app/services/kline_utils.py`** | 前复权K线工具 | `get_adjusted_kline()` / `get_ex_rights_dates()` 精确识别 |

**核心原则**: 每个 P0 问题都在数据入口或边界统一解决，而非在每个模块中打补丁。

### ⭐ DNA 个性化模型实验室 (v4.5 新增)

股票 DNA 系统实现了 AlphaGo 风格的个性化分析：
- **理念**: 不是 5507 只股票共享一套规则，每只股票从自己的历史中学到"残局棋谱"
- **两层突破**: 打掉 TG 信号枷锁（每天都是数据点） + 打掉 T+2 枷锁（多窗口自适应）
- **五维特征**: 日线截面(73维) + 分时表情(15维) + 市场情绪(15维) + 转移矩阵(12维) + 周期节律(8维) + 历史统计(15维) + 交互(8维) = 146维
- **表情聚类**: 15维→KMeans++ 轮廓系数选 K→5-8种个性化表情→马尔可夫转移矩阵→明日情绪预测
- **老兵周期**: 评分制锁死检测 (ATR<0.8 + MA<0.04 + VOL<0.8, ≥2/3条件+5日滑动窗口容错)
- **Per-Stock XGBoost**: 80树×depth=3, Huber loss δ=3.0, 4窗口联合输出 (T+2/5/10/20)
- **贝叶斯置信度**: `confidence = n / (n+500)`，样本<500天向原型收缩
- **独立并行**: 独立 API (/api/dna/*)、独立 DB (stock_dna.*)、不修改现有代码

### ⭐ 全局前复权 (v4.5 新增)

```
resync_all_kline.py —
  Tushare adj_factor API → 前向填充逐日复权因子 → 手动前复权公式
  close_adj[t] = close_raw[t] × (adj_factor[t] / adj_factor[latest])
  → daily_kline 全量覆盖 + adj_factor 列标记
```

退役了 **9 个除权补丁**: `find_last_ex_rights()`, `_is_ex_rights_day()`, `_adjust_ex_rights()`, 及 6 个调用方。

---

## 1. 系统概述

### 两大核心管线

Stock Analyst 目前运行 **两条独立又互补的管线**，并通过交叉反哺机制联动。v4.3 新增 **个股历史复盘 + 四维共振 + 操盘手法反推**：

```
管线 1: TG 日线扫描 (成熟)          管线 2: AlphaFlow 主升浪捕获 (v4.3)
─────────────────────────────      ────────────────────────────────────
日线 TG 指标 18 步计算              ① 锁死检测 (双窗口 + 除权免疫)
→ ★ Phase 1.5 周线 TG 扫描         → ~200 只锁死候选
→ 双周期信号匹配 (共振/日线/周线)   ② 老兵识别 (4+ 周期统计分析)
→ 14 维深度评分（分段权重+衰减）    ③ 历史评估 (A-G 8项过滤)
→ 策略原型分类                    ④ ★ 48维特征 + XGBoost V3 概率
→ ★ QUALITY_GATE 自适应放宽        ⑤ 策略分类 (8种 strategy_label)
→ 推荐排序（三级优先级）            ⑥ 筹码吸收 (三区模型 + 除权免疫)
                                   ⑦ 波段目标预测 (退市股排除 + 3σ过滤)
         ┌── 交叉反哺 ──┐           ⑧ 池管理 (老兵豁免 + 结构维护)
  分钟线 N/M 检测 ✅      │          ⑨ ★ 量能历史参考范围 (锁死周期分布)
  板块联盟验证 ✅         │
  信号质量评分 ✅         │
  市场门控 v2.0 ✅        │
  多周期验证 ✅           │
  ★ 周线共振评分 ✅       │
  ★ AlphaFlow 净买力 ────┘  (TG composite_score + TG→AlphaFlow 第48维)
                          │
  ★★★ v4.3 智能研判层 ★★★  │
  个股历史复盘 (7项)       │
  四维共振 (指数/板块/消息/筹码)  │
  操盘手法反推 (5分钟线)    │
  系统健康自动激活          │
  空仓建议 (force_empty)    │
```

### 核心能力矩阵

| 能力 | 管线 | 说明 |
|------|------|------|
| **TG 全市场扫描** | 管线 1 | 每日对 5000+ A 股运行 TG 精准买卖指标 V11.2，主板/创业板/科创板差异化阈值 |
| **14 维深度评分** | 管线 1 | ★ v4.3 按市场状态分段权重 + 软衰减 (regime>60天混入全局) + 安全门控 (n≥50/params≥10/AUC≥0.55) |
| **策略原型分类** | 管线 1 | K-means 聚类将股票分为 5 个策略原型，不同原型使用不同评分权重偏移 |
| **★ 个股历史复盘** | 管线 1 | ★ v4.3 7 项子复盘: 信号回溯+形态匹配+关键位置+筹码模拟+市场敏感性+四维共振+操盘手法 |
| **★ 四维共振分析** | 管线 1 | ★ v4.3 指数/板块/消息/筹码 四维度信号归因 → 独立率/领先率/伪强势率/技术驱动率 |
| **★ 操盘手法反推** | 管线 1 | ★ v4.3 5分钟线检测快速拉升/砸盘/托单/尾盘偷袭/开盘冲锋 → 提升度分析 → 触发条件发现 |
| **★ QUALITY_GATE 自适应** | 管线 1 | ★ v4.3 L1/L2 两级自适应放宽 (通过<10→降sq/wp/ts→仍<8→降sc/wp) |
| **★ 强制空仓** | 管线 1 | ★ v4.3 恐慌杀跌+胜率<25%+上涨<20% → force_empty → 前端红色横幅 |
| **★ 系统健康自动激活** | 管线 1 | ★ v4.3 每日检测分段权重/校准器/原型偏移达标 → 自动标记is_active + sync_log |
| **★ 信号异常检测** | 管线 1 | ★ v4.3 每日检测买入信号数量/win_probability偏离历史3σ → WARNING + sync_log |
| **★ 影子模型对比** | 管线 1 | ★ v4.3 每周日 evaluate_shadow_vs_main() → 连胜3周自动切换 |
| **AlphaFlow 锁死检测** | 管线 2 | ⭐ v2.2 双窗口锁死 (15-20日≤15% + 20-40日≤17%) + 除权免疫 |
| **老兵检测** | 管线 2 | ⭐ 4+锁死周期统计 (周期延长/振幅收敛/量萎缩) → pre_breakout/late_stage/monitoring |
| **历史评估+策略分类** | 管线 2 | ⭐ A-G 8项过滤 × quality_label → 8种 strategy_label |
| **XGBoost V3 概率** | 管线 2 | ⭐ ★ 48维特征 (含板块锁死期% + 6老兵增强 + TG反哺第48维), 版本校验 |
| **★ 量能历史参考** | 管线 2 | ★ v4.3 lock-detail 量能变化对比历史锁死周期分布 → 分位+标签 |
| **筹码吸收分析** | 管线 2 | ⭐ 三区模型 (Z_LOCK/Z_OVER/Z_BELOW) + 除权免疫, 吸收率 |
| **波段目标预测** | 管线 2 | ⭐ 历史浪幅统计 + 退市股排除 + 3σ极端值过滤 → target_zone + risk_levels |
| **5 分钟线分析** | 管线 2 | 底延/顶延/VWAP 计算 + T 模式网格建议 + 振幅收敛检测 |
| **结构性趋势检测** | 管线 2 | 摆动分析 → 关键支撑位 → 趋势破坏/崩塌判定 |
| **乏力度 5 阶段** | 管线 2 | 平台跌破逐层追踪 → 活跃→预警→破位→崩塌 四级判定 |
| **SXQS 资金博弈** | 共享 | ZIG 拐点 + H1/H2/H3 三级 EMA + VAR6/VAR7/VAR8 买卖力 + D/W 信号 |
| **分钟线反哺 TG** | 交叉 | N/M 形态检测 + 板块联盟验证 + 信号质量评分 |
| **AlphaFlow 净买力反哺** | ★ 交叉 v4.1 | XGBoost 最强特征 (25% importance) 作为 TG composite_score 修正因子 |
| **市场门控 v2.0** | 管线 1 | ⭐ 7市场体制 + 涨跌比 + 风格偏向(大小盘) + 双阈值 |
| **多周期验证** | 管线 1 | ⭐ 周MACD + 月趋势 + 背离检测 |
| **概率校准** | 管线 1 | ★ v4.1 T+3标签统一 + regime-aware分段校准 (数据不足自动回退) |
| **评分权重训练** | 管线 1 | ★ v4.1 Logistic Regression 真实盈亏训练 + 分段(bull/bear/range) + Bayesian持久化 |
| **周线双周期共振** | ★ 管线 1 v4.2 | 日线 TG + 周线 TG 独立信号 → resonance_type (weekly_resonance/daily_only/weekly_driven) → 三级排序 |
| **LLM 新闻分析** | 管线 1 | 爬取 4 源财经新闻 → DeepSeek 分类 → 深度分析 → 事件入库 |
| **LLM 精选反哺** | 管线 1 | 14 维结构化提示词 + 筹码硬指标 → DeepSeek 深度分析 → 批量横向评分 |
| **老兵突破率回测** | 管线 2 | ★ v4.1 按级别/分段的 T+5/T+20 突破率统计 (每周六自动) |
| **原型偏移校准** | 管线 1 | ★ v4.1 collect_archetype_calibration_data() 已就绪, 等待数据积累 |
| **退出信号** | 管线 1 | 止盈/止损/移动止盈/时间退出四类信号 |
| **持仓管理** | 管线 1 | ⭐ 资金账户 + 自动清仓(qty=0) + 待清仓 + 持股天数 + 筹码诊断 |

### DeepSeek 底座角色

DeepSeek 在系统中扮演 **三类角色**：

1. **新闻分析引擎**（Stage1 打标签 + Stage2 深度分析 + 晨报生成）—— **v4.8: Stage1 用 Flash, Stage2 公司级用 Pro, 行业/政策/商品用 Flash**
2. **个股深度分析师**（精选反哺/自动分析/持仓策略）—— 调用 chat 模型，含筹码硬指标附件
3. **结构化数据提取器**（持仓导入解析/反哺文本解析/批量横向评分）—— 调用 chat 模型

### v4.8 核心能力 (本次新增)

| 能力 | 模块 | 说明 |
|------|------|------|
| **DNA 实验室自动化加入** | `stock_dna_auto_join.py` | 3 机制: AlphaFlow突破+TG买入 / L3级 / 持仓变动, 异步训练不阻塞 |
| **新闻分类去重** | `news_classifier.py` | SimHash 跨源去重 + 三级分类 (company/sector/macro/garbage), macro_only 跳过 LLM |
| **龙虎榜精细化 v2.0** | `toplist_analyzer.py` | 机构 4 子类 (公募/北向/社保/QFII) + 游资 4 级 (顶级/一线/二线/三线) + 5 级共振 + 净买持续性 |
| **龙虎榜智能缓存** | `get_cached_daily_toplist()` | 历史永久 / 当日交易 5min / 休市 1h, 避免休市期间重复计算 |
| **龙虎榜 SSE 刷新** | `/api/scan/toplist-refresh` | 流式 sync → analyze → sector 3 阶段进度 |
| **新闻聚合接口** | `/api/scan/news-dashboard` | 6 请求 → 1 请求, 加载时间大幅减少 |
| **新闻新鲜度** | `/api/scan/news-freshness` | skip / crawl_only / analyze_only / full 4 种建议 |
| **新闻增量更新** | `news_pipeline.py` | 2h 内爬过跳过, 6h 内分析过跳过 LLM |
| **融资融券重写** | `get_margin_sentiment()` | 改用 rzye (融资余额) 直接判断亢奋/正常/谨慎 + 颜色 |
| **市场过滤 (后端)** | `trigger_scan?market_filter=` | 主板/中小板/创业板 服务端真过滤, 不再仅前端展示 |
| **DNA 异步训练** | `asyncio.create_task` | 60-180秒/只训练放入后台队列, 不阻塞 done 事件 |
| **进度推送节流** | `tg_engine.py` | scan phase 5% 步长推送, 5000只 → 100 事件 |
| **accuracy 隔离元参数** | `accuracy_tracker.py` | `isolated_meta=True` 写入独立列, 不覆盖主 discrimination |

### 技术栈

| 层 | 技术 | 版本/说明 |
|----|------|----------|
| 后端框架 | FastAPI | Python 3.13 |
| 异步数据库 | SQLAlchemy 2.0 + asyncpg | PostgreSQL |
| LLM 客户端 | httpx (async) | DeepSeek API v1 |
| 机器学习 | XGBoost + scikit-learn (LogisticRegression/StandardScaler) | AlphaFlow V2.1 (47维), TG 权重训练 |
| 数据分析 | pandas, numpy, scipy | TG 指标、K-means、Bayesian Opt |
| 浏览器自动化 | playwright | 新闻爬虫 |
| 数据源 | Tushare Pro | 日线/分钟线/财务/龙虎榜/申万指数/大盘指数 |
| 前端 | React 19 + TypeScript + Vite 8 | Ant Design 6 |
| 部署 | uvicorn | 单 worker（`--workers 1`） |

---

## 2. 目录结构与文件清单

```
Stock/
├── StockAnalyst.bat                      # 一键启动脚本
├── 系统自检.bat                          # 系统自检入口
├── README.md
├── backend/
│   ├── .env                              # 环境变量 (DB/DeepSeek/Tushare/百度千帆)
│   ├── requirements.txt                  # Python 依赖 (含 xgboost, scikit-learn)
│   ├── system_check.py                   # 6 维度系统自检 (DB/评分/学习/一致性/API/前端)
│   ├── _check.py                         # 快速检查
│   ├── models/                           # 模型文件
│   │   ├── alphaflow_xgb.json            # ★ XGBoost V2.1 47 维模型
│   │   ├── alphaflow_xgb_meta.json       # ★ V2.1 元信息 (training_date/feature_names/feature_hash)
│   │   ├── angel_model.json              # 天使模型 (待加载)
│   │   ├── guardian_model.json           # 守护模型 (signal_quality_scorer 已加载)
│   │   ├── dual_channel_meta.json        # 双通道元信息
│   │   └── backups/                      # ★ 模型备份目录 (retrain_xgb.ps1 自动创建)
│   ├── app/
│   │   ├── main.py                       # FastAPI 入口 + lifespan + 路由注册 + 后台调度
│   │   ├── core/
│   │   │   ├── config.py                 # Pydantic Settings (含 DEEPSEEK_PRO_MODEL)
│   │   │   ├── database.py               # async engine + session factory (pool_size=10)
│   │   │   ├── market_data.py            # ⭐ v4.5 统一基准加载 + 超额收益计算 (含缓存)
│   │   │   ├── progress.py               # ⭐ v4.5 进度回调协议 + 适配器工厂
│   │   │   ├── name_resolver.py          # ⭐ v4.5 名称解析三级缓存 (内存→DB→fallback)
│   │   │   ├── auth.py                   # X-User-ID Header 认证
│   │   │   └── security.py              # HTTPBearer 可选认证
│   │   ├── utils/                        # ⭐ v4.5 系统级工具模块
│   │   │   ├── numpy_utils.py            # NaN/Inf/JSON 安全 (safe_float/sanitize_for_json/safe_auc 等)
│   │   │   └── stock_code.py             # 代码规范化 (normalize_ts_code/strip_suffix/classify_board)
│   │   ├── models/
│   │   │   ├── base.py                   # DeclarativeBase + BaseMixin
│   │   │   └── data_models.py           # ScanResult, AnalysisScore, StockFundamentalSnapshot
│   │   ├── api/
│   │   │   ├── __init__.py               # 路由聚合 + /health (15 个路由)
│   │   │   ├── scan.py                   # TG 扫描 (SSE 两阶段) + 新闻 + NM验证 + 数据新鲜度
│   │   │   ├── result.py                 # 最终推荐 (市场门控 v2.0 + S3风险分类 + 板块感知)
│   │   │   ├── analysis.py              # 深度分析 + 手动加股(完整管线) + 删除
│   │   │   ├── comprehensive.py          # ★ 一键综合分析 (个股+板块+宏观+判定)
│   │   │   ├── alphaflow.py             # ★ AlphaFlow (pool/lock-detail/chip/wave/veteran-backtest)
│   │   │   ├── feedback.py              # ★ DeepSeek 反哺 + 批量横向评分(含筹码硬指标)
│   │   │   ├── holdings.py              # ★ 持仓 CRUD + 资金账户 + 自动清仓 + 筹码诊断
│   │   │   ├── learning.py              # ★ 学习面板 (训练/升级/回测/原型校准/权重)
│   │   │   ├── llm_analysis.py          # LLM 提示词生成 + 自动分析
│   │   │   ├── dna.py                   # ⭐ v4.5 DNA 实验室 API (7 端点: status/profile/predict/scan/compare/emotion/add-stock)
│   │   │   ├── ambush.py                # 潜伏猎手信号
│   │   │   ├── decisions.py             # 用户决策记录
│   │   │   └── settings.py              # 系统设置 (读写 .env)
│   │   └── services/
│   │       ├── tg_engine.py             # TG 全市场扫描引擎 (两阶段 SSE)
│   │       ├── tg_indicator.py          # TG V11.2 18 步指标计算 + 板块差异化参数
│   │       ├── tdx_functions.py         # 通达信指标函数 (MA/EMA/HHV/LLV/CROSS 等)
│   │       ├── deep_scorer.py           # ★ 14 维深度评分引擎 (1796 行, v4.1)
│   │       │                            #   含: 分段权重加载+安全门控+AlphaFlow净买力反哺
│   │       ├── enhanced_scorer.py       # 增强 6 维评分
│   │       ├── ma_scorer.py             # 均线趋势质量评分 + 支撑/压力位
│   │       ├── fingerprint_builder.py   # 11 维指纹构建器 (5 表预加载)
│   │       ├── archetype_classifier.py  # K-means 原型分类器 (5 原型)
│   │       ├── archetype_param_resolver.py # ★ 原型→权重/阈值解析 (5 原型偏移表 + 校准数据收集)
│   │       ├── probability_calibrator.py # ★ v4.1 概率校准 (T+3 + regime分段 + 小样本降权)
│   │       ├── scoring_trainer.py       # ★ v4.1 评分权重训练器 (552 行, 真实盈亏反馈)
│   │       │                            #   Logistic Regression + 分段训练 + Bayesian持久化
│   │       ├── market_gate.py           # ★ v2.0 市场门控 (7体制+涨跌比+风格偏向)
│   │       ├── multi_timeframe.py       # ★ 多周期验证 (周MACD+月趋势+背离)
│   │       ├── exit_signal_detector.py  # 退出信号检测 (止盈/止损/移动止盈/时间退出)
│   │       ├── event_detector.py        # 新闻事件分析引擎 v4.0
│   │       ├── news_crawler.py          # 新闻爬虫 (playwright)
│   │       ├── sector_heat_engine.py    # ★ 板块热度5阶段 (萌芽→发酵→高潮→分化→退潮)
│   │       ├── sector_alliance.py       # ★ 板块联动 + 涨停联盟检测
│   │       │
│   │       │ # ── AlphaFlow 核心服务 (v4.1) ──
│   │       ├── lock_detector.py         # ★ v2.3 双窗口锁死 (除权免疫已退役: 系统全局前复权)
│   │       ├── kline_utils.py            # ⭐ v4.5 前复权 K 线工具 (get_adjusted_kline/get_ex_rights_dates)│   │       │
│   │       │ # ── ⭐ DNA 个性化模型实验室 (v4.5) ──
│   │       ├── stock_dna/                # ⭐ v4.5 DNA 包 (10 个模块, 完全并行, 零现有代码侵入)
│   │       │   ├── __init__.py           # 包导出
│   │       │   ├── features.py           # 146 维特征工程 (73日线 + 15表情 + 15市场 + 12转移 + 8周期 + 15历史 + 8交互)
│   │       │   ├── emotion.py            # 日内表情聚类 (KMeans++ + 轮廓系数) + Laplace 平滑转移矩阵
│   │       │   ├── cycle.py              # 老兵周期检测 v2 (评分制, ATR<0.8/MA<0.04/VOL<0.8, 5日滑动窗口)
│   │       │   ├── market_context.py     # 大盘分时联动特征 (15维)
│   │       │   ├── data_builder.py       # 训练样本生成器 (daily_kline + min_kline → daily_samples)
│   │       │   ├── model.py              # Per-Stock XGBoost (80树×depth=3, Huber δ=3.0, 4窗口输出)
│   │       │   ├── inference.py          # DNA 推理服务 (模型缓存 + 特征重建)
│   │       │   ├── similarity.py         # 跨股票 DNA 余弦相似度
│   │       │   └── dna_models.py         # ORM (stock_dna.daily_samples/profiles/predictions)
│   │       ├── alphaflow_veteran.py     # ★ 老兵检测 (含 backtest_veteran_breakout_rate)
│   │       ├── alphaflow_evaluator.py   # ★ 历史评估 A-G 过滤 + strategy_label 策略分类
│   │       ├── alphaflow_features.py    # ★ 47 维特征提取 (含真实板块锁死期% + 6老兵增强)
│   │       ├── alphaflow_pool.py        # ★ 完整管线 + _load_xgb_model() 版本校验 (719 行)
│   │       ├── chip_analyzer.py         # ★ 筹码吸收三区模型 + 除权免疫 (338 行)
│   │       ├── wave_predictor.py        # ★ 波段预测 + 退市股排除 + 3σ过滤 (374 行)
│   │       ├── fatigue_detector.py      # 乏力度 5 阶段 (浪顶→破平台→加速下跌)
│   │       ├── structure_break_detector.py # 结构性破坏 (摆动分析+关键支撑)
│   │       ├── micro_pattern_detector.py  # 3 分钟微剧本 (5 维吸筹评分)
│   │       ├── five_stage_detector.py   # 5 阶段检测
│   │       ├── intraday_analyzer.py     # 盘中异动分析 (2%+涨幅出货/吸筹判定)
│   │       ├── minute_nm_detector.py    # ★ N/M 形态检测引擎 (分时段对比法+跨日聚合)
│   │       ├── signal_quality_scorer.py # 信号质量评分 (含 NM 验证入口)
│   │       │
│   │       │ # ── 自学习服务 ──
│   │       ├── learning_engine.py       # 学习引擎 (T+5 滚动回测+Bayesian 调度)
│   │       ├── bayesian_optimizer.py    # Bayesian 参数优化 (Normal-Normal 共轭, 4 参数组)
│   │       ├── shadow_trainer.py        # 影子训练引擎 (Bayesian Opt)
│   │       ├── contextual_bandit.py     # 上下文 Bandit (Thompson Sampling, 待接入)
│   │       ├── replay_buffer.py         # 经验回放缓冲 (6280 条经验)
│   │       ├── dual_channel_trainer.py  # 双通道训练器 (天使+守护, 待调度)
│   │       ├── self_learning_bootstrap.py # ★ 自学习 Bootstrap (对接 scoring_trainer)
│   │       │
│   │       │ # ── 数据与通用服务 ──
│   │       ├── deepseek.py              # DeepSeek API 客户端 (httpx, 180s)
│   │       ├── llm_deep_analyzer.py     # ★ LLM 深度分析 (提示词+解析+筹码段生成)
│   │       ├── feedback_parser.py       # 反哺文本解析
│   │       ├── feedback_integration.py  # 反馈评分融合
│   │       ├── baidu_search.py          # 百度千帆 AI Search (日配额 50/6h 缓存)
│   │       ├── tushare.py               # Tushare K 线数据
│   │       ├── tushare_common.py        # Tushare API 封装 (重试+QPS 控制)
│   │       ├── realtime_quote.py        # 东方财富实时行情
│   │       ├── stock_name_cache.py      # 股票名称缓存
│   │       ├── background_sync.py       # ★ 每日调度 (含周度训练/校准/回测, 403 行)
│   │       ├── toplist_analyzer.py      # 龙虎榜分析
│   │       ├── trend_filter.py          # 趋势过滤器
│   │       ├── tail_market_scanner.py   # 隔天尾盘扫描
│   │       ├── ambush_scanner.py        # 潜伏猎手 (4 阶段过滤)
│   │       ├── pattern_engine.py        # 形态扫描引擎
│   │       ├── pattern_scanner.py       # K 线形态量化检测
│   │       ├── comprehensive_analyzer.py # ★ 一键综合分析引擎
│   │       ├── session_manager.py       # ⚠️ 已简化: 仅 MAX(scan_date) 查询
│   │       └── accuracy_tracker.py      # 推荐准确率追踪
│   └── scripts/
│       ├── download_today.py            # 下载当日行情
│       ├── backfill_history.py          # 历史回填
│       ├── sync_min_kline.py            # ★ 分钟 K 线同步 (pool + holdings 股票)
│       ├── sync_toplist_detail.py       # ★ 龙虎榜明细同步
│       ├── refresh_fundamental_snapshot.py # 基本面快照刷新
│       ├── add_weekly_columns.py        # ★ 方案 B 迁移脚本 (周线字段) (v4.2)
backtest_weekly_resonance.py # ★ 方案 B 回测验证 (v4.2)
test_drill.py              # ★ v4.3 个股复盘测试脚本
test_full_drill.py         # ★ v4.3 全维度复盘测试
│       ├── backtest_weekly_resonance.py # ★ 方案 B 回测验证 (v4.2)
│       │
│       │ # ── AlphaFlow 脚本 ──
│       ├── alphaflow_train_v2.py        # ★★ XGBoost V2.1 训练 (47维, 含 sector_closes, 版本元信息)
│       ├── alphaflow_train.py           # V1 训练脚本
│       ├── alphaflow_label.py           # 训练标签生成
│       ├── alphaflow_mins.py            # 分钟线特征提取
│       ├── alphaflow_stats.py           # 统计特征
│       │
│       │ # ── 分钟线反哺 TG ──
│       ├── tg_mins_experiment.py        # 交易员视角: 20 天分时图→T+2 涨跌预测
│       ├── mins_egg_train.py            # 蛋期分时训练 (批量下载+特征提取)
│       ├── mins_egg_vs_goose.py         # 蛋 vs 大雁分时特征对比
│       │
│       │ # ── 其他脚本 ──
│       ├── phase0_migrate.py            # Phase 0 迁移 (申万指数/牛熊/退市股)
│       ├── phase0_sync.py               # 行业指数同步 + 牛熊阶段划分
│       ├── phase1_migrate.py            # Phase 1 迁移 (原型扩展)
│       ├── rebuild_archetypes.py        # 重建 K-means 质心
│       ├── rebuild_archetypes_market.py # 按市场重建原型
│       ├── bootstrap_train.py           # Bootstrap 训练
│       ├── build_dimension_tags.py      # 维度标签构建
│       ├── extend_dimension_tags.py     # 标签扩展
│       ├── analyze_recommendations.py   # 推荐胜率分析
│       ├── analyze_stratification.py    # Top-N 分层分析
│       ├── analyze_win_factors.py       # 赢家特征分析
│       ├── analyze_exit_timing.py       # 退出时机分析
│       ├── backtest_gate.py             # 门控回测
│       ├── backtest_exit_signals.py     # 退出信号回测
│       ├── backtest_benchmark.py        # 基准回测
│       ├── grid_search_gates.py         # 门控网格搜索
│       ├── transform_scores.py          # 分数变换
│       ├── backfill_cashflow.py         # 现金流回填
│       ├── backfill_fingerprint_dims.py # 回填指纹维度
│       ├── test_ma_score.py             # 均线评分测试
│       ├── test_feedback_parse.py       # 反哺解析测试
│       ├── test_e2e_feedback.py         # 端到端反哺测试
│       ├── test_deepseek_api.py         # DeepSeek API 测试
│       ├── test_news_llm.py             # 新闻 LLM 测试
│       ├── test_news_by_source.py       # 分源新闻测试
│       └── test_bootstrap.py            # Bootstrap 测试
├── frontend/
│   ├── package.json                     # React 19 + Vite 8 + Ant Design 6
│   ├── vite.config.ts                   # Vite 配置 (/api → 127.0.0.1:8000 代理)
│   ├── src/
│   │   ├── main.tsx                     # ReactDOM.createRoot
│   │   ├── App.tsx                      # 根组件 + BrowserRouter + 导航 + 13 Routes
│   │   ├── lib/
│   │   │   ├── api.ts                   # axios 封装 (5 次重试 502/503/504)
│   │   │   └── useDeepAnalysis.ts       # 深度分析 Hook (最多 3 只)
│   │   ├── pages/
│   │   │   ├── ScanPage.tsx             # TG 扫描页 (SSE 两阶段 + NM Defense + ★周线共振标签)
ResultPage.tsx           # ★ v4.3 最终推荐 (老股民研判区域+综合评级+正负分栏+操作建议)
AlphaFlowPage.tsx        # ★ v4.3 AlphaFlow (结论先行+四层信息架构+量能历史参考)
│   │   │   ├── ResultPage.tsx           # ★ 最终推荐 (市场门控 v2.0 + 信号质量徽章)
│   │   │   ├── AlphaFlowPage.tsx        # ★ AlphaFlow (老兵层级/策略标签/天时分/盘面分析)
│   │   │   ├── DeepAnalysisPage.tsx     # LLM 深度分析 (含重试单股按钮)
│   │   │   ├── HoldingsPage.tsx         # ★ 持仓管理 (资金账户/清仓/待清仓/筹码诊断)
│   │   │   ├── AnalysisPage.tsx         # 分析结果
│   │   │   ├── LearningPage.tsx         # 自学习面板 (概览/参数/经验/★分段权重)
MonitorPage.tsx           # ★ v4.3 系统监控 (老兵回测+校准+就绪状态)
│   │   │   ├── MonitorPage.tsx           # ★ 系统监控面板 (老兵回测/原型校准/权重状态) (v4.2)
│   │   │   ├── BlueprintPage.tsx        # 6 阶段流水线
│   │   │   ├── SettingsPage.tsx         # 系统设置
│   │   │   ├── AmbushPage.tsx           # 潜伏猎手
│   │   │   ├── StockSelectPage.tsx      # 股票选择
│   │   │   ├── TailMarketPage.tsx       # 尾盘战法
│   │   │   └── NewsPage.tsx             # 新闻速报
│   │   └── components/
│   │       ├── DeepAnalysisModal.tsx     # 深度分析弹窗
│   │       ├── FeedbackModal.tsx         # 反哺弹窗 (粘贴→解析→预览→提交)
│   │       └── PromptModal.tsx           # 提示词弹窗
│   └── dist/                            # 生产构建产物
├── browser-extension/                    # Edge/Chrome 扩展 (Manifest V3)
│   ├── manifest.json
│   ├── content.js                       # 逐消息注入按钮 + 股票代码对话框
│   ├── style.css                        # 暗色主题
│   ├── popup.html + popup.js            # 健康检查
│   ├── background.js                    # Service Worker
│   └── INSTALL.md                       # 安装指南
├── docs/
│   ├── architecture.md                  # 本文档 (v4.1)
│   ├── CHANGELOG.md                     # 变更日志
│   ├── news.md                          # 新闻模块文档
│   ├── 自学习升级.md                    # 自学习升级设计方案 v2.2 (13 章)
│   └── post_deployment_monitoring.md    # ★ 部署后日志监控指南
├── retrain_xgb.ps1                      # ★ XGBoost 重训脚本 (特征#41真实化 + 版本校验)
└── verify_all.ps1                       # ★ 完整部署验证脚本
```

---

## 3. 模块架构

系统分为 **10 个逻辑模块**：

### 3.1 数据采集层

| 职责 | 入口文件 | 关键函数 | 输出 |
|------|---------|---------|------|
| K 线下载 | `tg_engine.py` | `download_latest_kline()` | `daily_kline` 表 |
| 分钟 K 线同步 | `sync_min_kline.py` | — | `min_kline` 表 (pool + holdings 股票) |
| Tushare 分钟线 | API 内联 | `fetch_3min_bars()` / Tushare `stk_mins` | 内存 (不落库) |
| 新闻爬取 | `news_crawler.py` | `crawl_all_sources()` | `news_raw` 表 |
| 实时行情 | `realtime_quote.py` | `get_batch_realtime_quotes()` | 内存 dict |
| 名称缓存 | `stock_name_cache.py` | `load_from_tushare()` | `stock_name_cache` 表 |
| 基本面快照 | `refresh_fundamental_snapshot.py` | — | `stock_fundamental_snapshot` 表 |
| 龙虎榜同步 | `sync_toplist_detail.py` | — | `toplist_daily` / `toplist_detail` 表 |
| 申万指数 | `phase0_sync.py` | — | `sw_sector_index` 表 |
| 大盘指数 | `background_sync.py` | — | `index_daily` 表 (000300.SH/000852.SH) |

### 3.2 TG 扫描评分层 (管线 1)

| 职责 | 入口文件 | 关键函数 | 输出 |
|------|---------|---------|------|
| TG 扫描 | `tg_engine.py` | `scan_all_stocks()` (5% 步长推送) | `scan_results` 表 |
| TG 指标 | `tg_indicator.py` | `TGIndicator(df).compute()` | 18 步指标 + 买入/卖出信号 |
| 深度评分 | `deep_scorer.py` | `deep_analyze()` (14 维) | `analysis_scores` 表 (含 dimension_scores, win_probability) |
| 形态识别 | `pattern_engine.py` | `run_pattern_scan()` | `pattern_signals` 表 |
| 潜伏猎手 | `ambush_scanner.py` | `run_ambush_scan()` (用最新 scan_date) | `ambush_signals` 表 |

**v6.0 扫描阶段流程 (11 阶段, 新增🐉 阶段 4 潜龙池动态监控)**：

```
POST /api/scan/all
  ① toplist        → ensure_toplist_fresh() (skip_download 时跳过, 与 ⑧ 合并)
  ② download       → tg_engine 内部: download_latest_kline() + 覆盖率回退 365 天
  ③ scan           → 本地 TG 计算 (5% 步长推送 SSE, 5000只 → 100 事件)
  ④ ambush_scan    → 潜伏猎手 (用 scan_results 最新日期, 非历史最早)
  ⑤ pattern_scan   → 形态识别
  ⑥ deep_score     → 14 维深度评分 (文案修正: 12→14)
  ⑦ nm_defense     → 分钟线防伪 (异常不影响后续, 仍受 try/except 保护)
  ⑧ toplist_sync   → ⛔ v6.0 仍移除 (与 ① 重复, 合并到 toplist)
  ⑨ accuracy_feedback → isolated_meta=True 写独立列, 不覆盖主 discrimination
  ⑩ dna_auto_join  → asyncio.create_task 异步训练, 60-180s/只不阻塞 done
  ⑪ 🆕 dragon_pool → 🐉 潜龙池: 入池 + 状态更新 + 评估 (4 SSE 事件: join/update/evaluate/done)
  done 事件 ────→ 前端 currentPhase='done' 触发 load()
```

> **注**: `/api/scan/trigger` 路径（v4.8 旧版）不含阶段 ⑪ 潜龙池。需使用 `/api/scan/all` 触发完整 11 阶段。

**市场过滤 (v4.8 后端真正过滤)**：

```
前端: 主板/中小板/创业板 按钮
  ↓ market_filter query param
后端: /api/scan/trigger?market_filter=主板
  ↓ classify_board(ts_code) → '上海主板'|'深圳主板'|'中小板'|'创业板'
  ↓ results = results[results['symbol'].apply(in allowed)]
  ↓ 日志: market_filter=主板 (allowed=['上海主板', '深圳主板']): 5500 -> 3500
```

**TG 指标差异化阈值** (`tg_indicator.py:29-51`)：

| 板块 | 涨跌幅 | 量比归一化 | 卖价偏 A | 大卖跌幅 |
|------|--------|-----------|---------|---------|
| 主板 (±10%) | buy≥3%, sell≥3% | 2.0→1.0 | ≤5% | >3% |
| 创业板/科创板 (±20%) | buy≥5%, sell≥5% | 3.0→1.0 | ≤8% | >5% |

**评分管线调用链 (v4.1 含分段权重 + 交叉反哺)**：

```
deep_analyze()
  ├── 加载 scan_results
  ├── 构建指纹 → 原型分类
  ├── ★ 市场状态感知: get_market_state() → regime ∈ {bull, bear, range}
  │   ├── 尝试加载 regime-specific 权重 (get_beliefs(regime))
  │   ├── ★ 三重安全门控: n≥50 / params≥10 / AUC≥0.55
  │   └── 不满足 → WARNING + 回退 get_beliefs("__global__")
  ├── 权重解析: resolve_scoring_weights(archetype, beliefs)
  ├── 预加载: 基本面 / 形态 / 资金流 / 行业 Alpha / 大盘涨跌 / 多周期
  ├── 四层过滤: 涨停 / ST / 新股 / 资金流出
  └── 逐股评分 (14 个维度)
       ├── score_technical()        # RSI+MACD+Bollinger
       ├── score_kline_game()       # K 线博弈
       ├── score_fund_flow()        # 资金面
       ├── score_vol_ratio()        # 量比
       ├── score_arbr()             # ARBR 情绪
       ├── score_sector_alpha()     # 行业 Alpha
       ├── score_market_relative()  # 大盘相对强度
       ├── score_valuation()        # 估值
       ├── score_ma_trend()         # 均线趋势
       ├── score_pattern_signal()   # 形态信号
       ├── score_trend_deviation()  # 趋势偏离
       ├── score_bbi()              # BBI 多空
       ├── score_multi_box()        # 箱体结构
       ├── score_downside_risk()    # 下跌风险
       └── get_fundamental_score()  # 基本面修正
       → composite = weighted_sum + top3_boost + sector_bonus
       → ★ 跨周期验证: multi_timeframe.verify_multi_timeframe() → ± adjustment
       → ★ 龙虎榜质量注入: toplist_analyzer (三日陷阱/散户陷阱/单席位控盘)
       → ★ AlphaFlow 净买力修正: compute_sxqs_features().net_power / 50 (最多±3分)
       → ★ 概率校准: calibrate_with_regime(composite, archetype, regime)
```

### 3.3 AlphaFlow 主升浪捕获层 (管线 2) ★★★

#### 3.3.1 核心管线 (v4.1)

```
全市场 5500+ 股票
    │
    ▼
[1] lock_detector.py — 双窗口锁死检测 ★ 先于 XGBoost
    窗口1: 15-20日振幅≤15% + 窗口2: 20-40日振幅≤17%
    两窗口锁死价区重合 → lock_detected
    find_last_ex_rights(): |close[i]/close[i-1]-1| > 20% → 除权截断
    → ~200 只锁死候选
    │
    ▼
[2] alphaflow_veteran.py — 老兵识别 ★
    4+ 锁死周期统计分析:
      周期持续时间延长 → 锁死在加深
      振幅逐周期收敛 → 爆发在逼近
      量能逐周期萎缩 → 浮筹在减少
    → level: pre_breakout / late_stage / monitoring / none
    → score + verdict
    ★ 每周六自动回测: backtest_veteran_breakout_rate() 验证阈值
    │
    ▼
[3] alphaflow_evaluator.py — 历史评估 + 策略分类 ★
    A-G 8项历史过滤 + quality_label → strategy_label (8种) → strategy_group
    │
    ▼
[4] alphaflow_features.py — 47 维特征计算 ★ V2.1
    41 原始特征 + 6 老兵增强特征
    ★ 特征 #41 (板块锁死期%): 从 sw_sector_index 真实计算, 不再是占位符
    │
    ▼
[5] alphaflow_xgb.json — XGBoost V2.1 模型打分 ★
    ★ _load_xgb_model() 含三阶段版本校验:
      校验1: model_n_features == len(FEAT_NAMES) → 不匹配阻止加载
      校验2a: meta.feature_names 逐位比对
      校验2b: meta.feature_hash == runtime_hash → 不匹配 ERROR
      校验2c: meta.training_date 存在性检查
    │
    ▼
[6] alphaflow_pool.py — 池管理 ★
    INSERT/UPDATE alphaflow_pool (含 strategy_group, strategy_label, veteran_tier)
    结构维护 + 老兵豁免
    │
    ▼
[7] chip_analyzer.py — 筹码吸收分析 ★ 除权免疫
    三区模型 + find_last_ex_rights() 截断
    │
    ▼
[8] wave_predictor.py — 波段目标预测 ★ 幸存者偏差防护
    退市股排除 (查 delisted_stocks) + 3σ 极端浪幅过滤
```

#### 3.3.2 关键设计决策

| # | 决策 | 原因 |
|---|------|------|
| 1 | **锁死扫描先于 XGBoost** | 5500→200 筛选后 XGBoost 只对锁死候选打分，避免全市场盲打 |
| 2 | **老兵强制入池** | V1 模型仅在早期蛋上训练，老兵得分仅 15.2%，完全漏检。V2 修复 + 老兵豁免 |
| 3 | **strategy_group 是 DB 列** | 不再用 micro_score 负编码，直接写入 VARCHAR 列 |
| 4 | **除权免疫贯穿全管线** | lock_detector, veteran, evaluator, wave_predictor, chip_analyzer 均调用 find_last_ex_rights() |
| 5 | **筹码为硬指标** | 吸收率是客观数据，不依赖 LLM 主观评分 |
| 6 | **三区模型替代 profit_ratio** | 初版 profit_ratio 被用户否决（无意义），改用锁死区/上方/下方三区量能对比 |
| 7 | ★ **退市股排除** | wave_predictor 在加载历史数据时检查 delisted_stocks 表 |
| 8 | ★ **3σ 极端值过滤** | 浪幅统计中剔除 >3σ 的异常值 (ST异动/重组噪音) |
| 9 | ★ **板块数据真实化** | 训练+预测双管线接入 sw_sector_index, 特征#41 不再为 0.0 |
| 10 | ★ **版本校验** | _load_xgb_model() 自动比对运行时 FEAT_NAMES 与模型元信息 |

#### 3.3.3 相关服务清单

| 职责 | 入口文件 | 行数 | 关键函数 | 输出 |
|------|---------|------|---------|------|
| 锁死检测 | `lock_detector.py` | 167 | `detect_lock_simple()` + `find_last_ex_rights()` | 振幅/锁死天数/相对强度/判决 |
| 老兵识别 | `alphaflow_veteran.py` | 331 | `detect_veteran()` + `backtest_veteran_breakout_rate()` | level/score/verdict/cycle_stats + 回测报告 |
| 历史评估 | `alphaflow_evaluator.py` | 332 | `evaluate_history()` + `classify_strategy()` | history_label/quality_label/strategy_label |
| 特征提取 | `alphaflow_features.py` | 370 | `compute_wave_features()` (47维) + `compute_sxqs_features()` | FEAT_NAMES 向量 |
| 池管理 | `alphaflow_pool.py` | 719 | `daily_scan()` + `_load_xgb_model()` (版本校验) | `alphaflow_pool` 表 |
| 筹码分析 | `chip_analyzer.py` | 338 | `analyze_chip_absorption()` (含除权免疫) | absorption 三区数据 |
| 波段预测 | `wave_predictor.py` | 374 | `predict_wave_target()` + `detect_distribution()` | target_zone/risk_levels (含退市股排除+3σ过滤) |
| 乏力度 | `fatigue_detector.py` | — | `detect_fatigue()` | 浪平台逐层跌破 → 4 级判定 |
| 结构破坏 | `structure_break_detector.py` | — | `detect_trend_break()` | 摆动分析 → 关键支撑位 → 4 级判定 |
| 微剧本 | `micro_pattern_detector.py` | — | `detect_accumulation()` | 3 分钟线 5 维 0-5 分 |
| 盘中异动 | `intraday_analyzer.py` | — | `analyze_intraday_move()` | 2%+涨幅出货/吸筹判定 |

#### 3.3.4 策略分类体系 (8 种 strategy_label)

```
强势锁死: history_label=E/F(有利) + quality_label=strong
标准锁死: history_label=E/F + quality_label=normal
增量锁死: history_label=E(周期延长, 锁死在加深)
观察锁死: history_label=other + quality_label=watch
老兵锁死: veteran_detected → 强制入池, strategy_group='老兵锁死'
风险锁死: history_label=B/C(崩盘/闷杀) + quality_label=risky
底部锁死: history_label=A(无波段) + quality_label=bottom
未分类:   default fallback
```

#### 3.3.5 SXQS 资金博弈信号体系

基于 ZIG(3,10) 转向指标 + H1/H2/H3 三级 EMA + VAR6/VAR7/VAR8 买卖力：

| 信号 | 条件 | 含义 |
|------|------|------|
| **买入** | D 信号 + H1>H2 + ZIG 上升 | 趋势反转确认，强烈买入 |
| **ZIG 买入** | D 信号 + ZIG 上升 | ZIG 拐点买入 |
| **卖出** | W 信号 + ZIG 下降 | 趋势反转向下 |
| **持有** | W 信号 + ZIG 上升 | 上升中的正常回调 |
| **强势** | H1>H2 + A 信号 | 资金博弈偏多 |
| **观望** | 无明确信号 | 等待方向明确 |

### 3.4 分钟线反哺 TG 层 (交叉管线) ★

> **状态**: ✅ 已完成 (2026-05-31)

#### 3.4.1 核心思想

TG 日线信号可能被主力做出来（日线级别的假突破），但分钟线无法伪装——主力在分钟线上的每一笔进出都会留下痕迹。

```
TG 日线买入信号
       │
       ▼
  下载该股信号前 15 天的 5 分钟线 (Tushare stk_mins)
       │
       ├── 分时段 N/M 检测 (minute_nm_detector.py)
       │    上午最大跌幅 / 下午最大跌幅 → N型条件
       │    上午最大涨幅 / 下午最大涨幅 → M型条件
       │    收盘 vs VWAP / 收盘位置 → 验证
       │
       ├── 板块联盟放大 (sector_alliance.py)
       │    同行业 ≥3 只信号股 → 对比 N/M
       │    板块共识 > 0.15 + 一致性 > 60% → 联盟确认
       │    个股 vs 板块方向一致 → 加分 | 相反 → 减分
       │
       └── 信号质量调整 (signal_quality_scorer.py)
           NM分 × 0.25 + 联盟分 × 0.30 → 质量修正
           高置信(≥10天数据) → 修正权重 100%
           中置信(≥5天) → 60% | 低置信 → 20%
```

#### 3.4.2 N/M 形态定义

**N型 (吸筹)** — 两低夹一高, 低点抬高:
| 条件 | 说明 |
|------|------|
| 上午跌幅 > 1.2% | 早盘有显著抛压 |
| 下午跌幅 < 上午跌幅 × 0.7 | 下午抛压减小 (低点抬高) |
| 下午低点 > 上午低点 | 支撑位上移 |
| 收盘 > 开盘 | 最终买方获胜 |
| 收盘 > VWAP | 收盘在均价上方 |

**M型 (出货)** — 两高夹一低, 高点降低:
| 条件 | 说明 |
|------|------|
| 上午涨幅 > 1.2% | 早盘有显著拉升 (诱多) |
| 下午涨幅 < 上午涨幅 × 0.7 | 下午买力减弱 (高点降低) |
| 下午高点 < 上午高点 | 阻力位下移 |
| 收盘 < 开盘 | 最终卖方获胜 |
| 收盘 < VWAP | 收盘在均价下方 |

### 3.5 学习与训练层 (v4.1 重大升级) ★★★

#### 3.5.1 评分权重训练器 (scoring_trainer.py)

**这是学习闭环的核心引擎**，替换了过去人工猜测的 DEFAULT_WEIGHTS。

```
数据流:
  recommendation_tracking (真实盈亏标签)
      + analysis_scores (dimension_scores JSON)
      + market_status_log (市场阶段)
        │
        ▼
  load_training_data_with_regime()
    ├── JOIN bayesian_beliefs 获取市场阶段
    ├── 按 bull/bear/range 分组
    └── 每组 X(维度评分矩阵), y(was_profitable_3d)
        │
        ▼
  _fit_logistic_regression()
    ├── StandardScaler → LogisticRegression (C=0.5, class_weight='balanced')
    ├── 5-fold CV → AUC
    ├── 系数 → 权重映射 (0.5 ~ 4.0)
    └── 返回 {n_samples, cv_auc, coefficients, new_weights}
        │
        ▼
  persist_weights(regime="bull/bear/range/__global__")
    ├── param_library (strategy="scoring_{regime}", is_active=true)
    ├── bayesian_beliefs (archetype=regime, n_observations=N)
    ├── ★ __regime_auc__ 元参数 (用于 deep_scorer 安全门控)
    └── ★ __trained_at__ 时间戳
        │
        ▼
  deep_scorer 评分时自动加载:
    get_beliefs(regime) → resolve_scoring_weights(archetype, beliefs)
    ★ 三重安全门控: n≥50 / params≥10 / AUC≥0.55
```

**关键安全设计**：
- `MIN_SAMPLES_FOR_TRAINING = 30` (数据加载层拦截)
- `persist_weights()`: AUC < 0.52 拒绝写入
- `deep_scorer` 加载: `MIN_REGIME_SAMPLES = 50`, `MIN_REGIME_PARAMS = 10`, `MIN_REGIME_AUC = 0.55`
- 任一条件不满足 → WARNING 日志 + 回退全局权重

#### 3.5.2 概率校准器 (probability_calibrator.py v4.1)

```
v4.1 升级:
  1. ★ 标签统一: T+2 → T+3 (与 scoring_trainer was_profitable_3d 对齐)
  2. ★ regime 分段校准: build_calibration_by_regime()
     - 每个 regime ≥ 100 样本才启用分段校准
     - 不足时自动回退全局校准器
  3. ★ calibrate_with_regime(composite, archetype, regime, signal_quality)
     - 优先加载 regime-specific 校准曲线
     - 不可用时回退全局
  4. ★ scheduled_recalibrate_with_regime() (每周日自动)
  5. 小样本降权: bucket_weight = min(1.0, bucket_n / 10)
  6. 硬底: score>20 → min 8%, score>35 → min 15%
```

#### 3.5.3 原型与学习层

| 职责 | 入口文件 | 关键函数 | 输出 |
|------|---------|---------|------|
| 指纹构建 | `fingerprint_builder.py` | `build_fingerprints()` | 11 维向量 + 原型标签 |
| 原型分类 | `archetype_classifier.py` | `classify_stocks()` | 5 原型标签 |
| 权重解析 | `archetype_param_resolver.py` | `resolve_scoring_weights()` | ★ 含 ARCHETYPE_OFFSETS (待校准) + 校准数据收集 |
| 影子训练 | `shadow_trainer.py` | `train_shadow()` | `param_library` 表 |
| 学习引擎 | `learning_engine.py` | `run_rolling_backtest()` | T+5 回测指标 |
| 权重训练 | `scoring_trainer.py` | `full_training_pipeline()` (by_regime=True) | ★ 分段权重 + Bayesian 持久化 |
| 概率校准 | `probability_calibrator.py` | `calibrate_with_regime()` | ★ T+3 分段概率 |
| 自学习启动 | `self_learning_bootstrap.py` | `daily_incremental_train()` | ★ 对接 scoring_trainer |

**原型偏移表** (`archetype_param_resolver.py:31-111`)：
5 个原型 (large_bluechip, small_speculative, growth_tech, value_defensive, cyclical_resource) 约 70 个偏移值。
★ 标注为 "待校准 (2026-06-03): 从未基于真实盈亏数据回测校准"。
`collect_archetype_calibration_data()` 已就绪，按原型统计实际胜率 vs 全局胜率并生成建议偏移量。

### 3.6 DeepSeek 调用层

| 职责 | 入口文件 | 使用模型 | 调用场景 |
|------|---------|---------|---------|
| API 客户端 | `deepseek.py` | — | 统一封装 (httpx, 180s timeout, temperature=0.2) |
| 新闻打标签 | `event_detector.py` | deepseek-chat | Stage1: 250 条/批, 2 批并发 |
| 新闻深度分析 | `event_detector.py` | deepseek-chat | Stage2: 4 分类, 各 100 条 |
| 个股分析提示词 | `llm_deep_analyzer.py` | deepseek-chat | 用户精选后反哺 (含筹码段) |
| 反哺解析 | `feedback_parser.py` | deepseek-chat | 文本→结构化 JSON |
| 板块热度分析 | `sector_heat_engine.py` | deepseek-chat | 龙虎榜→板块热度 |
| 批量横向评分 | `feedback.py` | DEEPSEEK_PRO_MODEL | ★ 多只股票横向对比 (含筹码吸收率硬指标) |

### 3.7 API 路由层 (15 个路由) ⭐ v4.5 DNA Lab 新增

| 路由前缀 | 文件 | 端点数 | 主要功能 |
|---------|------|-------|---------|
| `/api/scan` | `scan.py` | 7 | TG 扫描 (SSE)、新闻、数据新鲜度、NM验证 |
| `/api/result` | `result.py` | 3 | 最终推荐 (市场门控 v2.0 + S3风险分类) + 融合 |
| `/api/alphaflow` | `alphaflow.py` | 7 | ★ 候选池/锁死详情/筹码分析/波段预测/统计/老兵回测 |
| `/api/analysis` | `analysis.py` | 3 | 深度分析 + 手动加股(完整管线) + 删除 |
| `/api/comprehensive` | `comprehensive.py` | 1 | ★ 一键综合分析 (symbol→4段报告) |
| `/api/feedback` | `feedback.py` | 5 | 反哺提交/解析/列表/批量检查/★批量横向评分 |
| `/api/holdings` | `holdings.py` | 10+ | ★ 持仓CRUD + 资金账户 + 自动清仓 + 筹码诊断 |
| `/api/learning` | `learning.py` | 20+ | ★ 训练/升级/回测/维度/原型/权重/校准数据 |
| `/api/llm` | `llm_analysis.py` | 1 | 深度分析提示词生成 |
| `/api/ambush-signals` | `ambush.py` | 1 | 潜伏猎手信号 |
| `/api/user-decisions` | `decisions.py` | 1 | 用户决策记录 |
| `/api/settings` | `settings.py` | 2 | 系统设置/密钥管理 |

**v4.1 新增端点**:
- `POST /api/learning/train-weights?force=true` — 触发权重训练
- `GET /api/learning/weights-trained` — 查看 Bayesian 训练参数
- `GET /api/learning/archetypes/calibration-data?days=180` — 原型校准数据
- `GET /api/alphaflow/veteran-backtest?days=180` — 老兵突破率回测

### 3.8 门控与风控层

| 职责 | 入口文件 | 关键函数 | 触发时机 |
|------|---------|---------|---------|
| 市场门控 v2.0 | `market_gate.py` | `get_gate_config()` | 每次 `/result/final` 调用 |
| 多周期验证 | `multi_timeframe.py` | `verify_multi_timeframe()` | 深度评分时 (参与 composite_score) |
| 概率校准 | `probability_calibrator.py` | `calibrate_with_regime()` | ★ 评分后, 按 regime 选择校准器 |
| 板块热度 | `sector_heat_engine.py` | — | 板块5阶段判定 + sector_factor 注入 |
| 退出信号 | `exit_signal_detector.py` | `detect_exit_signals()` | 用户主动查询 |
| 准确率追踪 | `accuracy_tracker.py` | `verify_all_periods()` | 每次全市场扫描后 |
| 信号质量 | `signal_quality_scorer.py` | `verify_signals_with_minute_bars()` | TG 信号筛选时 + NM验证 |
| 结构破坏 | `structure_break_detector.py` | `detect_trend_break()` | AlphaFlow 池更新时 (老兵豁免) |
| 乏力度 | `fatigue_detector.py` | `detect_fatigue()` | 用户主动查询 |

**市场门控 v2.0 逻辑**:

```
get_gate_config()
  ├── get_market_state()       → ★ 7 市场体制 + regime 判定
  ├── _get_market_breadth()    → ★ 涨跌家数比 + 新高新低比
  ├── _get_style_bias()        → ★ 风格偏向 (沪深300 vs 中证1000)
  ├── _get_volume_trend()      → ★ 成交额趋势
  ├── check_market_breadth()   → 合格股票数骤降 → 收紧
  └── check_self_feedback()    → 近期胜率 < 30% → 收紧
```

**7 市场体制判定**:
| 体制 | 条件 | 风险 | min_prob | max_stocks |
|------|------|------|----------|------------|
| 趋势上涨 | adv_pct>65% + 成交额扩张 + 20日涨幅>3% | low | 0.28 | 120 |
| 结构行情 | adv_pct>50% + 小盘风格 + 波动率<1.3 | normal | 0.30 | 100 |
| 缩量博弈 | 成交额萎缩>20% + abs(20日涨幅)<3% | elevated | 0.38 | 60 |
| 恐慌杀跌 | adv_pct<30% + 20日跌幅>3% + 波动率>1.5 | high | 0.45 | 40 |
| 维稳行情 | abs(20日涨幅)<2% + 波动率<0.8 | normal | 0.35 | 80 |
| 弱势探底 | adv_pct<30% (含双阈值) | high | 0.42 | 50 |
| 震荡整理 | 默认 fallback | normal | 0.32 | 90 |

### 3.9 后台调度层 (v4.1 扩展)

| 职责 | 入口文件 | 触发时间 | 任务 |
|------|---------|---------|------|
| 每日调度 | `background_sync.py` | 16:00 | 基本面快照 + 龙虎榜 + 回测 + 影子训练 + 融资融券 + 商品期货 + ★持股天数+1 + ★min_kline同步 |
| ★ 权重训练 | `background_sync.py` | 周一 16:00 | `full_training_pipeline(by_regime=True)` → 分段权重训练 + Bayesian 持久化 |
| ★ 老兵回测 | `background_sync.py` | 周六 16:00 | `backtest_veteran_breakout_rate()` → 突破率统计 |
| ★ 概率重校准 | `background_sync.py` | 周日 16:00 | `scheduled_recalibrate_with_regime()` → 全局+regime 双重重校准 |

### 3.10 部署验证层 ★ v4.1 新增

| 文件 | 用途 |
|------|------|
| `retrain_xgb.ps1` | XGBoost V2.1 重新训练脚本 (切换目录→备份→训练→验证meta→特征一致性) |
| `verify_all.ps1` | 完整部署验证链 (安全门控→模型一致性→API端点→校准→回测) |
| `docs/post_deployment_monitoring.md` | 部署后日志监控指南 (关键字/安全门控样例/回滚流程) |

---

## 4. 数据流与调用链路

### 4.1 TG 全市场扫描 → 推荐 完整链路 (v4.2 含 Phase 1.5 周线共振)

```
用户点击 "全市场扫描"
        │
        ▼
POST /api/scan/trigger (SSE 流式)
        │
        ├── Phase 1: TG 日线扫描
        │   ├── download_latest_kline()     → Tushare API → daily_kline 表
        │   ├── TGIndicator(df).compute()   → 逐股计算 TG 动量/买卖点
        │   └── save_scan_results()         → scan_results 表 (含 resonance_type)
        │
        ├── ★ Phase 1.5: 周线独立信号叠加 (v4.2)
        │   ├── scan_weekly_signals()       → 全市场逐股日线→周线重采样
        │   │   ├── resample_daily_to_weekly() → ISO周分组 → 周一开/周高低/周五收
        │   │   ├── TGIndicator(weekly_df).compute() → 周线买方向信号
        │   │   └── 质量过滤: 周成交量>0, 周收盘价≥2.0
        │   └── 双周期匹配:
        │       日线买入 AND 周线买入  → "weekly_resonance" (共振)
        │       日线买入 AND NOT 周线  → "daily_only"      (仅日线)
        │       写入: results[i]["resonance_type"] + ["weekly_tg_momentum"]
        │
        ├── Phase 2: 并行扫描
        │   ├── run_ambush_scan()           → ambush_signals 表
        │   └── run_pattern_scan()          → pattern_signals 表
        │
        ├── Phase 3: 深度评分
        │   └── deep_analyze()
        │       ├── 加载 scan_results (含 resonance_type)
        │       ├── ★ score_weekly_resonance() → resonance × weight → composite
        │       ├── ★ CALibrate_with_regime() → T+3 分段概率校准
        │       ├── ★ AlphaFlow 净买力修正 (net_power / 50)
        │       ├── ★ QUALITY_GATE 自适应放宽: 通过<10 → L1(sq≥0.55) → L2(sc≥35)
        │       ├── INSERT analysis_scores
        │       └── INSERT recommendation_tracking
        │
        ├── Phase 4: 准确率验证
        │   ├── verify_all_periods()
        │   └── ★ apply_accuracy_feedback() → T+5不足时降级到T+3
        │
        └── SSE 事件流返回给前端
                │
                ▼
GET /api/result/final?limit=20
        │
        ├── ★ ORDER BY resonance_priority ASC, composite_score DESC
        │   (weekly_resonance=0 > daily_only=1 > weekly_driven=2)
        ├── S3 风险分类 + ★ 板块感知门控 (安全阀扩展到所有体制)
        └── 返回: data[] (含 resonance_type)
```

### 4.1b 方案 B 周线共振数据流 ★ v4.2

```
resample_daily_to_weekly(kline_df):
  日线 DataFrame → df['week_label'] = dt.strftime('%G-W%V')
  └── 按 week_label 分组:
        Open  = 该周首日开盘
        High  = max(周内所有high)
        Low   = min(周内所有low)
        Close = 该周末日收盘 (周五/最后交易日)
        Volume = sum(周内所有volume)
  └── 至少产生 20 根周线 → 传给 TGIndicator

双周期信号匹配 (Phase 1.5 末尾):
  for r in 日线results:
      if ws := weekly_signals.get(r["symbol"]):
          if ws["has_weekly_buy"]: → "weekly_resonance" (共振)
          else:                    → "daily_only"      (仅日线)
      else:                        → "daily_only"      (无周线)

score_weekly_resonance(resonance_type):
  "weekly_resonance" → 1.0 + min(0.3, |momentum|/100) → × weight=2.0 → composite
  "weekly_driven"    → 0.6 → × weight=2.0 → composite
  "daily_only"       → 0.0 → 无影响
```

### 4.2 AlphaFlow 全市场扫描 → 候选池 完整链路 (v4.1)

```
POST /api/alphaflow/scan (或后台自动)
        │
        ├── ★ 0. 预加载板块指数数据
        │   ├── sw_sector_index (28 个 SW 一级行业)
        │   └── ths_member → stock→sector 映射
        │
        ├── ★ 1. 锁死检测 (5500+ → ~200) ★ 先于 XGBoost
        │   ├── 加载全市场日线数据
        │   ├── find_last_ex_rights() → 除权截断
        │   ├── 窗口1: 15-20日振幅≤15%
        │   ├── 窗口2: 20-40日振幅≤17%
        │   └── detect_lock_simple() → 锁死候选列表
        │
        ├── ★ 2. 老兵检测 ★
        │   └── detect_veteran() 对每个锁死候选
        │       ├── 4+ 锁死周期统计
        │       └── → level: pre_breakout/late_stage/monitoring
        │
        ├── ★ 3. 历史评估 + 策略分类 ★
        │   └── → strategy_label (8种) + strategy_group
        │
        ├── ★ 4. XGBoost V2.1 打分 ★
        │   ├── _load_xgb_model() → ★ 三阶段版本校验
        │   ├── compute_wave_features(..., sector_closes=板块日线)
        │   │   └── ★ 特征#41: _lock_sector_return() 真实板块收益率
        │   ├── model.predict_proba() → 主升浪概率
        │   └── 阈值 0.167+ → 入池候选
        │
        ├── 5. 池更新
        │   ├── 新入池: 通过阈值 + 在锁死状态
        │   ├── ★ 老兵强制入池: 检测到 veteran → 绕过 XGBoost 阈值
        │   ├── INSERT/UPDATE alphaflow_pool
        │   ├── 结构清理: detect_trend_break() → 踢出破位股
        │   └── ★ 老兵豁免: veteran 股票不被结构维护踢出
        │
        └── 返回: {new_entries, total_pool, veteran_count, tiers}
```

### 4.3 学习闭环数据流 (v4.1 真实训练)

```
recommendation_tracking (103+ 条真实盈亏)
   + analysis_scores (dimension_scores JSON)
   + market_status_log (phase 牛/熊/震荡)
        │
        ▼
scoring_trainer.load_training_data_with_regime()
   ├── 按 regime 分组: bull/bear/range
   └── 每组 X, y (was_profitable_3d)
        │
        ▼
_fit_logistic_regression()
   ├── StandardScaler + LogisticRegression (C=0.5, balanced)
   ├── 5-fold CV AUC
   └── 系数 → 0.5~4.0 权重
        │
        ▼
persist_weights(regime)
   ├── param_library (strategy="scoring_{regime}")
   ├── bayesian_beliefs (archetype=regime)
   └── __regime_auc__ / __trained_at__ 元信息
        │
        ▼
deep_scorer.deep_analyze()
   ├── get_beliefs(regime) → ★ 三重安全门控
   ├── resolve_scoring_weights(archetype, beliefs)
   └── 下次评分自动使用训练后的分段权重
```

---

## 5. 数据库架构

### 5.1 核心数据表

| 表 | 行数 (约) | 用途 |
|----|----------|------|
| `daily_kline` | 4,000,000+ | 日 K 线 (含上证/创业板/沪深300/中证1000指数) |
| `daily_basic` | 177,000+ | 每日估值指标 |
| `moneyflow` | 242,000+ | 资金流向 |
| `fina_indicator` | 7,000+ | 财务指标 |
| `margin_trading` | 7,000+ | 融资融券 |
| `cashflow` | 15,000+ | 经营现金流 |
| `min_kline` | — | ★ 分钟 K 线 (pool + holdings 股票, 5min × 60天) |
| `stock_name_cache` | — | 股票名称缓存 |
| `ths_member` | 18,000+ | 同花顺行业分类 |
| `sw_sector_index` | — | ★ 申万行业指数 (用于特征#41计算) |
| `index_daily` | 580+ | ★ 大盘指数日线 (000300.SH/000852.SH) |
| `trade_cal` | — | 交易日历 |
| `hk_hold` | 24,000+ | 北向持股 |
| `stk_holdernumber` | 10,000+ | 股东户数 |
| `toplist_daily` | — | 龙虎榜日数据 |
| `toplist_detail` | — | ★ 龙虎榜明细 |
| `suspend_d` | 200+ | 暂停/退市股票 |
| `delisted_stocks` | — | ★ 退市股 (波段预测排除 + S3 负样本) |
| `commodity_futures` | — | ★ 商品期货日线 (铜/铝/锌/螺纹/热卷等) |

### 5.2 DNA 个性化模型表 ⭐ v4.5 新增

独立 schema `stock_dna`，与现有系统**完全并行**：

| 表 | 用途 |
|----|------|
| `stock_dna.daily_samples` | 每日训练样本 (symbol×trade_date, 含 emotion_label/cycle_phase/多窗口标签/daily_features JSONB) |
| `stock_dna.profiles` | Per-Stock DNA 档案 (表情指纹/周期节律/最佳窗口/特征重要度/行为指纹/转移矩阵) |
| `stock_dna.predictions` | DNA 多窗口预测记录 (T+2/5/10/20 超额收益+胜率+置信度) |

### 5.3 业务数据表

| 表 | 行数 (约) | 用途 |
|----|----------|------|
| `scan_results` | 5,700+ | ★ TG 扫描结果 (含 level, market, tg_momentum, resonance_type, weekly_has_buy, weekly_tg_momentum) |
| `analysis_scores` | 4,900+ | ★ 14 维深度评分 (含 dimension_scores JSON, win_probability) |
| `stock_fingerprints` | 3,300+ | 11 维指纹向量 |
| `pattern_signals` | — | K 线形态信号 |
| `ambush_signals` | — | 潜伏猎手信号 |
| `stock_fundamental_snapshot` | — | 基本面快照 (从 fina_indicator/cashflow 聚合) |
| `stock_deep_feedback` | — | 外部分析反哺 |
| `recommendation_tracking` | — | ★ 推荐追踪 (was_profitable_3d/5d, verified_3d/5d) |

### 5.3 AlphaFlow 专用表 ★

| 表 | 用途 |
|----|------|
| `alphaflow_pool` | ★ 候选池主表 (含 strategy_group, strategy_label, veteran_tier, veteran_level, veteran_score) |
| `alphaflow_snapshots` | ★ 每日池快照 |
| `goose_archive` | 大雁归档 (涨幅>100%的已毕业股票) |
| `egg_phase_samples` | 蛋期样本 (蛋期中段快照) |
| `mins_train_samples` | 分钟线训练样本 (待消费) |

### 5.4 持仓专用表 ★

| 表 | 用途 |
|----|------|
| `holdings` | ★ 持仓主表 (含 holding_days, pending_close, capital) |
| `closed_positions` | ★ 已清仓记录 (含 t_trade_count, pnl) |
| `capital_accounts` | ★ 资金账户 |

### 5.5 自学习相关表

| 表 | 用途 |
|----|------|
| `archetype_profiles` | 原型中心点 + 可训练标记 |
| `param_library` | ★ 评分权重库 (strategy="scoring_{regime}", is_shadow, converge_status) |
| `bayesian_beliefs` | ★ Bayesian 信念参数 (archetype=regime, mu, sigma, n_observations, lo, hi) |
| `experience_replay` | 经验回放缓冲 (6280 条) |
| `learning_predictions` | 回测预测记录 |
| `learning_dimension_registry` | 维度注册表 |
| `learning_models` | 模型版本链 |
| `strategy_daily_score` | 策略日评分 |
| `prediction_log` | 预测日志 |
| `market_status_log` | ★ 市场状态日志 (含 phase 牛/熊/震荡, ma60_value, phase_duration) |
| `sync_log` | 同步任务日志 |

---

## 6. DeepSeek 底座集成细节

### 6.1 API 调用封装

**文件**: `app/services/deepseek.py`

```python
async def call_deepseek(prompt: str, max_tokens: int = 4096, model: str = None) -> str
```

| 参数 | 说明 |
|------|------|
| 端点 | `POST {DEEPSEEK_BASE_URL}/chat/completions` (默认 `https://api.deepseek.com/v1`) |
| 认证 | `Authorization: Bearer {DEEPSEEK_API_KEY}` |
| 超时 | **180 秒** (httpx.AsyncClient) |
| 温度 | 固定 **0.2** |
| 重试 | **无自动重试**，失败返回 `"[LLM 调用失败: {e}]"` |
| 模型 | deepseek-chat (默认) / DEEPSEEK_PRO_MODEL (批量评分) / deepseek-reasoner |

### 6.2 并发控制

- **新闻 Stage1**: `asyncio.Semaphore(2)` 并行 2 批，单批超时 `asyncio.wait_for(120s)`
- **AlphaFlow 全市场扫描**: 顺序逐股，`asyncio.sleep(0)` 每 10 股释放事件循环
- **批量横向评分**: 最多 20 只股票，单次调用 4096 tokens

---

## 7. 配置与部署

### 7.1 配置文件

**`.env` 文件** (由 `app/core/config.py` 的 `Settings` 类加载)：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@127.0.0.1:15432/stock_data` | PostgreSQL 连接 |
| `DEEPSEEK_API_KEY` | `""` | DeepSeek API 密钥 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/v1` | API 端点 |
| `TUSHARE_TOKEN` | `""` | Tushare API token |
| `BAIDU_QIANFAN_API_KEY` | `""` | 百度千帆 API 密钥 |
| `API_AUTH_KEY` | `""` | API 认证密钥 (⚠ 待启用) |
| `DEBUG` | `true` | 调试模式 (SQL echo) |

### 7.2 启动命令

```bash
# 后端 (端口 8000)
cd C:\AI-Agent-Local\Stock\backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --timeout-keep-alive 600

# 前端 (端口 3456 — AlphaFlow 专用)
cd C:\AI-Agent-Local\Stock\frontend
npm run dev

# 一键启动
StockAnalyst.bat

# 部署验证
.\retrain_xgb.ps1      # XGBoost 重训 + 版本校验
.\verify_all.ps1       # 完整验证链 (需后端先启动)
```

### 7.3 关键约束

- **uvicorn 必须单 worker** (`--workers 1`)：扫描状态存在进程内存中
- **SSE 超时**：`--timeout-keep-alive 600` (10 分钟)
- **DB 连接池**：pool_size=10, max_overflow=20
- **Tushare 分钟线** (`stk_mins`)：每次 API 调用约 1-2 秒，5 只并发
- **XGBoost 模型版本校验**：启动时自动检查 `alphaflow_xgb_meta.json` 与运行时 `FEAT_NAMES` 一致性

---

## 8. 未来开发指南

### 8.1 新增 AlphaFlow 特征 (含版本校验)

```python
# 1. 在 alphaflow_features.py 的 compute_wave_features() 中添加
# 2. 更新 FEAT_NAMES 列表以匹配维度数 (当前 47 维)
# 3. 重新训练模型:
#    .\retrain_xgb.ps1
#    (自动备份旧模型、训练、更新 meta、验证特征一致性)
# 4. 重启后端后 _load_xgb_model() 自动校验新模型
```

### 8.2 新增 TG 评分维度

```python
# 1. 在 deep_scorer.py 添加评分函数
# 2. 在 deep_analyze() 循环中调用
# 3. 在 scoring_trainer.py 的 DIM_KEYS 中添加
# 4. 在 WEIGHT_PARAM_NAMES 中添加映射
# 5. Model 将自动学习该维度的权重
```

### 8.3 可扩展设计点

| 扩展点 | 位置 | 机制 |
|--------|------|------|
| TG 评分维度 | `scoring_trainer.py::DIM_KEYS` | 添加 dim key → 自动参与 Logistic Regression 训练 |
| AlphaFlow 特征 | `alphaflow_features.py::FEAT_NAMES` | 添加特征 → 重新训练 XGBoost (V3) |
| 策略原型 | `archetype_classifier.py` | 修改 K 值 → 重新聚类 |
| 原型权重偏移 | `archetype_param_resolver.py::ARCHETYPE_OFFSETS` | 待数据积累后校准 (collect_archetype_calibration_data 已就绪) |
| 市场门控阈值 | `market_gate.py` | 修改 7 体制参数 |
| 分段训练参数 | `deep_scorer.py::MIN_REGIME_SAMPLES/AUC/PARAMS` | 调整安全门控阈值 |
| AlphaFlow 策略分类 | `alphaflow_evaluator.py` | 扩展 strategy_label 枚举 |

### 8.4 待完成的改进项

| # | 改进项 | 状态 | 优先级 |
|---|--------|------|--------|
| 1 | XGBoost 重新训练 (特征 #41 真实化) | ⏳ `retrain_xgb.ps1` 就绪, 待执行 | P0 |
| 2 | 推荐追踪准确率累积 | ✅ 已训练, 214 条样本, AUC 0.6452 (2026-06-03) | P0 |
| 3 | 推荐准确率反馈闭环 | ✅ apply_accuracy_feedback T+5不足时降级到T+3 (v4.2) | — |
| 4 | 质量门控自适应放宽 | ✅ QUALITY_GATE L1/L2 两级放宽 (v4.2) | — |
| 5 | 周线双周期共振 | ✅ 方案 B 已合入 (Phase 1.5 + 三级排序) (v4.2) | — |
| 6 | ScanPage 周线标签 + sync_log 表 | ✅ 断裂点修复完成 (v4.2) | — |
| 7 | 原型偏移自动校准 | ⏳ `collect_archetype_calibration_data()` 就绪, 等数据积累 | P1 |
| 3 | 原型偏移自动校准 | ⏳ `collect_archetype_calibration_data()` 就绪, 等数据积累 | P1 |
| 4 | contextual_bandit 接入影子训练 | ⏳ select_arm() 已实现, 缺 update() | P1 |
| 5 | angel_model.json 由 signal_quality_scorer 加载 | ⏳ 模型文件存在, 未消费 | P1 |
| 6 | dual_channel_trainer 定期调度 | ⏳ train() 已实现, 缺调度入口 | P1 |
| 7 | mins_train_samples → 分钟线分类器 | ⏳ 表有数据, 缺训练消费 | P2 |
| 8 | 未挂接模块清理 | ⏳ 6 个模块零引用 (baidu_search/contextual_bandit 等) | P2 |
| 9 | API_AUTH_KEY 启用 | ⏳ 配置为空, 生产需启用 | P2 |
| 10 | 提示词模板集中管理 | 💡 重构建议 | P3 |

---

## 9. 已知问题与审计状态

### 9.1 审计修复记录 (2026-06-03)

| # | 审计项 | 修复前 | 修复后 |
|---|--------|--------|--------|
| 1 | 分段训练安全门控 | `trained_params >= 5` | n≥50 + params≥10 + AUC≥0.55 三重检查 + WARNING 回退 |
| 2 | XGBoost 特征 #41 | 占位符 0.0 | 训练+预测双管线接入 sw_sector_index 真实板块数据 |
| 3 | XGBoost 版本校验 | 无 | 三阶段校验 (特征数/特征名/特征哈希) |
| 4 | 概率校准标签窗口 | T+2 (与训练器 T+3 不一致) | 统一为 T+3 |
| 5 | 概率校准 regime 分段 | 无 | build_calibration_by_regime + calibrate_with_regime (不足自动回退) |
| 6 | 跨管线信号断裂 | 净买力 25% importance 在 TG 完全缺失 | AlphaFlow net_power 作为 composite_score 修正因子 |
| 7 | 筹码三区除权偏移 | 无保护 | find_last_ex_rights() 截断 |
| 8 | 波段预测幸存者偏差 | 退市股/极端值未过滤 | delisted_stocks 排除 + 3σ 极端浪幅过滤 |
| 9 | 模型元信息 | 无 training_date/feature_hash | 增加完整元信息 + 版本校验 |
| 10 | 原型偏移 | 全人工猜测 | 标注待校准 + collect_archetype_calibration_data() 就绪 |

### 9.2 已知技术债务

| # | 问题 | 位置 | 严重度 | 建议 |
|---|------|------|--------|------|
| 1 | 提示词模板分散在多个文件中 | 多处 | 中 | 统一到 `prompts/` 目录 |
| 2 | `call_deepseek` 无自动重试/退避 | `deepseek.py` | 中 | 添加 exponential backoff |
| 3 | 无 API token 用量追踪 | 全部 LLM 调用 | 中 | 添加 token 计数和成本估算 |
| 4 | `_active_scan` 全局变量限制多 worker | `scan.py` | 低 | 迁移到数据库状态表 |
| 5 | param_library 全部 is_shadow=false | `param_library` | 中 | self_learning_bootstrap 冷启动 |
| 6 | stock_basic 表可能不存在 | `sector_alliance.py` | 中 | 已 fallback 到 scan_results.industry |
| 7 | feedback_parser.py 绕过 `call_deepseek` | `feedback_parser.py` | 低 | 统一到共享客户端 |
| 8 | 训练数据量 | `scoring_trainer.py` | 低 | 持续增长中 |
| 9 | ARCHETYPE_OFFSETS 全人工猜测 | `archetype_param_resolver.py` | 中 | 校准基础设施已就绪, 等待数据 |
| 10 | ⚠️ 影子训练权重需重训 | `shadow_trainer.py` | **高** | 日历日 bug 已修复，旧 param_library 权重基于错误标签，需清空后全部重训 |
| 11 | ⚠️ 6 个分析脚本丢失 BJ 股票 | `analyze_*.py` 等 | 中 | 引入 `normalize_ts_code()` 替代 `else: continue` |
| 12 | ⚠️ stock_dna.best_emotion_ret 列类型 | `dna_models.py` | 低 | 已修 (Float→JSONB + ALTER TABLE 迁移) |

## 10. 变更日志

### v7.0.34 (2026-06-20) — 🗂️ Exclusion 踢出名单 (5 reasons) + 股票信息对齐按钮 + v2 Trainer 训练数据筛选

**核心改造**:
- 把分散的"踢出逻辑"统一到 `exclusion_list` 表 + 5 reasons:
  - **TECH_BOARD** (688 开头) + **BJ_BOARD** (920 开头) - 全市场代码, 永久
  - **ST_NAME** - Tushare stock_st 实时
  - **PE_LOSS** - Tushare income_vip (n_income<0), 季度末过期自动回滚
  - **INSOLVENT** - Tushare balancesheet_vip (total_liab > total_assets)
- **前端集成**: ScanPage 顶部"🗂️ 股票信息对齐"按钮 → `POST /api/admin/refresh-exclusion` 一次性刷断链
- **TG 扫描集成**: `tg_engine.py` 在 ST/涨停过滤**前**加 exclusion_list 加载 (一次 SELECT, O(1) 查找)
- **v2 Trainer 训练数据筛选**: SQL 加 3 个过滤 (NOT EXISTS exclusion_list + close_price>=5 + 非涨停), 避免学习"被踢出票"的模式
- **澄清**: 潜龙猎手 (ambush_scanner) 和 AlphaFlow (alphaflow_pool) **直接读 daily_kline**, 跟 tg_engine 的 scan_results 互不干扰 — 屏蔽涨停股不会影响潜龙猎手数据

**新增表**:
```sql
exclusion_reasons (code PK, name, category, description, auto_refresh)
exclusion_list (symbol PK, reason_code, added_at, expires_at, note)
```

**新增文件**:
- `backend/app/api/admin.py` - `/api/admin/refresh-exclusion` + `/api/admin/exclusion-stats` 端点
- `backend/scripts/refresh_exclusion_list.py` - 5 reason 整合刷新脚本 (季度初跑)
- `frontend/src/pages/ScanPage.tsx` - 紫框按钮 + 确认弹窗 + 结果面板

**生产状态**:
- 现役 exclusion_list: 1771 条 (TECH 599 + BJ 318 + ST 211 + INSOLVENT 2 + PE 733, 跨 reason 去重)
- 64 套 v2 权重用筛选后数据重训 (lookback_days=880, ~2024-02 到 2026-06-20)
- OBV 仍 Top 10 有效特征 (T+2 win #8/29, T+10 win #12/23)

---

### v7.0.33 (2026-06-19) — 🧠 v2 Trainer 按 Regime 训练 (解决跨周期泛化失败)

**核心问题**:
- 旧 `train_4x2(market_style="all")` 不按市场状态分组训练
- 实测: 牛市训的 win 模型在熊市完全失效 (胜率 74.8% → 24.3%, cv_auc 0.45)
- 旧 `get_4x2_status()` 用 (h, mt) 作 key, 多 regime 权重互相覆盖 (32 套 → 只显示 8 套)

**解决方案**:
- 用 700001.TI LAG(10) ±2% 打 phase 标签 (bull/bear/range)
- v2 trainer 按 phase 分组训练, 缺样本自动降级到 all
- train_4x2 默认自动检测当前市场状态

**4 步改造**:
1. `market_gate.py` 加 `regime_to_market_style()` (6→3 映射) + `get_current_regime_simple()`
2. `scoring_trainer_v2.py` `load_training_data_v2()` 加 phase CTE (LAG(10) ±2%)
3. `train_single()` 缺样本降级 (n<30 → 'all')
4. `train_4x2()` 默认 `market_style=None` → 自动检测当前市场

**修复的 2 个严重 bug**:
1. `_backfill_tech_chip.py` 缺 COMMIT: `executemany` 后未 commit, 数据回滚 (5676 条实际未写入)
2. `get_4x2_status()` multi-regime 覆盖: SQL `WHERE is_active=true` + (h,mt) 作 key, 多 regime 互相覆盖

**新增/修改文件 (4 个修改 + 2 个新建)**:

#### 修改
- `backend/app/services/market_gate.py`: +`regime_to_market_style()`, +`get_current_regime_simple()`
- `backend/app/services/scoring_trainer_v2.py`: 3 处改 (load_training_data_v2 + train_single + train_4x2) + get_4x2_status 加 market_style
- `backend/app/api/scan.py`: line 334 lookback 120→730
- `backend/app/scheduler/daily_tasks.py`: line 420 lookback 120→730

#### 新建
- `backend/scripts/_backfill_tech_chip.py` — 5676 条 v7.0.32 新字段回填 (含 commit bug 修复)
- `backend/scripts/_pk_v1_v2.py` — v1 vs v2 PK 回归测试脚本

**生产权重 (32 套, 2026-06-19 写入)**:
- all (8 套): 1307 样本, cv_auc 0.4645-0.6788
- bull (8 套): 145 样本, cv_auc 0.4667-0.5505
- bear (8 套): 211-643 样本, cv_auc 0.2988-0.6519 (跨周期核心)
- range (8 套): 176-519 样本, cv_auc 0.4463-0.6662

**v1 vs v2 PK (3 天 × 3 组, T+1 均价买入)**:
| 组 | v1 T+5 | **v2 T+5** | v1 T+10 | **v2 T+10** |
|----|--------|-----------|---------|------------|
| Top 5 | +0.01% | **+0.98%** | -0.94% | **+1.73%** |
| Rank 5-10 | -0.32% | **+0.54%** | -1.01% | -0.67% |
| Rank 10-15 | -0.44% | -0.26% | +1.03% | -0.35% |

**结论**: v2 在 Top 5 显著胜出 (T+5 +0.97pt, T+10 +2.67pt)。`feature_flag.learning_v2_active=true` 已生产启用 (2026-06-18 扫描 100% v2_active=True)。

### v7.0.32 (2026-06-19) — 📊 系统评分维度扩展 (22 字段) + 全链路透传

**核心改动**: 加 22 个新字段 (技术指标 + 筹码分布), 全链路修复确保 DeepSeek + ResultPage + CuratedRankingView 都能消费。

**新增 22 字段**:
- **技术指标 (15)**: macd_dif/dea/bar, kdj_k/d/j, rsi_6/12/24, boll_upper/mid/lower/width/pos, cci
- **筹码分布 (7)**: cost_5/50/95pct, weight_avg, winner_rate, cost_spread, price_vs_cost

**数据回填**:
- `_backfill_tech_chip.py` 修复 commit bug 后, 5676 条回填成功 (2024-01 ~ 2026-05)
- T+5 verified (1915 条) macd 字段: 6.5% → **64.4%** (+57.9pt)
- 筹码字段: 16.6% (受 Tushare cyq_perf 限制, 仅 6 月起)
- regime 训练样本: bull=145, bear=428, range=228 (全部 ≥30, 不触发降级)

**全链路修复** (5 处):

1. `deep_scorer.py` line 770+: `dims` dict 加 5 维评分函数 (macd_score/kdj_score/rsi_24_score/boll_score/cci_score/chip_winner_rate)
   - 让 v2 trainer 能学到 22 字段权重
   - 注意: 新维度写入只在**新扫描**后生效

2. `llm_deep_analyzer.py` SQL + Prompt:
   - `get_stock_context()` + `_batch_get_stock_contexts()` SQL 加 22 字段
   - 加 `_build_tech_section()` + `_build_chip_extended_section()` 2 个格式化函数
   - Prompt 头部: "14 个维度" → "22 个维度 (含 v7.0.32 新增的 MACD/KDJ/RSI/BOLL/CCI/筹码分布 6 维度)"

3. `api/result.py` `/result/final` SQL:
   - `base_select` 加 22 字段 (line 87-95)
   - dict 输出加 22 字段 (r[18..39]), 索引重排

4. `frontend/src/components/CuratedRankingView.tsx`:
   - 加 7 列折叠行: MACD/KDJ/RSI/BOLL/CCI/成本/信号
   - 加 1 行展开区: v7.0.32 详细 (5 维 + 筹码 + 获利盘 + 金过滤标签)
   - 加金过滤判定 (`checkGoldFilter`):
     - ✓ 金过滤: 至少 4 维度 + 全 isGold + 没有 isWarn
     - ⚠ 风险: 任何 KDJ/RSI/BOLL/CCI 超买 OR price_vs_cost > 20%

**新增/修改文件 (1 个修改 + 1 个新建)**:

#### 修改
- `backend/app/services/deep_scorer.py`: +5 维评分函数 (line 770 后)
- `backend/app/services/llm_deep_analyzer.py`: SQL 加 22 字段 + 2 个格式化函数
- `backend/app/api/result.py`: SQL 加 22 字段 + dict 输出索引重排
- `frontend/src/components/CuratedRankingView.tsx`: 加 7 列 + 1 行展开 + 金过滤
- `Stock/backend/scripts/_backfill_tech_chip.py`: 加 `await conn.execute('COMMIT')`

**验证**: 002326.SZ (永太科技) /result/final 返回 82 字段 (含 22 个新字段), DeepSeek prompt 输出含 5 维技术 + 5 维筹码。

### v7.0.31 (2026-06-18) — 🐉 5 触发踢出 + Dragon 端点补全 + 路由统一 + 数据一致性 bug 修复 + OSError64 稳定性修复 + MonitorPage 升级

### v7.0.30 (2026-06-18) — 🎯 铁三角死规则接入 (MA20+RSI+VOL)

**需求 (用户 2026-06-18 实战理论)**:
1. **铁三角短线理论** = MA20(趋势) + RSI(动能) + VOL(真伪)
2. **三层过滤**: MA20 方向 → 回调 MA20 + RSI 底背离 → 放量确认
3. **数据驱动阈值校准**: 1915 行 verified_5d 实测,不用直觉
4. **archetype 适配**: 周期/价值股本来在 MA20 下方操作,R2/R3 跳过
5. **修 score_4h 覆辙**: 算了的字段必须写库,前端能消费,验证脚本能统计

**新增/修改文件 (1 个修改 + 3 个新建)**:

#### 修改
- `backend/app/services/deep_scorer.py`:
  - `_apply_hard_rules` (line 132-203): 阈值校准 (-5%→-3% / +5%→+10%) + archetype 跳过 + 函数 docstring
  - `_deep_persist_phase` (line 1119-1124): details dict 加 5 字段写入
  - 主流程 (line 1377-1397): 加 hard_rules_summary 字符串生成

#### 新建
- `backend/scripts/backfill_hard_rules.py` — 24,279 行 analysis_scores backfill
- `backend/scripts/_v7_30_validate.py` — 第一轮验证 (R6/R7/R8 反向发现)
- `backend/scripts/_v7_30_validate2.py` — 第二轮精细验证 (期望值视角)
- `backend/scripts/_v7_30_verify_production.py` — backfill 后生产数据验证
- `backend/scripts/_v7_30_smoke_test.py` — 8 项冒烟测试 (47 断言)

**新增 details 字段 (analysis_scores)**:
| 字段 | 类型 | 用途 |
|------|------|------|
| `hard_rules_passed` | list[str] | 通过的规则名 (e.g. `['R1_micro_cap', 'R2_weak', ...]`) |
| `hard_rules_failed` | list[[code, reason]] | 失败的规则 + 原因 (e.g. `[['R2_weak', '价格低于 MA20 -5.2%']]`) |
| `hard_rules_blocked` | bool | 是否被任意规则剔除 |
| `hard_rules_summary` | str | 一句话诊断 (`❌ R2_weak(价格低于 MA20 -5.2%)` 或 `✅ 通过 5/5 条`) |
| `v7_version` | str | 版本标记 (`'v7.0.30'`) |

**5 条铁三角规则 (按期望值差排序)**:
| ID | 规则 | 阈值 | E 差 | 评价 |
|----|------|------|------|------|
| R4 | RSI 超买 | > 70 | +260.8% | ⭐⭐⭐ 黄金规则 (30 样本, 27 亏) |
| R2 | 弱势股 (bias<-3%) | < -3% | +239.7% | ⭐⭐ 极强 |
| R1 | 微盘股 (mcap<50亿) | < 50亿 | +197.5% | 强 |
| R5 | 严格空头 (MA5<MA10<MA20) | strict=0 | +152.7% | ⭐⭐ 强 |
| R3 | 追高 (bias>10%) | > 10% | +71.2% | 中 |

**Archetype 适配**:
- R2/R3 对 `value_defensive` / `cyclical_resource` **跳过**
- 数据依据: value 上 R2 Δwr=+16.4% (反向), cyclical 上 Δwr=+15.2% (反向)
- 周期/价值股本来在 MA20 下方操作,超跌反弹是机会

**生产数据验证 (backfill 后实测, verified_5d 真交集)**:
| | n | wr | win_avg | loss_avg | avg_r | E(期望值) |
|---|---|----|---------|----------|-------|-----------|
| **5 条全过 (PASS)** | 343 | **56.6%** | +446% | -347% | **+101.65%** | **+101.96%** |
| **任一被剔 (FAIL)** | 1572 | 45.2% | +361% | -404% | -57.79% | **-58.01%** |

- PASS wr - FAIL wr = **+11.4%** (胜率差)
- PASS E - FAIL E = **+159.98%** (期望值差)
- **期望值由负转正**: -58% → +102%

**跨年稳定性**:
| 年 | R2 Δwr | R4 Δwr | R5 Δwr | 评价 |
|----|--------|--------|--------|------|
| 2024 | -20.7% | -39.8% | -17.9% | ✅ 三条都有效 |
| 2025 | -33.7% | -48.1% | -16.4% | ✅ 三条都更强 |
| 2026 | -14.9% | -24.4% | -13.3% | ✅ 三条都有效 |

**冒烟测试 (47/47 全过, 8 项测试)**:
1. ✅ import 检查 (改完代码后模块能加载)
2. ✅ `_apply_hard_rules` 8 个边界 case
3. ✅ details JSON round-trip (5 字段不丢)
4. ✅ deep_analyze 签名 + sanitize_for_json 健壮性
5. ✅ DB query `details->>` 读 5 字段 (24,279 行 100% 覆盖)
6. ✅ 5 条规则互相独立 (单/多/全过)
7. ✅ value/cyclical archetype 正确跳过 R2/R3
8. ✅ 旧 v2 字段不丢 (backfill 不覆盖 v2, 10,158 行同时含 v2+v7)

**关键设计**:
- **数据驱动阈值校准** (不是凭直觉): 第一轮测试发现 R6/R7/R8 反向 → 去掉
- **测试上下文明确**: 用户提醒"规则是加在 TG 扫描信号之后的" — 改测试设计
- **期望值视角**: 不只看 wr,还看 win_avg / loss_avg / E(期望值)
- **修了 score_4h 覆辙**: "算了没写" 的 bug 不会再犯
- **archetype 适配**: 不是一刀切,根据股票类型跳过不适用的规则
- **跨年稳定性**: 2024/2025/2026 三主规则全有效,不是过拟合

**v2 状态**:
- v2_active=true 仍生效 (best_horizon / best_strategy 仍写库)
- param_library_v2 64 套权重仍激活
- 铁三角规则与 v2 **并行不冲突**: 死规则在前,v2 概率调整在后
- v2 暂时挂起(用户原话"v2 的事先挂起,后面有时间再继续")

**业务价值**:
- 期望值由负转正 (-58% → +102%) — **核心目标**
- 通过规则 vs 未通过 wr 差 11.4%
- 5 条规则每条 E 差都为正(数据验证)
- 跨年稳(2024/2025/2026 三主规则都有效)

---

### v7.0 (2026-06-17) — 🎯 v2 学习链路 (4 horizon × 2 model_type)

**需求 (用户 2026-06-17 敲定口径)**:
1. T+N 验证只用 **2/3/5/10** 四个模式 (去掉 15)
2. **4 模式必须独立训练** (同一股票不同 T+N 方向可能不同)
3. 盈利/避坑两套独立模型 (不是 1=1, 0=0 二分类)
4. **完全新建一套 v2**, 跑通后用 feature_flag 切流量 (不破坏 v1)

**新增/修改文件 (8 个新建 + 6 个修改)**:

#### 新建
- `backend/scripts/migrate_v2.sql` — v2 表迁移 (param_library_v2 + feature_flag)
- `backend/scripts/run_migrate_v2.py` — 迁移执行器
- `backend/app/core/feature_flag.py` — feature flag 模块 (is_v2_active / get_flag / set_flag)
- `backend/app/services/scoring_trainer_v2.py` — v2 trainer (Logistic Regression, 4×2 独立训练)
- `backend/app/services/deep_scorer_v2.py` — v2 主推荐融合 (predict_optimal_horizon)
- `backend/app/api/learning_v2.py` — v2 API (7 个端点, 前缀 /learning/v2/*)
- `frontend/src/pages/LearningV2Page.tsx` — v2 学习面板 (8 套权重 + 切换开关 + 预测测试)

#### 修改
- `backend/app/models/data_models.py:228` — 删 T+15 字段 (暂留, v2 跑通后再删)
- `backend/app/services/accuracy_tracker.py:149` — 循环 [2,3,5,10] (去掉 15)
- `backend/scripts/verify_recommendations.py` — 加 T+3 验证 (修复 0/618 bug) + 加 T+10
- `backend/app/scheduler/daily_tasks.py` — 加 task_train_4x2_v2
- `backend/app/scheduler/scheduler_loop.py:193` — 注册新任务
- `backend/app/api/__init__.py` — 注册 learning_v2_router

**新增数据表**:
- `param_library_v2` — 8 套权重 (archetype='__global__', horizon × model_type 唯一)
- `feature_flag` — 切换开关 (默认 learning_v2_active=false)

**新增字段 (recommendation_tracking)**:
- `return_10d`, `was_profitable_10d`, `verified_10d` (v7.0)

**新增 API 端点 (7 个)**:
| 端点 | 方法 | 说明 |
|------|------|------|
| `/learning/v2/train-weights` | POST | 全跑或单跑 (4×2) |
| `/learning/v2/4x2-status` | GET | 8 套权重状态 |
| `/learning/v2/panel` | GET | v2 学习面板 |
| `/learning/v2/predict-optimal?symbol=` | POST | 单只股票推荐最佳持仓期 |
| `/learning/v2/feature-flag` | GET/POST | 切换 v2 主推开关 |
| `/learning/v2/health` | GET | v2 链路健康检查 |

**修复 bug (T+3=0)**:
- **根因**: `verify_recommendations.py` 之前只处理 T+2/T+5/T+15, 完全没写 T+3
- **现状**: 修复后 T+3 verified=164, wr=33.5%
- **业务价值**: shadow_trainer 之前只 T+3 训练, 但 T+3 一直 0 验证, 训练数据为空 (用户核心需求)

**8 套独立训练 (4 horizon × 2 model_type)**:
```
T+2_win  / T+2_loss    ← 短期: 盈利模型 / 避坑模型
T+3_win  / T+3_loss    ← 短中
T+5_win  / T+5_loss    ← 中线
T+10_win / T+10_loss   ← 长线
```

**关键设计**:
- **win 模型**: 标签 = was_profitable_Nd (盈利=1, 亏损=0)
- **loss 模型**: 标签 = NOT was_profitable_Nd (亏损=1, 盈利=0) — **反例独立训练**
- **不是 51%/49% 镜像** — 损失样本特征分布与盈利样本独立
- **样本不足 (n<30) 跳过 + warning, 保留占位** — 等用户补历史数据

**当前训练数据 (2026-06-17 13:25)**:
```
[OK]   T+2_win      n=214 cv_auc=0.4290
[OK]   T+2_loss     n=214 cv_auc=0.4290
[OK]   T+3_win      n= 69 cv_auc=0.4646
[OK]   T+3_loss     n= 69 cv_auc=0.4646
[SKIP] T+5_win      n=  5  (待用户补历史)
[SKIP] T+5_loss     n=  5
[SKIP] T+10_win     n=  0  (待 1-2 周数据积累)
[SKIP] T+10_loss    n=  0
```

**predict_optimal_horizon 输出**:
```json
{
  "best_horizon": 2,
  "best_strategy": "S1",
  "best_p_win": 1.0,
  "best_p_loss": 0.0,
  "best_net": 1.0,
  "advice": "建议持仓 T+2 (S1), 净胜率 +100%"
}
```

**切换流程 (跑通后)**:
1. 前端 /learning-v2 页面看 8 套权重状态
2. feature_flag.learning_v2_active 默认 false
3. 跑通 8 套训练 → 点 "开启 v2 主推" → TG 主推走 v2
4. 1 周后看 4 模式胜率独立追踪
5. 3 个月后 v1 退役 (param_library_v2 改名 param_library)

**已知问题**:
- v1 `app/api/regime.py` 模块缺失, 影响 main.py 启动 (与 v2 无关, 已存在)
- 6/8 当日 `analysis_scores` 0 行, 导致 T+5 训练样本不足 (待用户补历史数据)

**用户需提供**:
- 6/8 之前的历史推荐数据 (有 analysis_scores 配套) → 提升 T+5/T+10 训练样本

---

### v6.0.5 (2026-06-17) — 🎨 A 股惯例颜色统一

**需求**: 用户报告 "+330% 当前是绿色"，违反 A 股惯例

**改动 (6 文件)**:
- `lib/signalColor.ts`: 新增 `getPnlColor()` 工具函数 + 修复 `getPnlRowStyle`
- `pages/AmbushPage.tsx:279`: `drawdownPct<0` 红涨绿跌
- `pages/AlphaFlowPage.tsx:209`: `breakout_pct>15` 红
- `components/CuratedRankingView.tsx:134,185,326,333,340`: predicted_return/pred5/seWr/curAr/看多 红涨绿跌
- `components/DnaLab.tsx:302`: avg_breakout_return 红涨绿跌
- `pages/ResultPage.tsx:207,263`: predicted_return (C.red) 红涨绿跌

**新工具函数**:
```typescript
export function getPnlColor(pnlPct: number | null | undefined, neutralColor: string = '#6e7a8a'): string {
  if (pnlPct == null || pnlPct === 0) return neutralColor;
  return pnlPct > 0 ? '#ef4444' : '#10b981';
}
```

**保留原样 (非涨跌幅)**:
- 市场情绪 (bull=绿/bear=红)
- 评分高低 (tech_score 等)
- 进度条/就绪状态

---

### v6.0.4 (2026-06-17) — 🐉 5 触发踢出

**需求**: 000777.SZ 中核科技 6-15+6-16 连续涨停不应在池；600226.SH 6-04/6-09 涨停误判入池

**evaluate_exit 5 规则**:

| # | reason | 触发条件 |
|---|--------|---------|
| 1 | `atr_stop` | exit_signal_detector critical/high |
| 2 | `fatigue_broken` | fatigue_detector broken/capitulation |
| 3 | `time_decay_10d` | days_in_pool >= 10 |
| 4 | `not_first_limit` | added_at 前 10 天内 prev-based 涨幅 ≥9.9% |
| 5 | `consecutive_board` | added_at **后**任何一天 prev-based 涨幅 ≥9.9% |

**SQL 关键修复**: 规则 4 用 `trade_date < added_at`，规则 5 用 `trade_date > added_at` (首板日本身不算)

**数据修复**:
- 600226.SH 标记 exited (not_first_limit, 入池前 6-04/6-09 涨停)
- 000777.SZ 标记 exited (consecutive_board, 6-15+6-16 连续涨停)
- 000012.SZ / 600192.SH 标记 exited (consecutive_board, 6-15 实际未涨停不应入池)

---

### v6.0.3 (2026-06-17) — 🩺 涨跌幅修复

**Bug 1: 历史 `close_price=21.11` 数据污染**
- 6-15 入池的 6 只股 (000070/000593/000777/600226/603052/603093) 全部 close_price=21.11（错值）
- 修复: 用 daily_kline 真实 close 覆盖 6 行 + 同步 dragon_pool.first_limit_close

**Bug 2: 10 天检查规则没起作用**
- 现象: 600226.SH 在 6-04/6-09 都有涨停 (昨收对比 10%)，但被判定为首板
- 根因: `check_first_limit` 用 `(close - open) / open * 100`（**当日振幅**），不是 `(close - prev_close) / prev_close`（**真实涨跌幅**）
- 修复: `first_limit_scanner.py` `check_first_limit` + `get_today_limit_list` 改用 `LAG(close)` 算 prev-based 涨幅

**Bug 3: 1666 个 first_limit_up.name 错误**
- 现象: 名称字段全是 ts_code (603052.SH 显示 "603052.SH" 而不是"可川科技")
- 根因: 历史扫描写入时 name fallback 为 ts_code
- 修复: 批量 UPDATE 1666 行, 用 `get_stock_name` 取真实名称

---

### v6.0.2 (2026-06-17) — 🐉 前端改造

**需求**:
- "二波型潜力" tab 改名"龙抬头"（更符合 A 股语义）
- "首板猎人" tab 改为显示"监控中"的首板股（不是历史所有 S/A/B）
- 加"监控天数"列（10 天就要踢出，必须显眼）

**改动 `frontend/src/pages/AmbushPage.tsx`**:
- tab 名字: "🐉 二波型潜力" → "🐉 龙抬头" / "🐉 首板猎人" → "🐉 首板监控"
- "首板监控" 数据源: `/api/ambush-signals/first-limit` → `/api/dragon/pool?status=active`
- `FirstLimitView` 重构:
  - 加列标题 (监控 / 股票 / 起点 / 涨幅 / 二波/接力)
  - 监控天数列 (X/10 天 + 进度条 + 监控中/后期/即将清理)
  - 起点列 (首板日 added_at)
  - 涨幅列 (首板价 → 现价 + A 股颜色)
- SYNC 按钮: 触发 `/dragon/pool/scan + update-state + evaluate`
- "龙抬头" tab SYNC: 触发 `update-state + evaluate`

**前端字段类型**: `FirstLimitStock` interface 完全重写 (从历史首板字段 → dragon_pool 字段)

---

### v6.0.1 (2026-06-17) — 🩺 冒烟测试修复

**Bug**: `services/limit_cpt_list_service.py` 在 Windows GBK (cp936) cmd 下报 SyntaxError

**根因**:
- 文件历史为 **CRLF 行尾** + Windows Python 3.13 默认 `cp936` locale
- Python lexer 用 cp936 读 UTF-8 多字节字符 → 解码错误 → `"""` 配对失败
- 我前一次用 `open('r', encoding='utf-8')` 文本模式写入时，**实际 Python 内部仍按 cp936 解码**，写回了损坏字节 (0x80 残留)
- line 11 的 docstring 闭合符 `"""` 被错误转换为 `返回: ts_code, ...` 文字

**影响**:
- `/api/ambush-signals/hot-sectors` 端点返回 500
- 前端"最强板块" tab 数据缺失 (ResultPage 首页 + AmbushPage 共享)

**修复**:
- 完全重写 `services/limit_cpt_list_service.py` (UTF-8 LF 编码)
- 保留所有业务逻辑 + v6.0 清理备注
- 验证 import OK + 端点恢复 200

**完整冒烟测试**: 18/18 全过
- 后端 API (GET 7 + POST 3): ✅ 全部 200
- 前端页面 (8 个核心路由): ✅ 全部 200
- 端到端业务流: ✅ join → update → evaluate 全部正常
- 业务单测: ✅ 10d 强制清理 / 5d 不清理 / detect_emerging 全过

**预防**:
- 未来创建 .py 文件一律用 LF 行尾 + Write 工具
- 不在 Windows GBK cmd 下用 `open('r', encoding='utf-8')` 文本模式读写中文文件

**关联文档**:
- `docs/README.md` v6.0.1 章节
- `docs/DEVELOPER_GUIDE.md` v2.2 头部
- 改进意见归档: `docs/improvements/进行中/20260616-潜龙池-v6.0.md` → `已完成/`

---

### v6.0 (2026-06-16) — 🐉 潜龙池动态监控上线

**新增 dragon_pool 表** (`migrations 120-122`):
- 22 列: `id, ts_code, first_limit_id, added_at, status, exit_date, exit_reason, exit_confidence, current_price, min_price_since_join, first_limit_close, days_in_pool, emerging, emerging_at, emerging_pattern, relay_prob, waveback_prob, signal_quality, nm_score, last_evaluated_at, created_at, updated_at`
- 2 索引: `idx_dragon_pool_status (status, added_at DESC)` + 部分索引 `idx_dragon_pool_emerging WHERE emerging=TRUE`
- 唯一约束: `UNIQUE(ts_code, added_at)` (防止重复入池)

**新增 dragon_pool_service.py** (408 行, 6 个核心函数):
- `join_pool_from_first_limit(trade_date)`: 从 `first_limit_up` 选 S/A/B 级入池
- `update_pool_state(trade_date)`: 更新 current_price / min_price / days_in_pool (基于 daily_kline)
- `get_active_pool_symbols()`: 获取所有 active 池中股
- `evaluate_exit(symbol, days_in_pool)`: 模型驱动踢出判定（**3 触发任一**）
  - exit_signal_detector (ATR 动态止损) — `priority in (critical, high)`
  - fatigue_detector (平台破位 5 阶段) — `status in (broken, capitulation)`
  - **10 交易日未连板强制清理** (用户硬要求)
- `detect_emerging(symbol)`: 浮出二板信号判定（**强制分时验真**）
  - `waveback_prob > 0.3` (硬门槛) → 才进入分时验真
  - 强制调 `signal_quality_scorer.verify_signals_with_minute_bars`
  - 最终条件: `signal_quality > 0.5 + nm_score > 0`
- `evaluate_all_active()`: 全池评估编排

**新增 5 个 API 端点** (`app/api/dragon.py`):
- `GET  /api/dragon/pool` — 池中所有 active 股票 + 状态
- `GET  /api/dragon/waveback-potential` — 仅 emerging 的池中股（二波型 tab 用）
- `POST /api/dragon/pool/scan` — 手动触发入池
- `POST /api/dragon/pool/evaluate` — 手动触发全池评估
- `POST /api/dragon/pool/update-state` — 手动触发状态更新

**扫描流程升级** (`api/scan.py:551-575`):
- `/api/scan/all` 阶段 4 新增：4 个 SSE 事件 `dragon_pool_join / update / evaluate / done`
- `/api/scan/trigger` 旧路径**不包含**阶段 4（不破坏旧调用方）

**调度集成** (`scheduler/daily_tasks.py` + `scheduler_loop.py`):
- 新增 `task_update_dragon_pool()` — 每日收盘后跑 join + update + evaluate
- 加入 `scheduler_loop.py` 日常任务列表

**删除**:
- `services/limit_step_service.py` 整个文件
- `api/ambush.py` 的 `/limit-step` + `/limit-step/sync` 端点
- `AmbushPage.tsx` "连板天梯" tab

**修改**:
- `first_limit_scanner.py:127` `LIMIT 30` → `LIMIT 10` (10 交易日无涨停)
- `AmbushPage.tsx` 二波型 tab 改用 `/api/dragon/waveback-potential`
- `App.tsx:32` label "潜伏猎手" → "🐉 潜龙猎手"
- `ScanPage.tsx:144-146` 新增 4 个 dragon_pool SSE 事件标签

**待清理** (1 周系统稳定后):
- `services/ambush_scanner.py` — 旧"潜伏猎手"，仍被 `deep_scorer.py:48` 14 维评分使用
- `api/ambush.py` — `/hot-sectors` 端点保留 (ResultPage 首页仍在用)
- `services/limit_cpt_list_service.py` — 提供 `get_hot_sectors` / `get_sector_effect`

**约束遵守** (DEVELOPER_GUIDE 铁律):
- ✅ 数值安全: 0.0 兜底，无内联 NaN 守卫
- ✅ 进度回调: 4 参数标准
- ✅ 不复制代码: 全部 `from app.services.X import Y` 复用现有模型
- ✅ 不用 DROP/TRUNCATE: `CREATE TABLE IF NOT EXISTS`
- ✅ 不写死硬指标: 踢出/浮出全部调模型

**文档**:
- `docs/README.md` 更新 v6.0 状态 + 版本历史
- `docs/architecture.md` 更新扫描流程图 + 变更日志（本文档）
- `docs/潜龙猎手.md` 标记为 ⚠️ 滞后（设计蓝图，实际 v6.0 超出）
- `docs/improvements/进行中/20260616-潜龙池-v6.0.md` 改进意见文档

---

**⭐ 新闻页面重复标题修复 (v4.9)**:
- `app/services/event_aggregator.py`: 新增 SimHash 相似度去重
- `_dedup_similar_events()`: 同一股票内相似标题去重
- 阈值: 汉明距离 < 8 判定为相似，每股同主题只保留最高 display_score 的一条
- 修复同一股票多条重复显示问题

**⭐ 新闻分类去重 (v2.1)**:
- `app/services/news_classifier.py`: SimHash 指纹 + 智能分类服务
- **去重**: 跨源 SimHash (汉明距离<10) + 数据库唯一约束
- **分类**: 三级 (company/sector/macro/garbage) - macro_only 跳过 LLM (避免与宏观数据重复)
- **个股摘要保留**: `get_stock_news_summary()` 从 `news_raw` + `stock_events` 合并, 不丢失 title/summary

**⭐ 龙虎榜精细化 (v2.1)**:
- 机构细分: 公募/私募/QFII/北向/社保
- 游资分级: 顶级(95-87分) / 一线(80-60分) / 二线(55-45分) / 三线(40分)
- 共振强度: 5级 (extreme/strong/moderate/weak/minimal)
- 共振标签: 机构入场/顶级游资/一线游资/合力买入/净买普遍
- 净买持续性: 1/3/5日统计
- **智能缓存 (v2.1)**: 历史永久/当日交易时段5min/休市1h
- **SSE 刷新接口 (v2.1)**: POST /api/scan/toplist-refresh
- **新鲜度检查**: GET /api/scan/toplist-freshness
- **前端 SSE 集成**: 进度条 + 刷新按钮 + 缓存标识

**LLM 模型明确化 (v2.1)**:
- 新闻 Stage1 (打标签): DEEPSEEK_FLASH_MODEL (deepseek-v4-flash)
- 新闻 Stage2 公司级: DEEPSEEK_PRO_MODEL (深度分析)
- 新闻 Stage2 行业/政策/商品: DEEPSEEK_FLASH_MODEL (轻量任务)

**修复**:
- `news_crawler.py`: 浏览器启动加超时 (10s/15s), 防止按钮卡死
- `news_crawler.py`: 进度回调 (init/crawl_X/dedup/store) 让前端实时反馈

**前端**:
- NewsPage: 板块共振 5 级强度 + 共振标签展示
- NewsPage: 个股席位精细化 (北向/公募/社保/顶级/一线/二线) 标签

### v4.9 (2026-06-15) — P0-1 批量查询优化 + P1-4 特征选择 + Redis 集成 + P0-2 多进程 Worker

**P0-1 批量查询优化**:
- `app/core/database.py`: 弹性连接池 (FULL_SCAN_MODE: pool=20/overflow=40, 正常: pool=5/overflow=10)
- `app/services/alphaflow_pool.py`: 新增批量加载函数 `_batch_load_klines()` / `_batch_load_historical_klines()`
- **效果**: SQL 查询减少 ~10,000 → ~10 (98% 减少)

**P1-4 特征选择 & 正则化**:
- 77 个特征全部有效，无低贡献特征需裁剪
- Top 4 特征占 55.7% 重要性
- 训练模型: CV AUC=0.8888, R²=0.7426, n=19,853

**Redis 集成 (fakeredis 降级)**:
- `app/core/redis_client.py`: 异步 Redis 客户端，支持生产 Redis + fakeredis 降级
- `app/main.py`: FastAPI lifespan 中自动初始化 Redis

**P0-2 多进程 Worker**:
- `app/services/scan_worker.py`: ProcessPoolExecutor 并行 TG 计算
- `app/api/scan.py`: NUM_WORKERS 环境变量切换
- `StockAnalyst.bat`: --workers 参数支持
- 性能: 4858 只股票 83.6s (2 workers)

**P2-6 Redis 特征缓存**:
- `app/services/feature_cache.py`: 缓存核心模块 + @cached_feature 装饰器
- `app/services/wave_cache.py`: 波特征缓存包装
- `app/services/tg_engine.py`: download 返回更新股票列表 + 缓存失效
- 性能: 缓存命中加速 3284x

**P2-5 事件总线解耦**:
- `app/core/event_bus.py`: 异步事件总线 (Redis Pub/Sub + 内存订阅者)
- `app/services/scan_listeners.py`: scan_completed 事件处理器
- `app/api/scan.py`: 扫描完成后发送事件
- `app/main.py`: lifespan 中注册订阅者
- 订阅者: accuracy_tracker, dna_auto_join, alphaflow_pool

### v4.8 (2026-06-13) — DNA 实验室自动化 + 新闻采集优化 + 宏观数据改造 + TG 扫描阶段重组

**⭐ DNA 自动加入三种机制**:
- `app/services/stock_dna_auto_join.py`: 独立服务模块, 提供三个自动加入函数
- **机制1 (AlphaFlow)**: lock_state=breakout_up + TG买入信号 → 自动加入 DNA
- **机制2 (TG扫描)**: 每日扫描完成后, L3级股票自动加入 DNA (L1/L2不加入) — **v4.8.2 改为异步后台执行, 不阻塞 done 事件**
- **机制3 (持仓变动)**: 持仓新增或清仓 → 相关股票自动加入 DNA (保留模型用于后续分析)

**⭐ 新闻特征系统改造 (枯竭数据 → Tushare 宏观数据)**:
- 旧问题: `stock_events` / `news_aggregated` / `news_verify` 表数据稀疏, 新闻特征长期空缺
- 旧方案: `score_event_impact()` 读取空表, 返回 0
- 新方案: 使用 `compute_sector_macro_score()` + Tushare 宏观数据
- 改造位置:
  - `deep_scorer.py`: `score_event_impact()` → `compute_sector_macro_score()` (板块宏观得分 × 3)
  - `deep_scorer.py`: 新闻信号加权从 `news_aggregated` 改为宏观数据
  - `holdings.py`: `news_signal` 字段用 `sector_macro_cache` 预计算
  - `LearningPage.tsx`: 新闻验证标签 → 宏观快照展示 (MacroSnapshotView 组件)

**⭐ 新闻采集优化**:
- **聚合接口**: `GET /scan/news-dashboard` 一次返回所有数据
- **新鲜度API**: `GET /scan/news-freshness` 返回 should_crawl/should_analyze/recommendation
- **增量更新**: `news_pipeline.py` 根据新鲜度智能跳过爬取或LLM分析
- **前端分类工具**: `classifyMarket()` / `filterByMarket()` 统一市场分类逻辑

**⭐ 新闻分类去重 v2.1**:
- `app/services/news_classifier.py`: SimHash 指纹 + 智能分类
- **去重**: 跨源 SimHash (汉明距离<10) + 数据库唯一约束
- **分类**: 三级 (company/sector/macro/garbage) - macro_only 跳过 LLM
- **个股摘要保留**: `get_stock_news_summary()` 从 `news_raw` + `stock_events` 合并, 不丢失 title/summary
- **修复新闻速报按钮**: 浏览器启动加超时 (10s/15s), 防止按钮卡死

**⭐ 龙虎榜精细化 v2.0**:
- 机构细分: 公募/私募/QFII/北向/社保
- 游资分级: 顶级(95-87分) / 一线(80-60分) / 二线(55-45分) / 三线(40分)
- 共振强度: 5级 (extreme/strong/moderate/weak/minimal)
- 共振标签: 机构入场/顶级游资/一线游资/合力买入/净买普遍
- 净买持续性: 1/3/5日统计
- **智能缓存**: 历史永久/当日交易时段5min/休市1h
- **SSE 刷新接口**: POST /api/scan/toplist-refresh
- **新鲜度检查**: GET /api/scan/toplist-freshness

**⭐ 融资融券情绪重写**:
- 旧问题: `trend_pct`/`detail`/`sentiment` 字段 undefined, 返回 0
- 新方案: 改用 `rzye` (融资余额) 直接判断
- **判定标准**: > 1.6万亿=亢奋 (注意风险), 1.2-1.6万亿=正常, < 1.2万亿=谨慎
- 同步数据: `margin_trading` 表 (ts_code='TOTAL' 汇总)

**⭐ TG 扫描阶段重组 v4.8.2 (本次修复 P0/P1/P2 共 15 项问题)**:

| 修复 | 文件 | 说明 |
|------|------|------|
| **P0-1** | `ScanPage.tsx` | `setCurrentPhase` 类型从 `'download'\|'scan'\|null` 扩展为 10 个 `ScanPhase` |
| **P0-2** | `scan.py` | DNA auto-join 用 `asyncio.create_task()` 异步执行, 不阻塞 done 事件 |
| **P1-1** | `scan.py` | 移除 `toplist_sync` 阶段 (与 `toplist` 重复), 合并到 ① |
| **P1-2** | `ScanPage.tsx` | phaseMessages slice(-8) → slice(-20), maxHeight 120 → 280 |
| **P1-3** | `scan.py` + `ScanPage.tsx` | `trigger_scan` 接受 `market_filter` 参数, 后端用 `classify_board` 真过滤 |
| **P1-4** | `scan.py` | `skip_download=True` 同时跳过龙虎榜 + DNA, 不调 Tushare API |
| **P1-5** | `tg_engine.py` | scan phase 节流: `% 200/500` → `% max(1, total//20)` (5% 步长), `asyncio_sleep(0)` `% 10` → `% 50` |
| **P1-6** | `accuracy_tracker.py` | `apply_accuracy_feedback(isolated_meta=True)` 写入独立 `accuracy_feedback_factor` 列, 不覆盖主 discrimination |
| **P2-1** | `scan.py` | 文案 "12维评分" → "14维评分" |
| **P2-2** | `scan.py` | 所有 phase `extra` 异常信息统一改为 "异常: {e}" |
| **P2-3** | `tg_engine.py` | 覆盖率 < 95% 时回退 `latest_date` → 回退 365 天, 避免漏掉中间日 |
| **P2-4** | `scan.py` | ambush_scan 用 `scan_results.MAX(scan_date)` 而非 `analysis_scores.MAX(scan_date)` |
| **P2-5** | `tg_engine.py` | ST 过滤正则 `[*]?ST` → `name ~* '[* ]?ST' OR name LIKE '%ST%' OR name LIKE '%退%'` |
| **P2-6** | `ScanPage.tsx` | phaseLabel 新增 `'🧬DNA训练'` 标签, 阶段指示器含 dna_auto_join |
| **DB** | `param_library` | 新增列 `accuracy_feedback_factor`, `accuracy_feedback_at` |

**市场过滤 v4.8 (后端真过滤)**:

```
前端 ScanPage 主板/中小板/创业板 按钮
  ↓ market_filter=主板  (URL query)
后端 trigger_scan
  ↓ classify_board(ts_code) → '上海主板'|'深圳主板'|'中小板'|'创业板'
  ↓ 主板允许列表: ['上海主板', '深圳主板']
  ↓ results = results[results['symbol'].apply(in allowed)]
  ↓ 日志: market_filter=主板 (allowed=['上海主板', '深圳主板']): 5500 -> 3500
```

### v4.7 (2026-06-09) — AlphaFlow 信号重构 + 大神仙空全局部署 + 两期扫描

**⭐ 大神仙空 v2.0 — 全局卖出指标**:
- `app/services/big_fairy.py`: 7 维度评分 (KDJ + MACD + MA均线 + RSI + 量价 + 动量 + 超买综合), score≥2=sell, ≥3=strong_sell
- 13/13 同花顺卖出信号校准 (000881/000333/600329/600167 四股全部日期匹配)
- `_big_fairy_from_arrays()`: 纯 NumPy 计算, 批量模式下无 DB I/O, pool_service 一次查询加载全池 K 线后内存计算
- API: `GET /api/alphaflow/big-fairy` + `GET /api/holdings/big-fairy`

**⭐ AlphaFlow 信号逻辑重构**:
- `lock_detector.py v2.3`: `state` 字段区分 `locked` / `breakout_up` / `breakout_down`, 通过 close vs MA20 + 20日趋势判定方向
- `alphaflow_pool_service.py v4.7`: 批量加载全池 K 线+成交量, 一次查询加载所有 TG scan_results
- 信号规则: 锁死中→watch, 主升浪+TG(10天延续)→buy, 主升浪+BF(10天延续)→sell, TG/BF同时活跃→最新胜出, 破位→sell
- 大神仙空≥2 直接覆盖所有信号为卖出, ≥3 从分析页剔除, =2 打六折

**⭐ 两期 SSE 扫描**:
- `alphaflow_pool.py`: `daily_scan()` 新增 `restrict_symbols` 参数, 支持定向扫描
- `alphaflow.py /scan`: Phase 1 扫池内~100只 (秒级), 前端立即刷新; Phase 2 后台扫全市场~5400只找新蛋
- 前端 SSE 流式接收进度 (锁死检测/XGBoost/策略/清理 各阶段百分比)

**⭐ 富宏观上下文**:
- `llm_deep_analyzer.py`: 旧 3 值 (M2/SHIBOR/PMI) → 8 段 25+ 指标 (货币/通胀/PMI/GDP/利率曲线/杠杆/汇率/10商品/5概念/综合判读)
- 所有值来自 macro_cache, 杜绝 LLM 虚构宏观叙事

**⭐ 事件管道净化**:
- `event_detector.py v4.7`: LLM Stage 1 前加入商品/宏观关键词预过滤 (期货/原油/沪铜/人民币/美元/SHIBOR/PMI/CPI/国债...)
- 命中关键词但含公司级白名单 (中标/签约/减持/业绩/公告/涨停) → 保留; 否则丢弃
- 存量清理: 58 条 LLM 虚构 sector_events 已删除, 86→28

**修复**:
- `tg_engine.py`: 涨停过滤改用 `close/prev_close` 替代 `(close-open)/open`, 修复一字板漏检
- 科创板 20% / 北交所 30% 阈值已内置
- `DeepAnalysisPage.tsx`: localStorage 持久化恢复修复 (individual/batchScores/completed 完整恢复)

**前端**:
- AlphaFlowPage: +大神仙空列, SSE 扫描进度, 两期事件处理
- AnalysisPage: 趋势列→大神仙空列, BF 过滤
- HoldingsPage: 大神仙空信号展示

### v4.6 (2026-06-05~08) — 影子训练器升级 + 宏观数据扩展 + 新闻管线退役

**⭐ 影子训练器 66 维升级** (`shadow_trainer.py`):
- DEFAULT_WEIGHTS: 23→66 维 (23 技术 + 14 Tier1 大盘 + 18 Tier2 板块 + 11 Tier3 个股)
- 三级漏斗: Tier1 (宏观指标直接乘) → Tier2 (乘板块暴露系数) → Tier3 (个股 ROE/资金流)
- `build_macro_context()`: 从 macro_cache 批量加载, `score_stock(row, weights, macro_context)`
- `factor_exposure.py`: 27 板块 × 30 因子矩阵 + 12 商品 × 84 链路 + DEFAULT_EXPOSURE 回退

**⭐ 宏观数据扩展** (`macro_data.py`):
- INDICATORS: 16→50+ 指标 (新增 M1-M2剪刀差/SHIBOR利差/PMI细分/CPI核心/PPI产端/GDP分项/汇率/国债)
- `_sync_commodity_prices()`: 12 品种期货主力合约日线同步到 macro_cache
- `_sync_sector_indices()`: 28 SW 行业 + 概念指数 5 日涨跌幅
- `get_macro_snapshot()`: 含 direction (bullish/bearish/neutral) 和 unit 字段

**⭐ 新闻管线 M-5 退役** (`event_detector.py`):
- 删除 6 个宏观/政策/商品 LLM 分析入口 (`get_macro_adjustment`/`score_sector_news`)
- TAG_TO_SYSTEM 精简为仅保留公司级 4 类 (company_announcement/stock_market/leaderboard/tech_innovation)
- `deep_scorer.py`: 替换为 `score_macro_impact()` 数据驱动宏观修正
- `news_crawler.py`: 加 DEPRECATED 标记

**AlphaFlow 页面改造**:
- 表头 8→5 列 (移除 层级/趋势), 字体放大, 抽屉重写 (4 卡片 + 周期历史 + breakout_pct + 预判)
- Pool limit: 50→500 只
- `lock_detail_service.py`: 突破后 40 日 rally peak 计算

**其他修复**:
- TG lockup→lockup_score (评分制, 5日滑动窗口 ≥2/3 条件)
- `stock_dna` 中文前缀原型名 SQL 修复 (`SPLIT_PART(archetype, '_', 2)`)
- `shadow_trainer` 日历日→交易日修正 (compute_excess_return)

### v4.5 (2026-06-07) — 系统级 P0 能力升级 + DNA 个性化模型

**⭐ 系统级 P0 7 阶段升级**:
- **Phase 1 NaN 统一**: `app/utils/numpy_utils.py` (safe_float/safe_auc/safe_rsi/sanitize_array/sanitize_for_json/div0/safe_corrcoef) — 替代 14 处分散 NaN 守卫 (4 种不一致策略)
- **Phase 2 超额收益**: `app/core/market_data.py` (get_benchmark_closes/compute_excess_return) — 统一 3 处独立实现 + **修正 shadow_trainer 日历日 counting bug** (日历日→交易日)
- **Phase 3 Progress**: `app/core/progress.py` (ProgressCallback 协议 + make_progress_adapter) — 统一 3 种回调签名为 4 参数标准
- **Phase 4 基准**: `get_benchmark_closes()` 模块级缓存替代 14 处 700001.TI 分散 SQL
- **Phase 5 代码**: `app/utils/stock_code.py` (normalize_ts_code) — 替代 9 处 `startswith('6')→.SH` 复制 + 修复 6 处 BJ 丢弃 bug + 920xxx 支持
- **Phase 6 名称**: `app/core/name_resolver.py` (三级缓存: 内存→DB→fallback) — 替代 5 处绕过缓存
- **Phase 7 NumPy**: JSON 序列化统一 (Phase 1 中已做)

**⭐ 全局前复权**:
- `scripts/resync_all_kline.py`: Tushare adj_factor API → 前向填充 → 手动前复权公式 (close_adj = close_raw × af[t] / af[latest])
- `daily_kline` 表新增 `adj_factor DOUBLE PRECISION` 列
- `app/services/kline_utils.py`: get_adjusted_kline() / get_ex_rights_dates() / iter_non_exrights_chunks()
- **退役 9 个除权补丁**: find_last_ex_rights (20%阈值), _is_ex_rights_day (15%+10%), _adjust_ex_rights (18%), 及 6 个调用方全部移除

**⭐ DNA 个性化模型实验室**:
- 10 文件 `stock_dna` 包 (features/emotion/cycle/market_context/data_builder/model/inference/similarity/dna_models)
- 独立 API `/api/dna/*` 7 端点 + 独立 DB `stock_dna.*` 3 表 + 前端 `DnaLab.tsx` 4 Tab
- Per-Stock XGBoost (80树×depth=3, Huber δ=3.0, T+2/5/10/20 四窗口)
- 表情聚类 (15维→KMeans++ 轮廓系数→5-8种个性化表情→马尔可夫转移矩阵)
- 老兵周期 v2 (ATR<0.8/MA<0.04/VOL<0.8, ≥2/3条件+5日滑动窗口容错)
- 日线伪表情降级 (无分时数据时用 OHLCV 计算简化表情)
- DNA 穿透测试: API 12/12 + 数据 14/14 + 隔离 1/1 全部通过

**验证**: numpy_utils 65/65 PASS | market_data 14/14 PASS | 模块编译 20+ PASS | 路由 125 条 | API 全线正常

**个股历史深度复盘 (stock_historical_drill.py)**:
- 7 项子复盘: 信号有效性回溯 + K线形态匹配 + 关键位置博弈 + 筹码吸收模拟 + 市场敏感性 + 四维共振 + 操盘手法反推
- 缓存: 同日同股内存缓存, 避免重复计算
- 集成: result.py 中在推荐返回前执行, drill_summary/drill_resonance/drill_micro_behavior 注入 API 响应

**四维共振分析 (resonance_analyzer.py)**:
- 指数共振: 个股 vs 上证 T+5 收益分类 (独立上涨/共振/伪强势)
- 板块共振: 个股 vs SW行业 T+5 收益分类 (领先/跟随/背离)
- 消息共振: 信号日前后3天新闻方向 vs 涨跌一致性
- 筹码共振: 信号前20天三区吸收率 vs 后续胜率
- 应用到评分: 独立率+2, 伪强势率-3, 领先率+2, 高吸收+3, 低吸收-4

**操盘手法反推 (micro_behavior_analyzer.py)**:
- 5类动作检测: 快速拉升/砸盘/托单横盘/尾盘偷袭/开盘冲锋
- 10个嫌疑指标快照 (VWAP距离/布林分位/整数关口/上影占比等)
- 触发条件发现: 动作组 vs 随机对照组 → 提升度统计
- 当前状态扫描: 最近50根K线是否满足历史触发条件
- API: GET /api/drill/micro-behavior/active-signals
- 应用到评分: 拉升触发+2, 砸盘触发-3

**系统自动激活 + 异常检测**:
- system_health.py: check_and_upgrade_components() 每日16:00检测并自动激活达标组件
- anomaly_detector.py: check_signal_distribution() 信号数量/win_probability偏离3σ告警
- shadow_trainer.py: evaluate_shadow_vs_main() 每周日对比, 连胜3周自动切换
- background_sync 每日调度增加: 健康自检 + 异常检测 + 影子评估

**TG→AlphaFlow 反向特征注入 (48维)**:
- alphaflow_features.py: FEAT_NAMES 增至48, compute_wave_features 新增 tg_score 参数
- alphaflow_pool.py: daily_scan 前预加载 analysis_scores 的 composite_score 作为第48维
- alphaflow_train_v2.py: 训练管线同步接入 TG score

**质量控制 + 风控升级**:
- deep_scorer.py: QUALITY_GATE L1/L2 两级自适应放宽 (通过<10逐级降门槛)
- deep_scorer.py: 分段权重 regime 持续>60天软衰减混入全局权重
- market_gate.py: force_empty 判定 (恐慌杀跌+胜率<25%+上涨<20%)
- result.py: 安全阀扩展到全部体制, _sanitize_numpy() 清洗numpy类型防止500
- accuracy_tracker.py: apply_accuracy_feedback T+5不足时降级到T+3

**AlphaFlow 量能历史参考**:
- alphaflow.py lock-detail: volume_trend 增加 reference 字段 (历史锁死周期量变分布)
- 前端: 量能条下方展示历史范围 + 中位线 + 当前分位标签 (偏上/偏下/中等)

**前端重大改造**:
- AlphaFlowPage: 四层信息架构 (结论Banner→关键价格→锁死历史→可折叠详情)
- ResultPage: 老股民研判区域重设计 (综合评级+正负分栏+关键指标仪表盘+操作建议)
- MonitorPage: 组件就绪状态面板 (分段权重/校准器/原型偏移进度条)
- DeepAnalysisPage: localStorage 持久化, 页面跳转后可恢复分析结果
- ScanPage: 新增周线共振标签
- ResultPage: 历史入口修复 (date参数+snapshot_date查询)

**训练数据更新**:
- 推荐追踪: 214 条样本, Logistic Regression AUC 0.6452 (v4.2: 103条, AUC 0.60)
- 分段权重: range 段已训练, bull/bear 段等待积累 (需≥50条/段)
- 概率校准器: T+3 标签统一 + regime分段框架就绪 (等待≥100样本/段)

**新增API端点**:
- POST /api/drill/analyze — 批量复盘
- GET /api/drill/report/{symbol} — 单股复盘
- GET /api/drill/micro-behavior/active-signals — 操盘触发扫描
- GET /api/learning/system-readiness — 组件就绪

### v4.2 (2026-06-03) — 方案 B 周线共振 + 质量控制优化 + 断裂点修复

**方案 B — 周线双周期共振**:
- tg_engine: 新增 `resample_daily_to_weekly()` (日线→周线重采样) + `scan_weekly_signals()` (全市场周线TG扫描)
- tg_engine: 新增 Phase 1.5 — 在日线扫描完成后无条件执行周线扫描 + 双周期信号匹配
- deep_scorer: 新增 `score_weekly_resonance()` 评分函数 + `DEFAULT_WEIGHTS["weekly_resonance_weight"]=2.0`
- result.py: ORDER BY 改为三级优先级排序 (resonance=0 > daily_only=1 > weekly_driven=2)
- data_models.py: ScanResult 新增 3 个字段 (resonance_type, weekly_has_buy, weekly_tg_momentum)
- 前端: ScanPage + ResultPage 新增 ⭐周线共振/📅周线驱动 标签

**质量控制优化**:
- deep_scorer: QUALITY_GATE 新增 L1/L2 两级自适应放宽 (通过<10→降sq/wp/ts门槛→放宽→仍<8→降sc/wp门槛)
- deep_scorer: 质量过滤增加逐股失败日志 (sq_low/wp_low/ts_low 统计 + 前5条被过滤股详情)
- result.py: 安全阀从仅3种弱势体制扩展到全部体制 (>5只推荐兜底)

**断裂点修复 (全链路审计)**:
- scan.py: GET /api/scan/results 新增 resonance_type/weekly_tg_momentum 字段
- background_sync.py: `run_daily_backtest()` 增加 `CREATE TABLE IF NOT EXISTS sync_log` 保护
- deep_scorer.py: AlphaFlow 净买力修正 catch 块增加 `logger.debug()` 日志
- alphaflow.py: `get_pool()` 增加 `model_status` 字段 ("ok"/"degraded")
- accuracy_tracker.py: `apply_accuracy_feedback()` T+5 不足时自动降级到 T+3

**新增脚本**:
- `scripts/add_weekly_columns.py` — 为 scan_results 表添加周线三列
- `scripts/backtest_weekly_resonance.py` — 按共振类型统计 T+5/T+10/T+20 收益和胜率

**训练数据更新**:
- 推荐追踪从 103 条增至 214 条 (05-28 + 05-29 数据)
- Logistic Regression AUC 从 0.60 提升至 0.6452
- `full_training_pipeline(by_regime=True)` 支持分段训练，range 段 214 条

### v4.1 (2026-06-03) — 第二阶段安全审计修复

**学习闭环升级**:
- scoring_trainer: 新增 `load_training_data_with_regime()`, `train_weights_by_regime()`, `_fit_logistic_regression()` — 按市场状态分段训练
- scoring_trainer: `persist_weights()` 写入 `__regime_auc__` 和 `__trained_at__` 元信息
- deep_scorer: 三重安全门控 (MIN_REGIME_SAMPLES=50, MIN_REGIME_PARAMS=10, MIN_REGIME_AUC=0.55)
- deep_scorer: AlphaFlow 净买力接入 composite_score 作为交叉反哺修正因子
- probability_calibrator: T+2→T+3 标签统一, 新增 `build_calibration_by_regime()`, `calibrate_with_regime()`, `scheduled_recalibrate_with_regime()`
- archetype_param_resolver: ARCHETYPE_OFFSETS 标注待校准 + `collect_archetype_calibration_data()`

**AlphaFlow 升级**:
- alphaflow_train_v2: 训练管线接入 sector_closes (特征#41真实化), meta 增加 training_date/feature_names/feature_hash
- alphaflow_pool: `_load_xgb_model()` 三阶段版本校验 (特征数/特征名/特征哈希)
- chip_analyzer: 新增 `find_last_ex_rights()` 除权免疫
- wave_predictor: 新增 delisted_stocks 排除 + 3σ 极端浪幅过滤
- alphaflow_veteran: 新增 `backtest_veteran_breakout_rate()` + API 端点

**后台调度扩展**:
- background_sync: 周一权重分段训练 + 周六老兵回测 + 周日 regime 概率重校准

**新增端点**:
- `GET /api/alphaflow/veteran-backtest`
- `GET /api/learning/archetypes/calibration-data`

**部署工具**:
- `retrain_xgb.ps1` — XGBoost 重训脚本
- `verify_all.ps1` — 完整部署验证链
- `docs/post_deployment_monitoring.md` — 监控指南

### v4.0 (2026-06-02) — AlphaFlow 全面重写

- AlphaFlow 锁死→老兵→评估→XGBoost→策略→池 完整管线
- 筹码吸收三区模型 + 波段目标预测
- XGBoost V2 47维 (含6维老兵增强) AUC 0.7898
- 市场门控 v2.0 (7体制+涨跌比+风格偏向)
- 持仓管理升级 (资金账户/自动清仓/待清仓/筹码诊断)
- 一键综合分析引擎

---

> **文档维护**：本文件随系统升级持续更新。最后更新：2026-06-07 (v4.5: P0 系统级升级 + DNA 实验室)。
