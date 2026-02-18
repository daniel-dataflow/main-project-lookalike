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
    실시간 Docker 컨테이너 메트릭 조회 (Elasticsearch 불필요)
    백그라운드 수집 서비스 없이 직접 Docker에서 조회
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
    현재 실행 중인 컨테이너의 평균 통계 (실시간)
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
