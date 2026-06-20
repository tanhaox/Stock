-- Phase 50: news_verify 表 — 新闻信号验证（命中率 + 自适应接入开关）
CREATE TABLE IF NOT EXISTS news_verify (
    commodity    VARCHAR(30),
    direction    VARCHAR(10),
    symbol       VARCHAR(20),
    total        INT DEFAULT 0,
    correct_t1   INT DEFAULT 0,
    correct_t2   INT DEFAULT 0,
    hit_rate_t2  DECIMAL(5,2) DEFAULT 0,
    avg_return   DECIMAL(6,2) DEFAULT 0,
    last_signal_date  DATE,
    is_active    BOOLEAN DEFAULT FALSE,
    updated_at   TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (commodity, direction, symbol)
);

CREATE INDEX IF NOT EXISTS idx_nv_active ON news_verify(is_active) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_nv_commodity ON news_verify(commodity);
CREATE INDEX IF NOT EXISTS idx_nv_hit_rate ON news_verify(hit_rate_t2 DESC);
