"""Pydantic schemas for drill — migrated from app/api/drill.py (Phase 11)."""
from pydantic import BaseModel, Field
from typing import Optional, List

class DrillRequest(BaseModel):
    symbols: list[str]
    force_refresh: bool = False


