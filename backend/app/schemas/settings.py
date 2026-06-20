"""Pydantic schemas for settings — migrated from app/api/settings.py (Phase 11)."""
from pydantic import BaseModel, Field
from typing import Optional, List

class SettingsUpdate(BaseModel):
    tushare_token: str = ""
    deepseek_key: str = ""
    baidu_key: str = ""
    tushare_cookie: str = ""


