"""
어드민 인프라 모니터링 API
시스템 리소스, 데이터베이스, Docker 컨테이너 상태 모니터링
"""
import logging
import asyncio
import time
import psutil
import docker
from datetime import datetime
from typing import Dict, List, Any, Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..database import get_pg_cursor, get_redis, get_mongo_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])

# ──────────────────────────────────────
# [성능 최적화] 인메모리 캐시 저장소
# ──────────────────────────────────────
_system_cache: Optional[dict] = None
_system_cache_time: float = 0
_SYSTEM_CACHE_TTL = 5  # 5초

_db_cache: Optional[dict] = None
_db_cache_time: float = 0
_DB_CACHE_TTL = 10  # 10초


# ──────────────────────────────────────
# Response Models
# ──────────────────────────────────────
class SystemStatusResponse(BaseModel):
    cpu_percent: float
    cpu_freq_current: float
    cpu_freq_min: float
    cpu_freq_max: float
    cpu_cores_physical: int
    cpu_cores_logical: int
    memory_total: int
    memory_used: int
    memory_percent: float
    disk_total: int
    disk_used: int
    disk_percent: float
    uptime_seconds: int


class DatabaseStatusResponse(BaseModel):
    postgresql: Dict[str, Any]
    redis: Dict[str, Any]
    mongodb: Dict[str, Any]


class ContainerInfo(BaseModel):
    name: str
    status: str
    cpu_percent: float
    memory_usage: int
    memory_limit: int


class DockerStatusResponse(BaseModel):
    containers: List[ContainerInfo]
    checked_at: str  # ISO8601 마지막 실측 시각


# ──────────────────────────────────────
# 시스템 상태 API
# ──────────────────────────────────────
@router.get("/system/status", response_model=SystemStatusResponse)
async def get_system_status():
    """시스템 리소스 상태 조회 (5초 캐싱)"""
    global _system_cache, _system_cache_time
    
    # [성능 최적화] 5초 캐싱: psutil 반복 호출 방지
    now = time.time()
    if _system_cache and now - _system_cache_time < _SYSTEM_CACHE_TTL:
        return _system_cache
    
    try:
        cpu_percent = psutil.cpu_percent(interval=0)
        cpu_freq = psutil.cpu_freq()
        cpu_cores_logical = psutil.cpu_count(logical=True)
        cpu_cores_physical = psutil.cpu_count(logical=False)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        boot_time = psutil.boot_time()
        uptime_seconds = int(datetime.now().timestamp() - boot_time)
        
        result = SystemStatusResponse(
            cpu_percent=cpu_percent,
            cpu_freq_current=round(cpu_freq.current, 2) if cpu_freq else 0.0,
            cpu_freq_min=round(cpu_freq.min, 2) if cpu_freq else 0.0,
            cpu_freq_max=round(cpu_freq.max, 2) if cpu_freq else 0.0,
            cpu_cores_physical=cpu_cores_physical or 0,
            cpu_cores_logical=cpu_cores_logical or 0,
            memory_total=memory.total,
            memory_used=memory.used,
            memory_percent=memory.percent,
            disk_total=disk.total,
            disk_used=disk.used,
            disk_percent=disk.percent,
            uptime_seconds=uptime_seconds
        )
        _system_cache = result
        _system_cache_time = now
        return result
    except Exception as e:
        logger.error(f"시스템 상태 조회 실패: {e}")
        raise HTTPException(status_code=500, detail="시스템 상태 조회 실패")


# ──────────────────────────────────────
# 데이터베이스 상태 API
# ──────────────────────────────────────
@router.get("/database/status", response_model=DatabaseStatusResponse)
async def get_database_status():
    """데이터베이스 상태 조회 (병렬 실행 + 10초 캐싱)"""
    global _db_cache, _db_cache_time
    
    # [성능 최적화] 10초 캐싱: DB 상태는 자주 바뀌지 않음
    now = time.time()
    if _db_cache and now - _db_cache_time < _DB_CACHE_TTL:
        return _db_cache
    
    # [성능 최적화] PG/Redis/Mongo를 asyncio.gather로 병렬 실행
    # 동기 DB 클라이언트를 threadpool executor로 래핑
    loop = asyncio.get_event_loop()
    pg_result, redis_result, mongo_result = await asyncio.gather(
        loop.run_in_executor(None, _check_postgresql),
        loop.run_in_executor(None, _check_redis),
        loop.run_in_executor(None, _check_mongodb),
    )
    
    result = DatabaseStatusResponse(
        postgresql=pg_result,
        redis=redis_result,
        mongodb=mongo_result
    )
    _db_cache = result
    _db_cache_time = now
    return result


def _check_postgresql() -> dict:
    """PostgreSQL 상태 조회 (스레드풀에서 실행)"""
    try:
        with get_pg_cursor() as cur:
            cur.execute("SELECT count(*) as cnt FROM pg_stat_activity;")
            row = cur.fetchone()
            active_connections = row['cnt'] if row else 0
            
            cur.execute("SELECT pg_database_size(current_database()) as size;")
            row = cur.fetchone()
            db_size = row['size'] if row else 0
            
            return {
                "status": "healthy",
                "active_connections": active_connections,
                "database_size_mb": round(db_size / 1024 / 1024, 2)
            }
    except Exception as e:
        logger.error(f"PostgreSQL 상태 조회 실패: {e}")
        return {"status": "error", "message": str(e)}


def _check_redis() -> dict:
    """Redis 상태 조회 (스레드풀에서 실행)"""
    try:
        redis_client = get_redis()
        info = redis_client.info()
        return {
            "status": "healthy",
            "used_memory_mb": round(info.get("used_memory", 0) / 1024 / 1024, 2),
            "total_keys": redis_client.dbsize()
        }
    except Exception as e:
        logger.error(f"Redis 상태 조회 실패: {e}")
        return {"status": "error", "message": str(e)}


def _check_mongodb() -> dict:
    """MongoDB 상태 조회 (스레드풀에서 실행)"""
    try:
        db = get_mongo_db()
        stats = db.command("dbStats")
        return {
            "status": "healthy",
            "collections": stats.get("collections", 0),
            "data_size_mb": round(stats.get("dataSize", 0) / 1024 / 1024, 2)
        }
    except Exception as e:
        logger.error(f"MongoDB 상태 조회 실패: {e}")
        return {"status": "error", "message": str(e)}


# ──────────────────────────────────────
# Docker 컨테이너 상태 API (캐싱 + 백그라운드 워밍)
# ──────────────────────────────────────
from datetime import datetime, timedelta
from typing import Optional

# 캐시 저장소
_docker_cache: Optional[Dict[str, Any]] = None
_docker_cache_time: Optional[datetime] = None
_CACHE_TTL_SECONDS = 60  # 60초 캐시
_docker_warming = False  # 백그라운드 워밍 중복 방지 플래그


async def _fetch_docker_data() -> Dict[str, Any]:
    """Docker stats 수집 (병렬) — 캐시 워밍과 직접 호출 모두에서 재사용"""
    global _docker_cache, _docker_cache_time

    try:
        client = docker.from_env()
        containers = client.containers.list(all=True)

        loop = asyncio.get_event_loop()

        async def fetch_container_info(container):
            try:
                stats = await loop.run_in_executor(
                    None, lambda: container.stats(stream=False)
                )
                cpu_delta = (
                    stats['cpu_stats']['cpu_usage']['total_usage']
                    - stats['precpu_stats']['cpu_usage']['total_usage']
                )
                system_delta = (
                    stats['cpu_stats']['system_cpu_usage']
                    - stats['precpu_stats']['system_cpu_usage']
                )
                cpu_percent = 0.0
                if system_delta > 0:
                    cpu_percent = (cpu_delta / system_delta) * 100.0
                memory_usage = stats['memory_stats'].get('usage', 0)
                memory_limit = stats['memory_stats'].get('limit', 0)
            except Exception as e:
                logger.warning(f"컨테이너 {container.name} 통계 조회 실패: {e}")
                cpu_percent, memory_usage, memory_limit = 0.0, 0, 0

            return ContainerInfo(
                name=container.name,
                status=container.status,
                cpu_percent=round(cpu_percent, 2),
                memory_usage=memory_usage,
                memory_limit=memory_limit
            )

        container_info = await asyncio.gather(
            *[fetch_container_info(c) for c in containers]
        )

        now = datetime.now()
        result = {
            "containers": list(container_info),
            "checked_at": now.isoformat()
        }
        _docker_cache = result
        _docker_cache_time = now
        return result
    except Exception as e:
        logger.error(f"Docker 컨테이너 조회 실패: {e}")
        raise


async def _warm_docker_cache():
    """[성능 최적화] 캐시 만료 전 백그라운드 갱신 → 사용자 요청에 항상 캐시 hit"""
    global _docker_warming
    if _docker_warming:
        return
    _docker_warming = True
    try:
        await _fetch_docker_data()
        logger.debug("Docker 캐시 백그라운드 워밍 완료")
    except Exception as e:
        logger.warning(f"Docker 캐시 워밍 실패 (무시): {e}")
    finally:
        _docker_warming = False


@router.get("/docker/containers", response_model=DockerStatusResponse)
async def get_docker_containers():
    """Docker 컨테이너 상태 조회 (60초 캐싱 + 백그라운드 워밍)"""
    # 캐시 확인
    if _docker_cache and _docker_cache_time:
        elapsed = (datetime.now() - _docker_cache_time).total_seconds()
        if elapsed < _CACHE_TTL_SECONDS:
            # [성능] TTL 80% 경과 시 백그라운드 워밍 시작
            if elapsed > _CACHE_TTL_SECONDS * 0.8:
                asyncio.create_task(_warm_docker_cache())
            return DockerStatusResponse(**_docker_cache)

    # 캐시 miss: 동기 수집 (최초 또는 서버 재시작 직후)
    try:
        result = await _fetch_docker_data()
        return DockerStatusResponse(**result)
    except Exception:
        raise HTTPException(status_code=500, detail="Docker 컨테이너 조회 실패")


# ──────────────────────────────────────
# [성능 최적화] 인프라 통합 대시보드 API
# system + database 상태를 단일 요청으로 반환 → HTTP 왕복 감소
# ──────────────────────────────────────
@router.get("/infra/dashboard")
async def get_infra_dashboard():
    """시스템+DB 통합 API (기존 캐시 재사용, 추가 부하 0)"""
    system_data, db_data = await asyncio.gather(
        get_system_status(),
        get_database_status(),
    )
    return {
        "system": system_data,
        "database": db_data,
    }
