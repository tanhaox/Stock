"""Pydantic schemas for learning — migrated from app/api/learning.py (Phase 11)."""
from pydantic import BaseModel, Field
from typing import Optional, List

class UpgradeRequest(BaseModel):
    archetype: str
    strategy: str


class RollbackRequest(BaseModel):
    archetype: str
    strategy: str

