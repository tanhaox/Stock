"""Pydantic schemas for feedback — migrated from app/api/feedback.py (Phase 11)."""
from pydantic import BaseModel, Field
from typing import Optional, List

class SubmitRawRequest(BaseModel):
    ts_code: str; trade_date: str; raw_response: str; source_type: str = "browser_extension"

class SubmitRequest(BaseModel):
    ts_code: str; trade_date: str; raw_response: str = ""
    business_stage: str | None = None; profit_quality: str | None = None
    recurring_profit_pct: float | None = None; suggested_score: float | None = None
    confidence_score: float | None = None; data_freshness: str | None = None
    profit_attribution: list[dict] = []; hidden_risks: list[dict] = []
    catalysts: list[dict] = []; data_corrections: list[str] = []; capability_gaps: list[str] = []
    system_score_before: float | None = None


class BatchScoreRequest(BaseModel):
    symbol_texts: dict[str, str]  # {symbol: raw_response}

