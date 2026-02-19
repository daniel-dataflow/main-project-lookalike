"""
애플리케이션 설정 - 환경변수 기반
"""
from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional
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
    POSTGRES_PASSWORD: str = ""  # Set in .env

    # === MongoDB ===
    MONGODB_HOST: str = "localhost"
    MONGODB_PORT: int = 27017
    MONGODB_USER: str = "datauser"
    MONGODB_PASSWORD: str = ""  # Set in .env

    # === Redis ===
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""  # Set in .env

    # === Elasticsearch ===
    ELASTICSEARCH_HOST: str = "localhost"
    ELASTICSEARCH_PORT: int = 8903

    # === OAuth2 소셜 로그인 ===
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    NAVER_CLIENT_ID: str = ""
    NAVER_CLIENT_SECRET: str = ""
    KAKAO_CLIENT_ID: str = ""
    KAKAO_CLIENT_SECRET: str = ""
    
    # 리다이렉트 URI (환경변수 설정 제거 - 요청 호스트 기반 동적 생성)
    # OAUTH_REDIRECT_URI: Optional[str] = None

    # === 세션 ===
<<<<<<< HEAD
    SESSION_SECRET_KEY: str = "lookalike-session-secret-change-in-production-2024"
    SESSION_EXPIRE_HOURS: int = 24

    # === 관리자 ===
    ADMIN_PASSWORD: str = "admin1234!"
=======
    SESSION_SECRET_KEY: str = "change-this-in-production"  # Set in .env
    SESSION_EXPIRE_HOURS: int = 24

    # === 관리자 ===
    ADMIN_USERNAME: str = ""  # Set in .env
    ADMIN_PASSWORD: str = ""  # Set in .env
>>>>>>> feature/web-mainpage

    # === FastAPI ===
    FASTAPI_PORT: int = 8900

<<<<<<< HEAD
=======
    # === Hadoop / HDFS ===
    HADOOP_NAMENODE_HOST: str = "namenode"
    HDFS_NAMENODE_PORT: int = 9000
    HDFS_WEBHDFS_PORT: int = 9870

    # === 이미지 업로드 ===
    MAX_UPLOAD_SIZE_MB: int = 10
    THUMBNAIL_SIZE: int = 150
    THUMBNAIL_QUALITY: int = 85
    USE_MOCK_ML: bool = True

>>>>>>> feature/web-mainpage
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

    # OAUTH_REDIRECT_BASE 프로퍼티 제거 (동적 생성을 위해 auth.py에서 처리)

    def is_oauth_configured(self, provider: str) -> bool:
        """지정된 OAuth 제공자의 클라이언트 키가 설정되어 있는지 확인"""
        if provider == "google":
            return bool(self.GOOGLE_CLIENT_ID and self.GOOGLE_CLIENT_SECRET)
        elif provider == "naver":
            return bool(self.NAVER_CLIENT_ID and self.NAVER_CLIENT_SECRET)
        elif provider == "kakao":
            return bool(self.KAKAO_CLIENT_ID and self.KAKAO_CLIENT_SECRET)
        return False

    class Config:
        env_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".env"
        )
        env_file_encoding = "utf-8"
        extra = "ignore"  # .env에 다른 변수가 있어도 무시


@lru_cache()
def get_settings() -> Settings:
    return Settings()
