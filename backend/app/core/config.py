from pydantic_settings import BaseSettings
import pathlib

_env_path = pathlib.Path(__file__).parent.parent.parent / ".env"

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@127.0.0.1:15432/stock_data"
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-chat"
    DEEPSEEK_REASONER_MODEL: str = "deepseek-reasoner"
    DEEPSEEK_FLASH_MODEL: str = "deepseek-v4-flash"
    DEEPSEEK_PRO_MODEL: str = "deepseek-v4-pro"
    TUSHARE_TOKEN: str = ""
    TUSHARE_API_URL: str = "https://api.tushare.pro"
    BAIDU_QIANFAN_API_KEY: str = ""
    TUSHARE_COOKIE: str = ""
    SECRET_KEY: str = ""
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    DEBUG: bool = True
    API_AUTH_KEY: str = ""
    REDIS_URL: str = "redis://localhost:6379/0"
    model_config = {
        "env_file": str(_env_path) if _env_path.exists() else ".env",
        "env_file_encoding": "utf-8",
    }

settings = Settings()
if not settings.SECRET_KEY:
    import secrets; settings.SECRET_KEY = secrets.token_urlsafe(32)
