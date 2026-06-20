-- Phase 49: news_aggregated 表 — 信号聚合（密度→强度 + 低频保护）
CREATE TABLE IF NOT EXISTS news_aggregated (
    id           SERIAL PRIMARY KEY,
    date         DATE NOT NULL,
    commodity    VARCHAR(30) NOT NULL,
    direction    VARCHAR(10) NOT NULL,        -- 利好/利空
    signal_count INT DEFAULT 1,
    intensity    DECIMAL(4,3) DEFAULT 0.5,   -- 合成烈度 0-1
    stocks_json  JSONB,
    category     VARCHAR(30),
    sources      TEXT[],
    first_seen   TIME,
    last_seen    TIME,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(date, commodity, direction)
);

CREATE INDEX IF NOT EXISTS idx_na_date ON news_aggregated(date);
CREATE INDEX IF NOT EXISTS idx_na_commodity ON news_aggregated(commodity);
CREATE INDEX IF NOT EXISTS idx_na_intensity ON news_aggregated(intensity DESC);
CREATE INDEX IF NOT EXISTS idx_na_date_commodity ON news_aggregated(date, commodity);
