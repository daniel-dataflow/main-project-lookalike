from fastapi import APIRouter, Query
from typing import Optional, List, Dict
from datetime import datetime, timedelta
from ..core.elasticsearch_setup import get_es_client

router = APIRouter(
    prefix="/api/metrics",
    tags=["metrics"]
)

es_client = get_es_client()
INDEX_NAME = "container-metrics"

@router.get("/stream")
async def get_metrics_stream(
    service: Optional[str] = None,
    limit: int = Query(60, le=1000)
):
    """
    최신 메트릭 데이터 조회
    기본값: 최근 60개 (약 5분치)
    """
    must_conditions = []
    if service and service != "ALL":
        must_conditions.append({"term": {"service": service}})

    query = {"bool": {"must": must_conditions}} if must_conditions else {"match_all": {}}
    
    body = {
        "query": query,
        "sort": [{"timestamp": {"order": "desc"}}],
        "size": limit
    }

    try:
        resp = es_client.search(index=INDEX_NAME, body=body)
        hits = resp['hits']['hits']
        metrics = [h['_source'] for h in hits]
        return {"total": resp['hits']['total']['value'], "metrics": metrics}
    except Exception as e:
        print(f"Error fetching metrics: {e}")
        return {"total": 0, "metrics": []}

@router.get("/stats")
async def get_metric_stats():
    """
    최근 1시간 동안의 평균 CPU/Memory 사용량 (서비스별)
    """
    one_hour_ago = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    
    body = {
        "query": {"range": {"timestamp": {"gte": one_hour_ago}}},
        "size": 0,
        "aggs": {
            "by_service": {
                "terms": {"field": "service", "size": 20},
                "aggs": {
                    "avg_cpu": {"avg": {"field": "cpu_percent"}},
                    "avg_mem": {"avg": {"field": "memory_percent"}},
                    "max_mem_usage": {"max": {"field": "memory_usage"}}
                }
            }
        }
    }
    
    try:
        resp = es_client.search(index=INDEX_NAME, body=body)
        aggs = resp['aggregations']['by_service']['buckets']
        
        result = {}
        for bucket in aggs:
            service = bucket['key']
            result[service] = {
                "avg_cpu": round(bucket['avg_cpu']['value'], 2),
                "avg_mem": round(bucket['avg_mem']['value'], 2),
                "max_mem_mb": round(bucket['max_mem_usage']['value'] / 1024 / 1024, 2)
            }
            
        return result
    except Exception as e:
        print(f"Error fetching metric stats: {e}")
        return {}
