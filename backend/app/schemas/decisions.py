"""Pydantic schemas for decisions — migrated from app/api/decisions.py (Phase 11)."""
from pydantic import BaseModel, Field
from typing import Optional, List

class DecisionRequest(BaseModel):
    symbol: str
    action: str  # buy / watch / pass
    decision_reason: str = ""
    source_prompt: str = ""
    feedback_id: str | None = None


