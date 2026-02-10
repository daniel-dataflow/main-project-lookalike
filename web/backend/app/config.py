"""
애플리케이션 설정 - 환경변수 기반
"""
from pydantic_settings import BaseSettings
from functools import lru_cache
import os


class Settings(BaseSettings):
    """환경변수에서 설정을 로드"""

    # === 앱 ===
    APP_ENV: str = "development"
    APP_TITLE: str = "Lookalike"
    APP_VERSION: str = "1.0.0"

    # === PostgreSQL ===
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "datadb"
    POSTGRES_USER: str = "datauser"
    POSTGRES_PASSWORD: str = "DataPass2024!"

    # === MongoDB ===
    MONGODB_HOST: str = "localhost"
    MONGODB_PORT: int = 27017
    MONGODB_USER: str = "datauser"
    MONGODB_PASSWORD: str = "DataPass2024!"

    # === Redis ===
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = "DataPass2024!"

    # === Elasticsearch ===
    ELASTICSEARCH_HOST: str = "localhost"
    ELASTICSEARCH_PORT: int = 8903

    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def MONGODB_URL(self) -> str:
        return (
            f"mongodb://{self.MONGODB_USER}:{self.MONGODB_PASSWORD}"
            f"@{self.MONGODB_HOST}:{self.MONGODB_PORT}"
        )

    @property
    def REDIS_URL(self) -> str:
        return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/0"

    @property
    def ELASTICSEARCH_URL(self) -> str:
        return f"http://{self.ELASTICSEARCH_HOST}:{self.ELASTICSEARCH_PORT}"

    class Config:
        env_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".env"
        )
        env_file_encoding = "utf-8"
        extra = "ignore"  # .env에 다른 변수가 있어도 무시


@lru_cache()
def get_settings() -> Settings:
    return Settings()
