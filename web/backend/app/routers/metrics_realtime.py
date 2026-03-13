"""
Docker 데몬을 통해 실시간 컨테이너 리소스를 조회하는 분리형 라우터 모듈.
- Elasticsearch나 별도 수집 로직(Metric Collector)이 다운되거나 지연(Network Delay) 현상이 생길 경우에도 즉각적인 시스템 모니터링 수단을 확보하기 위해 구성됨.
"""
from fastapi import APIRouter
from typing import List, Dict
import docker
import logging

router = APIRouter(
    prefix="/api/metrics",
    tags=["metrics"]
)

logger = logging.getLogger(__name__)

@router.get("/realtime")
async def get_realtime_metrics():
    """
    백그라운드 모니터링 에이전트를 거치지 않고, Docker Engine API를 직접 호출해 가장 최신의 시스템 자원 할당량(Stats)을 연산함.
    ES 의존성을 제거해 인프라 장애 시 원인 분석(Root Cause Analysis)의 정확도를 높임.

    Returns:
        dict: 활성화된 컨테이너 총 숫자와 각 컨테이너 별 평균적인 점유율(CPU, RAM, 등) 통계 데이터의 목록. 에러 시 빈 목록 반환.
    """
    try:
        client = docker.from_env()
        containers = client.containers.list(all=False)  # 실행 중인 컨테이너만
        
        metrics = []
        for container in containers:
            try:
                stats = container.stats(stream=False)
                
                # CPU 계산
                cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - \
                           stats['precpu_stats']['cpu_usage']['total_usage']
                system_delta = stats['cpu_stats']['system_cpu_usage'] - \
                              stats['precpu_stats']['system_cpu_usage']
                cpu_percent = 0.0
                if system_delta > 0:
                    cpu_percent = (cpu_delta / system_delta) * 100.0
                
                # 메모리 계산
                memory_usage = stats['memory_stats'].get('usage', 0)
                memory_limit = stats['memory_stats'].get('limit', 1)
                memory_percent = (memory_usage / memory_limit) * 100.0 if memory_limit > 0 else 0.0
                
                metrics.append({
                    "container": container.name,
                    "service": container.name.split('-')[0] if '-' in container.name else container.name,
                    "cpu_percent": round(cpu_percent, 2),
                    "memory_usage": memory_usage,
                    "memory_percent": round(memory_percent, 2),
                    "status": container.status
                })
            except Exception as e:
                logger.warning(f"컨테이너 {container.name} 메트릭 조회 실패: {e}")
                continue
        
        return {"total": len(metrics), "metrics": metrics}
    
    except Exception as e:
        logger.error(f"Docker 메트릭 조회 실패: {e}")
        return {"total": 0, "metrics": [], "error": str(e)}

@router.get("/stats")
async def get_metric_stats():
    """
    Docker 엔진에서 직접 산출한 시스템 스냅샷 데이터를 컨테이너를 넘어 전체 서비스 단위(App, DB 등)로 통합/평균 내어 제공함.
    클러스터 수준에서의 성능 병목 구간을 신속하게 파악할 목적으로 사용.

    Returns:
        dict: 컨테이너명 접두사(서비스)를 키로 가지고 평균 CPU/Mem 값을 지니는 집계 객체.
    """
    try:
        realtime_data = await get_realtime_metrics()
        metrics = realtime_data.get("metrics", [])
        
        if not metrics:
            return {}
        
        # 서비스별 그룹화
        service_stats = {}
        for m in metrics:
            svc = m["service"]
            if svc not in service_stats:
                service_stats[svc] = {
                    "cpu_values": [],
                    "mem_values": [],
                    "mem_mb_values": []
                }
            service_stats[svc]["cpu_values"].append(m["cpu_percent"])
            service_stats[svc]["mem_values"].append(m["memory_percent"])
            service_stats[svc]["mem_mb_values"].append(m["memory_usage"] / 1024 / 1024)
        
        # 평균 계산
        result = {}
        for svc, data in service_stats.items():
            result[svc] = {
                "avg_cpu": sum(data["cpu_values"]) / len(data["cpu_values"]),
                "avg_mem": sum(data["mem_values"]) / len(data["mem_values"]),
                "max_mem_mb": max(data["mem_mb_values"])
            }
        
        return result
    
    except Exception as e:
        logger.error(f"메트릭 통계 조회 실패: {e}")
        return {}
