-- =============================================================
-- v7.0.32 数据库迁移: analysis_scores 加 22 个技术/筹码字段
-- 目标: 给综合评分 + v2 训练 + 前端展示 提供新维度
-- 创建日期: 2026-06-19
-- =============================================================

-- 1.1 备份当前表 (防回滚, 万一迁移失败)
DROP TABLE IF EXISTS analysis_scores_backup_v7031;
CREATE TABLE analysis_scores_backup_v7031 AS SELECT * FROM analysis_scores;

-- 1.2 加字段 (22 个新字段,全部可空,不破坏现有)
ALTER TABLE analysis_scores
    -- MACD 指标
    ADD COLUMN IF NOT EXISTS macd_dif double precision,
    ADD COLUMN IF NOT EXISTS macd_dea double precision,
    ADD COLUMN IF NOT EXISTS macd_bar double precision,
    -- KDJ 指标
    ADD COLUMN IF NOT EXISTS kdj_k double precision,
    ADD COLUMN IF NOT EXISTS kdj_d double precision,
    ADD COLUMN IF NOT EXISTS kdj_j double precision,
    -- RSI 多周期
    ADD COLUMN IF NOT EXISTS rsi_6 double precision,
    ADD COLUMN IF NOT EXISTS rsi_12 double precision,
    ADD COLUMN IF NOT EXISTS rsi_24 double precision,
    -- BOLL 布林带
    ADD COLUMN IF NOT EXISTS boll_upper double precision,
    ADD COLUMN IF NOT EXISTS boll_mid double precision,
    ADD COLUMN IF NOT EXISTS boll_lower double precision,
    ADD COLUMN IF NOT EXISTS boll_width double precision,
    ADD COLUMN IF NOT EXISTS boll_pos double precision,
    -- CCI 顺势指标
    ADD COLUMN IF NOT EXISTS cci double precision,
    -- 筹码 (从 daily_chip_perf join)
    ADD COLUMN IF NOT EXISTS cost_5pct double precision,
    ADD COLUMN IF NOT EXISTS cost_50pct double precision,
    ADD COLUMN IF NOT EXISTS cost_95pct double precision,
    ADD COLUMN IF NOT EXISTS weight_avg double precision,
    ADD COLUMN IF NOT EXISTS winner_rate double precision,
    -- 衍生指标
    ADD COLUMN IF NOT EXISTS cost_spread double precision,        -- 95分位 - 5分位
    ADD COLUMN IF NOT EXISTS price_vs_cost double precision;     -- (close - weight_avg) / weight_avg * 100

-- 1.3 加索引 (查询性能, 部分索引节省空间)
CREATE INDEX IF NOT EXISTS idx_as_macd ON analysis_scores (scan_date, macd_dif) WHERE macd_dif IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_as_kdj ON analysis_scores (scan_date, kdj_j) WHERE kdj_j IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_as_chip ON analysis_scores (scan_date, cost_50pct) WHERE cost_50pct IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_as_winner ON analysis_scores (scan_date, winner_rate) WHERE winner_rate IS NOT NULL;

-- 1.4 输出验证
SELECT
    COUNT(*) AS total_rows,
    COUNT(macd_dif) AS has_macd,
    COUNT(kdj_j) AS has_kdj,
    COUNT(cost_50pct) AS has_chip,
    COUNT(weight_avg) AS has_weight
FROM analysis_scores;

-- 1.5 字段确认
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'analysis_scores'
AND (column_name LIKE 'macd_%'
     OR column_name LIKE 'kdj_%'
     OR column_name LIKE 'rsi_%'
     OR column_name LIKE 'boll_%'
     OR column_name LIKE 'cci'
     OR column_name LIKE 'cost_%'
     OR column_name IN ('weight_avg', 'winner_rate', 'price_vs_cost'))
ORDER BY column_name;
