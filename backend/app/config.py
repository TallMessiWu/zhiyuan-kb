from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置。环境变量前缀 ZY_，见 .env.example。"""

    model_config = SettingsConfigDict(env_prefix="ZY_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://zhiyuan:zhiyuan_dev@localhost:5433/zhiyuan"
    llm_gateway_url: str = "http://localhost:9000/v1"
    llm_model: str = "internal-chat"
    embedding_model: str = "bge-m3"
    webhook_secret: str = "change-me"

    # 复核触发去抖窗口（小时）与按需治理阈值，见 docs/design.md §4/§7
    review_debounce_hours: int = 24
    governance_usage_days: int = 90


settings = Settings()
