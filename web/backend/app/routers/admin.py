"""
어드민 대시보드에서 시스템, 데이터베이스, 도커 컨테이너의 실시간 상태를 모니터링하기 위한 통합 라우터 모듈.
- 잦은 새로고침으로 인한 대상 서버(PostgreSQL, Redis, Docker 데몬)의 부하를 방지하고자 엔드포인트 단에서 인메모리 캐싱 및 병렬 조회를 강제함.
"""
import logging
import asyncio
import time
import psutil
import docker
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from fastapi import APIRouter, HTTPException, Request

from ..config.admin import SYSTEM_CACHE_TTL, DB_CACHE_TTL, INFRA_CACHE_TTL
from ..database import get_pg_cursor, get_redis, get_mongo_db
from ..models.admin import (
    SystemStatusResponse,
    DatabaseStatusResponse,
    ContainerInfo,
    DockerStatusResponse
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])

# ──────────────────────────────────────
# [성능 최적화] 인메모리 캐시 저장소
# ──────────────────────────────────────
_system_cache: Optional[dict] = None
_system_cache_time: float = 0
_SYSTEM_CACHE_TTL = SYSTEM_CACHE_TTL

_db_cache: Optional[dict] = None
_db_cache_time: float = 0
_DB_CACHE_TTL = DB_CACHE_TTL



# ──────────────────────────────────────
# 시스템 상태 API
# ──────────────────────────────────────
@router.get("/system/status", response_model=SystemStatusResponse)
async def get_system_status():
    """
    서버 OS 레벨의 자원 점유 상태(CPU, 메모리, 디스크)를 반환함.
    잦은 psutil I/O 호출을 막기 위해 5초 단위 인메모리 캐시를 적용.

    Returns:
        SystemStatusResponse: 시스템 리소스 할당 및 점유율을 담은 Pydantic 응답 객체.
    """
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
    """
    운영 중인 주요 스토리지 리소스(PostgreSQL, Redis, MongoDB)의 헬스체크 및 논리적 용량을 반환함.
    다수 DB 조회의 병목을 풀기 위해 asyncio.gather 기반 병렬 실행 및 10초단위 캐시를 사용함.

    Returns:
        DatabaseStatusResponse: 각 DB 솔루션별 커넥션/저장공간 수치를 담은 Pydantic 응답 객체.
    """
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
    """
    PostgreSQL 통계 뷰를 직접 조회하여 커넥션 활성 개수와 DB 물리 사이즈를 확보함.

    Returns:
        dict: DB 헬스 상태, 활성 커넥션 수 및 용량(MB) 매핑 정보. 오류 시 에러 사유 반환.
    """
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
    """
    Redis 엔진의 in-memory 통계 지표(INFO 명령)를 파싱하여 주요 메모리 사용량과 보관된 키 총량을 산출함.

    Returns:
        dict: Redis 헬스 상태, 메모리 점유(MB) 및 키 보관 수. 오류 시 에러 사유 반환.
    """
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
    """
    MongoDB 클러스터의 dbStats 명령을 수행해 누적 컬렉션 수 및 저장 데이터의 논리 사이즈를 조회함.

    Returns:
        dict: MongoDB 헬스 상태, 컬렉션 빈도 및 데이터 용량(MB). 오류 시 에러 사유 반환.
    """
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


# 캐시 저장소
_docker_cache: Optional[Dict[str, Any]] = None
_docker_cache_time: Optional[datetime] = None
_CACHE_TTL_SECONDS = INFRA_CACHE_TTL  # 캐시 설정 사용
_docker_warming = False  # 백그라운드 워밍 중복 방지 플래그


async def _fetch_docker_data() -> Dict[str, Any]:
    """
    설치된 Docker Engine의 HTTP API를 경유하여 기동 중인 모든 컨테이너 목록과 각각의 실시간 Stats(CPU 델타 연산, Memory) 뷰를 산출함.
    수집 시간이 오래 걸릴 수 있으므로 백그라운드 캐시 워밍 태스크와 공용으로 사용됨.

    Returns:
        Dict[str, Any]: 컨테이너 객체 리스트 및 수집 기준 시점을 포함한 딕셔너리.
    """
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
    """
    사용자가 데이터를 요청하기 전, 캐시 TTL 만료가 가까워지면 백그라운드 태스크에서 선제적으로 도커 상태를 조회/갱신해둠.
    대시보드 접속 시 체감되는 API 레이턴시를 0(Cache Hit)으로 수렴시키려는 목적.
    """
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
    """
    클라이언트에게 정제된 컨테이너 지표를 반환함. 
    기존 캐시가 신선할 때는 즉시 내려주고, 만료 임박 시 백그라운드 업데이트 트리거를 당기며 동작함.

    Returns:
        DockerStatusResponse: 최신 도커 정보 및 수집 시간 데이터를 래핑한 객체.
    """
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
    """
    별도 컴포넌트로 나뉘어져 있던 System API와 Database API의 결괏값을 하나로 묶어 제공.
    초기 로딩 시 프론트엔드가 N번 통신하는 브라우저 HTTP Connection 병목을 회피하고 한 번에 화면을 그릴 수 있게 지원.

    Returns:
        dict: 시스템 캐시 데이터와 DB 캐시 데이터가 통합된 JSON 페이로드.
    """
    system_data, db_data = await asyncio.gather(
        get_system_status(),
        get_database_status(),
    )
    return {
        "system": system_data,
        "database": db_data,
    }
