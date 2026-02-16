"""
어드민 인프라 모니터링 API
시스템 리소스, 데이터베이스, Docker 컨테이너 상태 모니터링
"""
import logging
import psutil
import docker
from datetime import datetime
from typing import Dict, List, Any
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..database import get_pg_cursor, get_redis, get_mongo_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


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


# ──────────────────────────────────────
# 시스템 상태 API
# ──────────────────────────────────────
@router.get("/system/status", response_model=SystemStatusResponse)
async def get_system_status():
    """시스템 리소스 상태 조회"""
    try:
        # CPU 사용률
        cpu_percent = psutil.cpu_percent(interval=1)
        
        # CPU 주파수 및 코어 정보
        cpu_freq = psutil.cpu_freq()
        cpu_cores_logical = psutil.cpu_count(logical=True)
        cpu_cores_physical = psutil.cpu_count(logical=False)

        # 메모리 정보
        memory = psutil.virtual_memory()
        
        # 디스크 정보
        disk = psutil.disk_usage('/')
        
        # 시스템 업타임
        boot_time = psutil.boot_time()
        uptime_seconds = int(datetime.now().timestamp() - boot_time)
        
        return SystemStatusResponse(
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
    except Exception as e:
        logger.error(f"시스템 상태 조회 실패: {e}")
        raise HTTPException(status_code=500, detail="시스템 상태 조회 실패")


# ──────────────────────────────────────
# 데이터베이스 상태 API
# ──────────────────────────────────────
@router.get("/database/status", response_model=DatabaseStatusResponse)
async def get_database_status():
    """데이터베이스 상태 조회"""
    result = {
        "postgresql": {"status": "error", "message": "연결 실패"},
        "redis": {"status": "error", "message": "연결 실패"},
        "mongodb": {"status": "error", "message": "연결 실패"}
    }
    
    # PostgreSQL 상태
    try:
        with get_pg_cursor() as cur:
            # 활성 연결 수
            cur.execute("SELECT count(*) as cnt FROM pg_stat_activity;")
            row = cur.fetchone()
            active_connections = row['cnt'] if row else 0
            
            # 데이터베이스 크기
            cur.execute("SELECT pg_database_size(current_database()) as size;")
            row = cur.fetchone()
            db_size = row['size'] if row else 0
            
            result["postgresql"] = {
                "status": "healthy",
                "active_connections": active_connections,
                "database_size_mb": round(db_size / 1024 / 1024, 2)
            }
    except Exception as e:
        logger.error(f"PostgreSQL 상태 조회 실패: {e}")
        result["postgresql"]["message"] = str(e)
    
    # Redis 상태
    try:
        redis_client = get_redis()
        info = redis_client.info()
        
        result["redis"] = {
            "status": "healthy",
            "used_memory_mb": round(info.get("used_memory", 0) / 1024 / 1024, 2),
            "total_keys": redis_client.dbsize()
        }
    except Exception as e:
        logger.error(f"Redis 상태 조회 실패: {e}")
        result["redis"]["message"] = str(e)
    
    # MongoDB 상태
    try:
        db = get_mongo_db()
        stats = db.command("dbStats")
        
        result["mongodb"] = {
            "status": "healthy",
            "collections": stats.get("collections", 0),
            "data_size_mb": round(stats.get("dataSize", 0) / 1024 / 1024, 2)
        }
    except Exception as e:
        logger.error(f"MongoDB 상태 조회 실패: {e}")
        result["mongodb"]["message"] = str(e)
    
    return DatabaseStatusResponse(**result)


# ──────────────────────────────────────
# Docker 컨테이너 상태 API
# ──────────────────────────────────────
@router.get("/docker/containers", response_model=DockerStatusResponse)
async def get_docker_containers():
    """Docker 컨테이너 상태 조회"""
    try:
        # Docker 환경 변수 및 기본 설정을 통해 연결 (유연한 연결 지원)
        client = docker.from_env()
        containers = client.containers.list(all=True)
        
        container_info = []
        for container in containers:
            try:
                stats = container.stats(stream=False)
                
                # CPU 사용률 계산
                cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - \
                           stats['precpu_stats']['cpu_usage']['total_usage']
                system_delta = stats['cpu_stats']['system_cpu_usage'] - \
                              stats['precpu_stats']['system_cpu_usage']
                cpu_percent = 0.0
                if system_delta > 0:
                    cpu_percent = (cpu_delta / system_delta) * 100.0
                
                # 메모리 사용량
                memory_usage = stats['memory_stats'].get('usage', 0)
                memory_limit = stats['memory_stats'].get('limit', 0)
                
                container_info.append(ContainerInfo(
                    name=container.name,
                    status=container.status,
                    cpu_percent=round(cpu_percent, 2),
                    memory_usage=memory_usage,
                    memory_limit=memory_limit
                ))
            except Exception as e:
                logger.warning(f"컨테이너 {container.name} 통계 조회 실패: {e}")
                container_info.append(ContainerInfo(
                    name=container.name,
                    status=container.status,
                    cpu_percent=0.0,
                    memory_usage=0,
                    memory_limit=0
                ))
        
        return DockerStatusResponse(containers=container_info)
    except Exception as e:
        logger.error(f"Docker 컨테이너 조회 실패: {e}")
        raise HTTPException(status_code=500, detail="Docker 컨테이너 조회 실패")
