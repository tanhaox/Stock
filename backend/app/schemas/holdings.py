"""Pydantic schemas for holdings — migrated from app/api/holdings.py (Phase 11)."""
from pydantic import BaseModel, Field
from typing import Optional, List

class HoldingAdd(BaseModel):
    symbol: str
    name: str = ""
    quantity: int
    cost_price: float
    current_price: float | None = None


class HoldingImport(BaseModel):
    raw_text: str


class HoldingAnalyze(BaseModel):
    symbol: str
    raw_text: str


class CapitalOp(BaseModel):
    amount: float          # amount (yuan), positive = deposit, negative = withdraw
    note: str = ""


class CloseRequest(BaseModel):
    sell_price: float
