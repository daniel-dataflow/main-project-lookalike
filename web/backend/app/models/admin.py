"""
어드민 관련 Pydantic 모델
"""
from typing import Dict, List, Any
from pydantic import BaseModel


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
