from datetime import date, time, datetime
from uuid import UUID
from sqlalchemy import String, Float, Integer, Date, Time as SATime, DateTime, Boolean, UniqueConstraint, Numeric, BigInteger
from sqlalchemy.dialects.postgresql import JSON, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base

class ScanResult(Base):
    __tablename__ = "scan_results"
    scan_date: Mapped[date] = mapped_column(Date, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(50))
    scan_time: Mapped[time | None] = mapped_column(SATime)
    level: Mapped[str | None] = mapped_column(String(5))
    tg_momentum: Mapped[float | None] = mapped_column(Float)
    dist_low: Mapped[float | None] = mapped_column(Float)
    j_value: Mapped[float | None] = mapped_column(Float)
    vol_ratio: Mapped[float | None] = mapped_column(Float)
    buy_strength: Mapped[float | None] = mapped_column(Float)
    close_price: Mapped[float | None] = mapped_column(Float)
    composite_score: Mapped[float | None] = mapped_column(Float)
    trigger_path: Mapped[str | None] = mapped_column(String(50))
    industry: Mapped[str | None] = mapped_column(String(50))
    params_version: Mapped[str | None] = mapped_column(String(30))
    market: Mapped[str | None] = mapped_column(String(10), nullable=True, comment="主板/创业板/中小板")
    # 方案 B：周线独立信号叠加 — 双周期共振类型
    resonance_type: Mapped[str | None] = mapped_column(String(20), nullable=True,
        comment="双周期共振类型: weekly_resonance/daily_only/weekly_driven")
    weekly_has_buy: Mapped[bool | None] = mapped_column(Boolean, nullable=True,
        comment="周线TG是否出现买入信号")
    weekly_tg_momentum: Mapped[float | None] = mapped_column(Float, nullable=True,
        comment="周线TG动量值")
    # v4.9: 分钟线防伪判定 N/M 型
    nm_verdict: Mapped[str | None] = mapped_column(String(20), nullable=True,
        comment="分钟线判定: N_dominant/N_leaning/neutral/M_leaning/M_dominant/null(未检测)")

class AnalysisScore(Base):
    __tablename__ = "analysis_scores"
    scan_date: Mapped[date] = mapped_column(Date, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(50))
    tech_score: Mapped[float | None] = mapped_column(Float)
    kline_score: Mapped[float | None] = mapped_column(Float)
    fund_score: Mapped[float | None] = mapped_column(Float)
    sector_bonus: Mapped[float | None] = mapped_column(Float)
    composite_score: Mapped[float | None] = mapped_column(Float)
    fundamental_adjustment: Mapped[float | None] = mapped_column(Float)
    market_correction: Mapped[str | None] = mapped_column(String(200))
    details: Mapped[dict | None] = mapped_column(JSON)
    archetype: Mapped[str | None] = mapped_column(String(30))
    weight_snapshot: Mapped[dict | None] = mapped_column(JSON)
    adjustment_reasons: Mapped[list | None] = mapped_column(JSON)
    dimension_scores: Mapped[dict | None] = mapped_column(JSON)
    win_probability: Mapped[float | None] = mapped_column(Float)
    downside_risk: Mapped[float | None] = mapped_column(Float)
    signal_quality: Mapped[float | None] = mapped_column(Float, nullable=True)
    trend_score: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    entry_score: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    signal_count: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    strategy_label: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # v7.0.32: MACD 指标
    macd_dif: Mapped[float | None] = mapped_column(Float, nullable=True)
    macd_dea: Mapped[float | None] = mapped_column(Float, nullable=True)
    macd_bar: Mapped[float | None] = mapped_column(Float, nullable=True)
    # v7.0.32: KDJ 指标
    kdj_k: Mapped[float | None] = mapped_column(Float, nullable=True)
    kdj_d: Mapped[float | None] = mapped_column(Float, nullable=True)
    kdj_j: Mapped[float | None] = mapped_column(Float, nullable=True)
    # v7.0.32: RSI 多周期
    rsi_6: Mapped[float | None] = mapped_column(Float, nullable=True)
    rsi_12: Mapped[float | None] = mapped_column(Float, nullable=True)
    rsi_24: Mapped[float | None] = mapped_column(Float, nullable=True)
    # v7.0.32: BOLL 布林带
    boll_upper: Mapped[float | None] = mapped_column(Float, nullable=True)
    boll_mid: Mapped[float | None] = mapped_column(Float, nullable=True)
    boll_lower: Mapped[float | None] = mapped_column(Float, nullable=True)
    boll_width: Mapped[float | None] = mapped_column(Float, nullable=True)
    boll_pos: Mapped[float | None] = mapped_column(Float, nullable=True)
    # v7.0.32: CCI 顺势指标
    cci: Mapped[float | None] = mapped_column(Float, nullable=True)
    # v7.0.32: 筹码 (从 daily_chip_perf join)
    cost_5pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_50pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_95pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    winner_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    # v7.0.32: 衍生指标
    cost_spread: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_vs_cost: Mapped[float | None] = mapped_column(Float, nullable=True)

class StockFundamentalSnapshot(Base):
    __tablename__ = "stock_fundamental_snapshot"
    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    trade_date: Mapped[date | None] = mapped_column(Date)
    roe: Mapped[float | None] = mapped_column(Float)
    revenue_yoy: Mapped[float | None] = mapped_column(Float)
    profit_yoy: Mapped[float | None] = mapped_column(Float)
    debt_to_assets: Mapped[float | None] = mapped_column(Float)
    current_ratio: Mapped[float | None] = mapped_column(Float)
    ocflow_net: Mapped[float | None] = mapped_column(Float)
    pb: Mapped[float | None] = mapped_column(Float)
    pe_ttm: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ═══════════ Exclusion 踢出机制 (v7.0.34) ═══════════

class ExclusionReason(Base):
    """软踢出条件字典表 - 定义排除原因类型."""
    __tablename__ = "exclusion_reasons"
    code: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    category: Mapped[str | None] = mapped_column(String(30))
    description: Mapped[str | None] = mapped_column(String(500))
    auto_refresh: Mapped[bool | None] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExclusionList(Base):
    """踢出名单 - 按 symbol 维度记录 (v7.0.34)."""
    __tablename__ = "exclusion_list"
    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    reason_code: Mapped[str] = mapped_column(String(30))
    added_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(String(200))


# ═══════════ Phase 6: 新增 10 张高优先级表的 ORM 模型 (v4.3) ═══════════

# ═══════════ H7 待补 ORM 模型清单 (由外部脚本/历史 SQL 创建, 非 ORM DDL) ═══════════
# bayesian_beliefs, stock_fingerprints, archetype_profiles, param_library,
# learning_dimension_registry, experience_replay, stock_events, sector_events,
# strategy_daily_score, stock_name_cache, pattern_signals, ambush_signals,
# market_status_log, min_kline (已有OR: MinKline 但列名不完整),
# signal_history, user_decisions, users, news_raw
# 这些表在 DB 重建时需通过原始 SQL 脚本创建, 不能依赖 ORM create_all.

class DailyKline(Base):
    __tablename__ = "daily_kline"
    ts_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)


class Holding(Base):
    """ORM 模型对齐数据库实际列名 (DDL: created_by, quantity, cost_price, floating_pnl, etc.).

    注意: holdings 表由历史 SQL 创建, 列名与 ORM 命名惯例不同.
    此处 Mapped 名称沿用代码中的惯例 (user_id/qty/cost/pnl),
    在 raw SQL 中使用实际 DB 列名 (created_by/quantity/cost_price/floating_pnl).
    """
    __tablename__ = "holdings"
    id: Mapped[UUID] = mapped_column(PG_UUID, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(50))          # DB: created_by
    symbol: Mapped[str] = mapped_column(String(20))
    name: Mapped[str | None] = mapped_column(String(50))
    qty: Mapped[float | None] = mapped_column(Float)           # DB: quantity
    cost: Mapped[float | None] = mapped_column(Float)           # DB: cost_price
    current_price: Mapped[float | None] = mapped_column(Float)
    market_value: Mapped[float | None] = mapped_column(Float)
    pnl: Mapped[float | None] = mapped_column(Float)           # DB: floating_pnl
    pnl_pct: Mapped[float | None] = mapped_column(Float)
    holding_days: Mapped[int | None] = mapped_column(Integer)
    pending_close: Mapped[bool | None] = mapped_column(Boolean)
    strategy: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClosedPosition(Base):
    __tablename__ = "closed_positions"
    id: Mapped[UUID] = mapped_column(PG_UUID, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(50))
    symbol: Mapped[str] = mapped_column(String(20))
    name: Mapped[str | None] = mapped_column(String(50))
    qty: Mapped[float | None] = mapped_column(Float)
    cost: Mapped[float | None] = mapped_column(Float)
    sell_price: Mapped[float | None] = mapped_column(Float)
    pnl: Mapped[float | None] = mapped_column(Float)
    pnl_pct: Mapped[float | None] = mapped_column(Float)
    holding_days: Mapped[int | None] = mapped_column(Integer)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(String(500))


class CapitalAccount(Base):
    __tablename__ = "capital_account"  # 对齐历史 DB (非 capital_accounts)
    id: Mapped[UUID] = mapped_column(PG_UUID, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(50))
    amount: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MinKline(Base):
    __tablename__ = "min_kline"
    ts_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    trade_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)


class StockDeepFeedback(Base):
    __tablename__ = "stock_deep_feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(20))
    trade_date: Mapped[date | None] = mapped_column(Date)
    suggested_score: Mapped[float | None] = mapped_column(Float)
    hidden_risks: Mapped[list | None] = mapped_column(JSON)
    catalysts: Mapped[list | None] = mapped_column(JSON)
    positive_signals: Mapped[list | None] = mapped_column(JSON)
    negative_signals: Mapped[list | None] = mapped_column(JSON)
    short_note: Mapped[str | None] = mapped_column(String(500))
    mid_note: Mapped[str | None] = mapped_column(String(500))
    support: Mapped[str | None] = mapped_column(String(50))
    resistance: Mapped[str | None] = mapped_column(String(50))
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AlphaflowPool(Base):
    __tablename__ = "alphaflow_pool"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str | None] = mapped_column(String(50))
    first_seen: Mapped[date] = mapped_column(Date)
    last_updated: Mapped[date] = mapped_column(Date)
    current_prob: Mapped[float | None] = mapped_column(Numeric(6, 4))
    prob_trend: Mapped[float | None] = mapped_column(Numeric(6, 4))
    tier: Mapped[str | None] = mapped_column(String(10))
    tier_since: Mapped[date | None] = mapped_column(Date)
    micro_score: Mapped[int | None] = mapped_column(Integer)
    days_in_pool: Mapped[int | None] = mapped_column(Integer)
    consecutive_dormant: Mapped[int | None] = mapped_column(Integer)
    strategy_group: Mapped[str | None] = mapped_column(String(30))
    strategy_label: Mapped[str | None] = mapped_column(String(30))
    veteran_detected: Mapped[bool | None] = mapped_column(Boolean)
    veteran_level: Mapped[str | None] = mapped_column(String(20))
    veteran_score: Mapped[float | None] = mapped_column(Float)
    lock_days: Mapped[int | None] = mapped_column(Integer)


class IndexDaily(Base):
    __tablename__ = "index_daily"
    ts_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    close: Mapped[float | None] = mapped_column(Float)


class ThsMember(Base):
    __tablename__ = "ths_member"
    ts_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    ths_name: Mapped[str | None] = mapped_column(String(100))
    out_date: Mapped[date | None] = mapped_column(Date)


class ToplistDaily(Base):
    __tablename__ = "toplist_daily"
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    ts_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(50))
    close: Mapped[float | None] = mapped_column(Float)
    pct_change: Mapped[float | None] = mapped_column(Float)
    l_buy: Mapped[float | None] = mapped_column(Float)
    l_sell: Mapped[float | None] = mapped_column(Float)
    l_net: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)


class RecommendationTracking(Base):
    __tablename__ = "recommendation_tracking"
    scan_date: Mapped[date] = mapped_column(Date, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    rank: Mapped[int | None] = mapped_column(Integer)
    composite_score: Mapped[float | None] = mapped_column(Float)
    close_price: Mapped[float | None] = mapped_column(Float)
    was_profitable_3d: Mapped[bool | None] = mapped_column(Boolean)
    was_profitable_5d: Mapped[bool | None] = mapped_column(Boolean)
    was_profitable_15d: Mapped[bool | None] = mapped_column(Boolean)
    return_3d: Mapped[float | None] = mapped_column(Float)
    return_5d: Mapped[float | None] = mapped_column(Float)
    return_15d: Mapped[float | None] = mapped_column(Float)
    verified_3d: Mapped[bool | None] = mapped_column(Boolean)
    verified_5d: Mapped[bool | None] = mapped_column(Boolean)
    verified_15d: Mapped[bool | None] = mapped_column(Boolean)


class GooseArchive(Base):
    __tablename__ = "goose_archive"
    ts_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    first_seen: Mapped[date | None] = mapped_column(Date)
    last_prob: Mapped[float | None] = mapped_column(Float)
    gain_from_first_lock: Mapped[float | None] = mapped_column(Float)
    first_lock_avg: Mapped[float | None] = mapped_column(Float)
    waves_completed: Mapped[int | None] = mapped_column(Integer, default=0)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AlphaflowPoolHistory(Base):
    __tablename__ = "alphaflow_pool_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(20), nullable=False)
    record_date: Mapped[date] = mapped_column(Date, nullable=False)
    xgb_prob: Mapped[float | None] = mapped_column(Float)
    micro_score: Mapped[int | None] = mapped_column(Integer, default=0)
    tier: Mapped[str | None] = mapped_column(String(10))
    __table_args__ = (UniqueConstraint("ts_code", "record_date"),)


class ArchetypeOffsetOverride(Base):
    __tablename__ = "archetype_offset_overrides"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    archetype: Mapped[str] = mapped_column(String(30), nullable=False)
    param_name: Mapped[str] = mapped_column(String(50), nullable=False)
    offset_value: Mapped[float | None] = mapped_column(Float)
    effective_date: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (UniqueConstraint("archetype", "param_name"),)


class StockTag(Base):
    """股票标签表 — 板块/风险/市值/上市时长 (v4.9 Phase 26b)."""
    __tablename__ = "stock_tags"
    ts_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(50))
    board: Mapped[str | None] = mapped_column(String(10), comment="主板/创业板/科创板/北交所")
    risk_status: Mapped[str | None] = mapped_column(String(10), comment="正常/ST/*ST/PT/退市")
    market_cap_tier: Mapped[str | None] = mapped_column(String(10), comment="大盘/中盘/小盘/微盘")
    ipo_age: Mapped[str | None] = mapped_column(String(10), comment="次新(<1年)/次新(1-3年)/成熟")
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SectorTrend(Base):
    """板块趋势预计算表 — 5/10/20日涨跌幅 + 排名 + 方向 + 生命周期 (v4.9 Phase 26c)."""
    __tablename__ = "sector_trend"
    sector_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    pct_5d: Mapped[float | None] = mapped_column(Float)
    pct_10d: Mapped[float | None] = mapped_column(Float)
    pct_20d: Mapped[float | None] = mapped_column(Float)
    rank_5d: Mapped[int | None] = mapped_column(Integer)
    rank_20d: Mapped[int | None] = mapped_column(Integer)
    direction: Mapped[str | None] = mapped_column(String(8))
    lifecycle: Mapped[str | None] = mapped_column(String(8))
    vol_ratio: Mapped[float | None] = mapped_column(Float)


class StockSectorMap(Base):
    """固化的股票→板块映射表 (v4.9 Phase 28). 多级填充: ths_member → keyword → default."""
    __tablename__ = "stock_sector_map"
    ts_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    stock_name: Mapped[str | None] = mapped_column(String(50))
    sw_code: Mapped[str | None] = mapped_column(String(20))
    sw_name: Mapped[str | None] = mapped_column(String(20))
    sse_code: Mapped[str | None] = mapped_column(String(20))
    ths_code: Mapped[str | None] = mapped_column(String(20))
    source: Mapped[str | None] = mapped_column(String(20))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SectorMinKline(Base):
    """板块分钟K线 — 指数级别 5 分钟 K 线数据 (v4.9 Phase 26d)."""
    __tablename__ = "sector_min_kline"
    sector_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    trade_time: Mapped[datetime] = mapped_column(DateTime(timezone=False), primary_key=True)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    vol: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)


# ── 索引创建 ──

async def ensure_indexes():
    """创建缺失的数据库索引 (IF NOT EXISTS)."""
    from sqlalchemy import text as _text
    from app.core.database import async_session_factory as _asf

    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_daily_kline_code_date ON daily_kline(ts_code, trade_date)",
        "CREATE INDEX IF NOT EXISTS idx_analysis_archetype ON analysis_scores(scan_date, archetype)",
        "CREATE INDEX IF NOT EXISTS idx_stock_events_date ON stock_events(event_date)",
        "CREATE INDEX IF NOT EXISTS idx_sector_events_date ON sector_events(event_date)",
        "CREATE INDEX IF NOT EXISTS idx_param_library_active ON param_library(is_shadow, is_active)",
        "CREATE INDEX IF NOT EXISTS idx_ths_member_code ON ths_member(ts_code, out_date)",
        "CREATE INDEX IF NOT EXISTS idx_toplist_date ON toplist_daily(trade_date)",
        "CREATE INDEX IF NOT EXISTS idx_min_kline_code_date ON min_kline(ts_code, trade_date)",
        "CREATE INDEX IF NOT EXISTS idx_recommendation_scan ON recommendation_tracking(scan_date, symbol)",
    ]
    # v4.9: 字段迁移
    migrations = [
        "ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS nm_verdict VARCHAR(20)",
        # v7.0.33: OBV 主力能量潮
        "ALTER TABLE analysis_scores ADD COLUMN IF NOT EXISTS obv_value DOUBLE PRECISION",
        "ALTER TABLE analysis_scores ADD COLUMN IF NOT EXISTS obv_ma20 DOUBLE PRECISION",
        # v7.0.34: exclusion 表 (踢出名单)
        """CREATE TABLE IF NOT EXISTS exclusion_reasons (
            code VARCHAR(30) PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            category VARCHAR(30),
            description VARCHAR(500),
            auto_refresh BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ
        )""",
        """CREATE TABLE IF NOT EXISTS exclusion_list (
            symbol VARCHAR(20) PRIMARY KEY,
            reason_code VARCHAR(30) NOT NULL,
            added_at TIMESTAMPTZ,
            expires_at TIMESTAMPTZ,
            note VARCHAR(200)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_exclusion_reason ON exclusion_list(reason_code)",
        "CREATE INDEX IF NOT EXISTS idx_exclusion_expires ON exclusion_list(expires_at)",
    ]
    # v7.0.34: 初始化 exclusion_reasons 字典
    init_reasons = [
        ("PE_LOSS",     "市盈(TTM)=亏损",  "FINANCIAL",  "pe_ttm < 0 的亏损股",                True),
        ("TECH_BOARD",  "科创板权限",       "PERMISSION", "688 开头, 需 50 万+2 年",           False),
        ("BJ_BOARD",    "北交所权限",       "PERMISSION", "8 开头, 需 50 万+2 年",             False),
        ("ST_NAME",     "ST/*ST/PT 股票",   "RISK",       "Tushare stock_st 实时同步",          True),
        ("INSOLVENT",   "资不抵债",         "FINANCIAL",  "总负债 > 总资产 (Tushare balancesheet_vip)", True),
    ]
    async with _asf() as s:
        for idx_sql in indexes:
            try:
                await s.execute(_text(idx_sql))
            except Exception:
                pass
        for mig_sql in migrations:
            try:
                await s.execute(_text(mig_sql))
            except Exception:
                pass
        # v7.0.34: 初始化 exclusion_reasons 字典
        for code, name, cat, desc, auto in init_reasons:
            try:
                await s.execute(_text("""
                    INSERT INTO exclusion_reasons (code, name, category, description, auto_refresh, created_at, updated_at)
                    VALUES (:c, :n, :cat, :d, :a, NOW(), NOW())
                    ON CONFLICT (code) DO NOTHING
                """), {"c": code, "n": name, "cat": cat, "d": desc, "a": auto})
            except Exception:
                pass
        await s.commit()


async def run_pending_migrations() -> list[str]:
    """执行待执行的数据库迁移 (v4.9)."""
    from sqlalchemy import text as _text
    from app.core.database import async_session_factory as _asf

    migrations = [
        ("nm_verdict", "ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS nm_verdict VARCHAR(20)"),
    ]
    executed = []
    async with _asf() as s:
        for name, sql in migrations:
            try:
                # 检查字段是否已存在
                r = await s.execute(_text(f"""
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'scan_results' AND column_name = '{name}'
                """))
                if not r.fetchone():
                    await s.execute(_text(sql))
                    executed.append(name)
            except Exception:
                pass
        await s.commit()
    return executed
