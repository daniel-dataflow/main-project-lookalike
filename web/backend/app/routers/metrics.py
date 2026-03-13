"""
Elasticsearch에 영구 스토리지로 저장된 과거 메트릭 데이터를 조회하는 라우터 모듈.
- 대시보드에서 시계열 차트를 렌더링하기 위해, 일정 기간 누적된 리소스 사용량(CPU, Memory)을 시계열 및 통계 형태로 제공함.
"""
from fastapi import APIRouter, Query
from typing import Optional, List, Dict
from datetime import datetime, timedelta
from ..core.elasticsearch_setup import get_es_client
from ..config.logging import METRIC_INDEX_NAME

router = APIRouter(
    prefix="/api/metrics",
    tags=["metrics"]
)

es_client = get_es_client()
INDEX_NAME = METRIC_INDEX_NAME

@router.get("/stream")
async def get_metrics_stream(
    service: Optional[str] = None,
    limit: int = Query(60, le=1000)
):
    """
    Elasticsearch 검색을 통해 지정된 시스템 컴포넌트의 시간 흐름에 따른 리소스 메트릭 추이를 반환함.
    대시보드의 실시간 꺾은선 그래프(Line Chart)를 렌더링하기 위한 데이터 소스로 활용됨.

    Args:
        service (Optional[str], optional): 필터링할 대상 시스템 서비스 이름 (예: API_BE). ALL일 경우 전체 조회.
        limit (int, optional): 조회할 도큐먼트 개수 한도 (기본 60개, 약 5분 분량). 안전을 위해 최대 1000개로 제한.

    Returns:
        dict: 전체 히트 수와 시간 역순 정렬된 메트릭 배열을 포함한 JSON. 오류 시 빈 리스트 반환.
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
    최근 1시간 내에 기록된 시스템 지표들을 서비스별로 그룹화하여 평균 CPU, 메모리 점유율을 집계함.
    대시보드 상단 요약 패널 혹은 전체 인프라 건강 상태(Health Status)를 훑어보기 위해 사용.

    Returns:
        dict: 서비스 이름을 키로 가지며, 평균 CPU/Memory 및 최대 메모리(MB) 사용량이 담긴 객체.
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
