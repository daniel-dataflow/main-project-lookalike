"""
데이터베이스 연결 관리
- PostgreSQL: psycopg2 커넥션 풀
- MongoDB: pymongo 클라이언트
- Redis: redis 클라이언트
- Elasticsearch: elasticsearch 클라이언트
"""
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from pymongo import MongoClient
import redis
from contextlib import contextmanager
from typing import Optional
import logging

from .config import get_settings

logger = logging.getLogger(__name__)

# ──────────────────────────────────────
# 글로벌 커넥션 객체
# ──────────────────────────────────────
_pg_pool: Optional[pool.ThreadedConnectionPool] = None
_mongo_client: Optional[MongoClient] = None
_redis_client: Optional[redis.Redis] = None


# ========================
# PostgreSQL
# ========================
def init_postgres():
    """PostgreSQL 커넥션 풀 초기화"""
    global _pg_pool
    settings = get_settings()
    try:
        _pg_pool = pool.ThreadedConnectionPool(
            minconn=settings.POSTGRES_MIN_CONN,
            maxconn=settings.POSTGRES_MAX_CONN,
            host=settings.POSTGRES_HOST,
            port=settings.POSTGRES_PORT,
            database=settings.POSTGRES_DB,
            user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD,
        )
        logger.info("✅ PostgreSQL 커넥션 풀 초기화 완료")
    except Exception as e:
        logger.error(f"❌ PostgreSQL 연결 실패: {e}")
        _pg_pool = None


@contextmanager
def get_pg_connection():
    """PostgreSQL 커넥션을 컨텍스트 매니저로 제공"""
    if _pg_pool is None:
        raise ConnectionError("PostgreSQL 커넥션 풀이 초기화되지 않았습니다")
    conn = _pg_pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pg_pool.putconn(conn)


@contextmanager
def get_pg_cursor(dict_cursor=True):
    """PostgreSQL 커서를 직접 제공하는 편의 함수"""
    with get_pg_connection() as conn:
        cursor_factory = RealDictCursor if dict_cursor else None
        cur = conn.cursor(cursor_factory=cursor_factory)
        try:
            yield cur
        finally:
            cur.close()


# ========================
# MongoDB
# ========================
def init_mongo():
    """MongoDB 클라이언트 초기화"""
    global _mongo_client
    settings = get_settings()
    try:
        _mongo_client = MongoClient(
            host=settings.MONGODB_HOST,
            port=settings.MONGODB_PORT,
            username=settings.MONGODB_USER,
            password=settings.MONGODB_PASSWORD,
            authSource="admin",
            serverSelectionTimeoutMS=settings.MONGO_TIMEOUT_MS,
        )
        # 연결 테스트
        _mongo_client.admin.command("ping")
        logger.info("✅ MongoDB 연결 완료")
    except Exception as e:
        logger.error(f"❌ MongoDB 연결 실패: {e}")
        _mongo_client = None


def get_mongo_db():
    """MongoDB 데이터베이스 객체 반환"""
    if _mongo_client is None:
        raise ConnectionError("MongoDB 클라이언트가 초기화되지 않았습니다")
    settings = get_settings()
    return _mongo_client[settings.POSTGRES_DB]  # datadb


# ========================
# Redis
# ========================
def init_redis():
    """Redis 클라이언트 초기화"""
    global _redis_client
    settings = get_settings()
    try:
        _redis_client = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD,
            db=0,
            decode_responses=True,
            socket_connect_timeout=settings.REDIS_TIMEOUT_SEC,
        )
        _redis_client.ping()
        logger.info("✅ Redis 연결 완료")
    except Exception as e:
        logger.error(f"❌ Redis 연결 실패: {e}")
        _redis_client = None


def get_redis():
    """Redis 클라이언트 반환"""
    if _redis_client is None:
        raise ConnectionError("Redis 클라이언트가 초기화되지 않았습니다")
    return _redis_client


# ========================
# 전체 초기화 / 종료
# ========================
def init_all_databases():
    """모든 데이터베이스 연결 초기화 (앱 시작 시 호출)"""
    init_postgres()
    init_mongo()
    init_redis()
    logger.info("🚀 모든 데이터베이스 초기화 완료")


def close_all_databases():
    """모든 데이터베이스 연결 종료 (앱 종료 시 호출)"""
    global _pg_pool, _mongo_client, _redis_client

    if _pg_pool:
        _pg_pool.closeall()
        logger.info("PostgreSQL 커넥션 풀 종료")

    if _mongo_client:
        _mongo_client.close()
        logger.info("MongoDB 클라이언트 종료")

    if _redis_client:
        _redis_client.close()
        logger.info("Redis 클라이언트 종료")
