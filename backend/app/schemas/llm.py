"""Pydantic schemas for llm — migrated from app/api/llm_analysis.py (Phase 11)."""
from pydantic import BaseModel, Field
from typing import Optional, List

class PromptRequest(BaseModel):
    symbols: list[str]


class RetryRequest(BaseModel):
    symbol: str

