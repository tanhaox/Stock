"""Pydantic schemas for analysis — migrated from app/api/analysis.py (Phase 11)."""
from pydantic import BaseModel, Field
from typing import Optional, List

class AddStockRequest(BaseModel):
    symbol: str


