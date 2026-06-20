"""DNA 系统 ORM 模型 — stock_dna schema 下的表.

三个表:
  daily_samples — 每日训练样本
  profiles — Per-Stock DNA 档案
  predictions — DNA 预测记录
"""
from sqlalchemy import (
    Column, String, Date, Integer, Float, Boolean, TIMESTAMP,
    PrimaryKeyConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DnaDailySample(Base):
    __tablename__ = "daily_samples"
    __table_args__ = (
        PrimaryKeyConstraint("symbol", "trade_date"),
        {"schema": "stock_dna"},
    )

    symbol = Column(String(20))
    trade_date = Column(Date)
    emotion_label = Column(Integer, default=0)
    emotion_features = Column(JSONB)
    cycle_phase = Column(String(10))
    cycle_day = Column(Integer, default=0)
    lead_lag_min = Column(Float)
    independent_pct = Column(Float)
    amplify_ratio = Column(Float)
    excess_ret_t2 = Column(Float)
    excess_ret_t5 = Column(Float)
    excess_ret_t10 = Column(Float)
    excess_ret_t20 = Column(Float)
    was_verified_t2 = Column(Boolean, default=False)
    was_verified_t5 = Column(Boolean, default=False)
    was_verified_t10 = Column(Boolean, default=False)
    was_verified_t20 = Column(Boolean, default=False)
    daily_features = Column(JSONB)
    created_at = Column(TIMESTAMP, server_default=text("NOW()"))
    updated_at = Column(TIMESTAMP, server_default=text("NOW()"))


class DnaProfile(Base):
    __tablename__ = "profiles"
    __table_args__ = {"schema": "stock_dna"}

    symbol = Column(String(20), primary_key=True)
    n_emotions = Column(Integer, default=1)
    emotion_names = Column(JSONB)
    transition_matrix = Column(JSONB)
    stationary_dist = Column(JSONB)
    emotion_entropy = Column(Float)
    best_emotion = Column(Integer)
    best_emotion_ret = Column(Float)
    avg_lockup_days = Column(Float)
    std_lockup_days = Column(Float)
    cycle_cv = Column(Float)
    avg_breakout_return = Column(Float)
    avg_breakout_days = Column(Float)
    best_horizon = Column(Integer)
    best_horizon_auc = Column(Float)
    horizon_auc_json = Column(JSONB)
    top_features = Column(JSONB)
    crash_resilience = Column(Float)
    rally_capture = Column(Float)
    deception_rate = Column(Float)
    consistency = Column(Float)
    extreme_tail = Column(Float)
    training_samples = Column(Integer)
    model_path = Column(String(200))
    last_trained = Column(TIMESTAMP)
    last_dna_update = Column(TIMESTAMP)
    archetype = Column(String(30))
    similar_stocks = Column(JSONB)
    created_at = Column(TIMESTAMP, server_default=text("NOW()"))
    updated_at = Column(TIMESTAMP, server_default=text("NOW()"))


class DnaPrediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (
        PrimaryKeyConstraint("scan_date", "symbol"),
        {"schema": "stock_dna"},
    )

    scan_date = Column(Date)
    symbol = Column(String(20))
    current_emotion = Column(Integer)
    current_cycle_phase = Column(String(10))
    current_cycle_day = Column(Integer)
    pred_excess_t2 = Column(Float)
    pred_excess_t5 = Column(Float)
    pred_excess_t10 = Column(Float)
    pred_excess_t20 = Column(Float)
    pred_win_prob_t2 = Column(Float)
    pred_win_prob_t5 = Column(Float)
    pred_win_prob_t10 = Column(Float)
    pred_win_prob_t20 = Column(Float)
    best_horizon = Column(Integer)
    confidence = Column(Float)
    feature_importance = Column(JSONB)
    created_at = Column(TIMESTAMP, server_default=text("NOW()"))


async def ensure_dna_tables():
    """创建 stock_dna schema 和三张表 (如果不存在). 纯 SQL, 不依赖 ORM Base."""
    from app.core.database import async_session_factory
    from sqlalchemy import text as sa_text

    async with async_session_factory() as s:
        # 创建 schema
        await s.execute(sa_text("CREATE SCHEMA IF NOT EXISTS stock_dna"))
        await s.commit()

    async with async_session_factory() as s:
        # daily_samples
        await s.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS stock_dna.daily_samples (
                symbol VARCHAR(20) NOT NULL,
                trade_date DATE NOT NULL,
                emotion_label INTEGER DEFAULT 0,
                emotion_features JSONB,
                cycle_phase VARCHAR(10),
                cycle_day INTEGER DEFAULT 0,
                lead_lag_min DOUBLE PRECISION,
                independent_pct DOUBLE PRECISION,
                amplify_ratio DOUBLE PRECISION,
                excess_ret_t2 DOUBLE PRECISION,
                excess_ret_t5 DOUBLE PRECISION,
                excess_ret_t10 DOUBLE PRECISION,
                excess_ret_t20 DOUBLE PRECISION,
                was_verified_t2 BOOLEAN DEFAULT FALSE,
                was_verified_t5 BOOLEAN DEFAULT FALSE,
                was_verified_t10 BOOLEAN DEFAULT FALSE,
                was_verified_t20 BOOLEAN DEFAULT FALSE,
                daily_features JSONB,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (symbol, trade_date)
            )
        """))

        # profiles
        await s.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS stock_dna.profiles (
                symbol VARCHAR(20) PRIMARY KEY,
                n_emotions INTEGER DEFAULT 1,
                emotion_names JSONB,
                transition_matrix JSONB,
                stationary_dist JSONB,
                emotion_entropy DOUBLE PRECISION,
                best_emotion INTEGER,
                best_emotion_ret JSONB,
                avg_lockup_days DOUBLE PRECISION,
                std_lockup_days DOUBLE PRECISION,
                cycle_cv DOUBLE PRECISION,
                avg_breakout_return DOUBLE PRECISION,
                avg_breakout_days DOUBLE PRECISION,
                best_horizon INTEGER,
                best_horizon_auc DOUBLE PRECISION,
                horizon_auc_json JSONB,
                top_features JSONB,
                crash_resilience DOUBLE PRECISION,
                rally_capture DOUBLE PRECISION,
                deception_rate DOUBLE PRECISION,
                consistency DOUBLE PRECISION,
                extreme_tail DOUBLE PRECISION,
                training_samples INTEGER,
                model_path VARCHAR(200),
                last_trained TIMESTAMP,
                last_dna_update TIMESTAMP,
                archetype VARCHAR(30),
                similar_stocks JSONB,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """))

        # predictions
        await s.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS stock_dna.predictions (
                scan_date DATE NOT NULL,
                symbol VARCHAR(20) NOT NULL,
                current_emotion INTEGER,
                current_cycle_phase VARCHAR(10),
                current_cycle_day INTEGER,
                pred_excess_t2 DOUBLE PRECISION,
                pred_excess_t5 DOUBLE PRECISION,
                pred_excess_t10 DOUBLE PRECISION,
                pred_excess_t20 DOUBLE PRECISION,
                pred_win_prob_t2 DOUBLE PRECISION,
                pred_win_prob_t5 DOUBLE PRECISION,
                pred_win_prob_t10 DOUBLE PRECISION,
                pred_win_prob_t20 DOUBLE PRECISION,
                best_horizon INTEGER,
                confidence DOUBLE PRECISION,
                feature_importance JSONB,
                created_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (scan_date, symbol)
            )
        """))

        # 索引
        await s.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_dna_samples_sym_date ON stock_dna.daily_samples(symbol, trade_date)"
        ))
        await s.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_dna_pred_scan ON stock_dna.predictions(scan_date)"
        ))
        # Fix-1: best_emotion_ret column type migration (Float → JSONB)
        await s.execute(sa_text("""
            DO $$ BEGIN
                ALTER TABLE stock_dna.profiles ALTER COLUMN best_emotion_ret TYPE JSONB USING
                    CASE WHEN best_emotion_ret IS NULL THEN NULL
                         ELSE to_jsonb(best_emotion_ret) END;
            EXCEPTION WHEN others THEN NULL;
            END $$;
        """))
        await s.commit()

    import logging
    logging.getLogger("stock_dna").info("DNA tables created successfully")
