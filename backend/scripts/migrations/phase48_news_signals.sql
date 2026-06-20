-- Phase 48: news_signals 表 — 新闻→股票信号匹配结果
CREATE TABLE IF NOT EXISTS news_signals (
    id          SERIAL PRIMARY KEY,
    news_id     UUID REFERENCES news_raw(id),
    symbol      VARCHAR(20) NOT NULL,
    direction   VARCHAR(10) NOT NULL,       -- 利好/利空/中性
    magnitude   VARCHAR(10) DEFAULT '中',   -- 大/中/小
    category    VARCHAR(30),                -- commodity/macro/policy/sector
    commodity   VARCHAR(30),                -- 商品名
    reason      VARCHAR(200),               -- 匹配依据
    confidence  VARCHAR(10),                -- 确定/大概率/可能
    source_file VARCHAR(50) DEFAULT 'stock-macro-mapping',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_news_signals_news_id ON news_signals(news_id);
CREATE INDEX IF NOT EXISTS idx_news_signals_symbol ON news_signals(symbol);
CREATE INDEX IF NOT EXISTS idx_news_signals_commodity ON news_signals(commodity);
CREATE INDEX IF NOT EXISTS idx_news_signals_created ON news_signals(created_at);
CREATE INDEX IF NOT EXISTS idx_news_signals_symbol_created ON news_signals(symbol, created_at);
