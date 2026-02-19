
from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List, Dict
from datetime import datetime, timedelta
import time as _time
from ..core.elasticsearch_setup import get_es_client
from kafka import KafkaConsumer
from kafka.errors import KafkaError

router = APIRouter(
    prefix="/api/logs",
    tags=["logs"],
    responses={404: {"description": "Not found"}},
)

es_client = get_es_client()
INDEX_NAME = "container-logs"

# ──────────────────────────────────────
# [성능] 대시보드 통합 캐시 (30초)
# ──────────────────────────────────────
_dashboard_cache = None
_dashboard_cache_time: float = 0
_DASHBOARD_CACHE_TTL = 30  # 30초

@router.get("/dashboard")
async def get_log_dashboard():
    """
    [성능 최적화] 대시보드 초기 로딩용 통합 API
    - ES msearch로 stats/trend/top-errors/service-health를 1회 왕복으로 처리
    - 30초 캐싱 적용
    - stream(로그 목록)은 필터 의존성 때문에 별도 호출
    """
    global _dashboard_cache, _dashboard_cache_time

    now = _time.time()
    if _dashboard_cache and now - _dashboard_cache_time < _DASHBOARD_CACHE_TTL:
        return _dashboard_cache

    one_hour_ago = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    twenty_four_hours_ago = (datetime.utcnow() - timedelta(hours=24)).isoformat()

    # ES msearch: 단일 왕복으로 4개 쿼리 실행
    searches = [
        # 1. stats (1시간 레벨별 집계)
        {"index": INDEX_NAME},
        {
            "query": {"range": {"timestamp": {"gte": one_hour_ago}}},
            "size": 0,
            "aggs": {
                "by_level": {"terms": {"field": "level", "size": 10}},
                "by_service": {"terms": {"field": "service", "size": 20}}
            }
        },
        # 2. trend (24시간 히스토그램)
        {"index": INDEX_NAME},
        {
            "query": {"range": {"timestamp": {"gte": twenty_four_hours_ago}}},
            "size": 0,
            "aggs": {
                "over_time": {
                    "date_histogram": {"field": "timestamp", "fixed_interval": "1h"},
                    "aggs": {"by_level": {"terms": {"field": "level", "size": 10}}}
                }
            }
        },
        # 3. top-errors (24시간 에러 메시지 Top5)
        {"index": INDEX_NAME},
        {
            "query": {
                "bool": {
                    "must": [
                        {"range": {"timestamp": {"gte": twenty_four_hours_ago}}},
                        {"bool": {"should": [
                            {"term": {"level": "ERROR"}},
                            {"term": {"level": "CRITICAL"}}
                        ], "minimum_should_match": 1}}
                    ]
                }
            },
            "size": 0,
            "aggs": {
                "by_message": {
                    "terms": {"field": "message.keyword", "size": 5, "order": {"_count": "desc"}},
                    "aggs": {
                        "by_service": {"terms": {"field": "service", "size": 5}},
                        "latest": {
                            "top_hits": {"size": 1, "sort": [{"timestamp": {"order": "desc"}}],
                                         "_source": ["timestamp", "container"]}
                        }
                    }
                }
            }
        },
        # 4. service-health (1시간 서비스별 레벨)
        {"index": INDEX_NAME},
        {
            "query": {"range": {"timestamp": {"gte": one_hour_ago}}},
            "size": 0,
            "aggs": {
                "by_service": {
                    "terms": {"field": "service", "size": 20},
                    "aggs": {"by_level": {"terms": {"field": "level", "size": 10}}}
                }
            }
        },
    ]

    try:
        resp = es_client.msearch(body=searches)
        responses = resp["responses"]

        # --- stats 파싱 ---
        stats_aggs = responses[0].get("aggregations", {})
        by_level = {b["key"]: b["doc_count"] for b in stats_aggs.get("by_level", {}).get("buckets", [])}
        by_service = {b["key"]: b["doc_count"] for b in stats_aggs.get("by_service", {}).get("buckets", [])}

        # --- trend 파싱 ---
        trend_buckets = responses[1].get("aggregations", {}).get("over_time", {}).get("buckets", [])
        trend = []
        for bucket in trend_buckets:
            lc = {b["key"]: b["doc_count"] for b in bucket["by_level"]["buckets"]}
            trend.append({
                "time": bucket["key_as_string"],
                "ERROR": lc.get("ERROR", 0) + lc.get("CRITICAL", 0),
                "WARN": lc.get("WARN", 0),
                "INFO": lc.get("INFO", 0),
            })

        # --- top-errors 파싱 ---
        top_err_buckets = responses[2].get("aggregations", {}).get("by_message", {}).get("buckets", [])
        top_errors = []
        for bucket in top_err_buckets:
            services = [b["key"] for b in bucket["by_service"]["buckets"]]
            latest = bucket["latest"]["hits"]["hits"]
            latest_src = latest[0]["_source"] if latest else {}
            top_errors.append({
                "message": bucket["key"],
                "count": bucket["doc_count"],
                "services": services,
                "last_seen": latest_src.get("timestamp", ""),
                "container": latest_src.get("container", ""),
            })

        # --- service-health 파싱 ---
        svc_buckets = responses[3].get("aggregations", {}).get("by_service", {}).get("buckets", [])
        services_health = []
        for bucket in svc_buckets:
            lc = {b["key"]: b["doc_count"] for b in bucket["by_level"]["buckets"]}
            total = bucket["doc_count"]
            errors = lc.get("ERROR", 0) + lc.get("CRITICAL", 0)
            warns = lc.get("WARN", 0)
            error_rate = round((errors / total) * 100, 2) if total > 0 else 0
            if error_rate > 10 or errors > 20:
                status = "critical"
            elif error_rate > 3 or warns > 10:
                status = "warning"
            else:
                status = "healthy"
            services_health.append({
                "service": bucket["key"], "total": total,
                "errors": errors, "warns": warns,
                "error_rate": error_rate, "status": status
            })

        result = {
            "stats": {"by_level": by_level, "by_service": by_service},
            "trend": trend,
            "top_errors": top_errors,
            "service_health": services_health,
            "generated_at": datetime.utcnow().isoformat(),
        }
        _dashboard_cache = result
        _dashboard_cache_time = now
        return result

    except Exception as e:
        print(f"Dashboard msearch 실패: {e}")
        return {
            "stats": {"by_level": {}, "by_service": {}},
            "trend": [],
            "top_errors": [],
            "service_health": [],
            "generated_at": datetime.utcnow().isoformat(),
        }


@router.get("/pipeline-status")
async def get_pipeline_status():
    """파이프라인 상태 확인 (캐시 30초 적용)"""
    from ..services.log_collector import LogCollector
    
    # [성능 최적화] 30초 캐싱: Kafka 연결 타임아웃(최대 1초)을 반복 호출마다 지불하지 않도록
    import time
    now = time.time()
    if (hasattr(get_pipeline_status, '_cache')
            and now - get_pipeline_status._cache_time < 30):
        return get_pipeline_status._cache
    
    kafka_ok = False
    direct_ok = False
    
    # 1. Kafka Check (Real connection)
    # [성능 최적화] request_timeout_ms: 3000 → 1000 ms 단축
    try:
        from kafka import KafkaAdminClient
        admin = KafkaAdminClient(
            bootstrap_servers="kafka:9092",
            request_timeout_ms=1000
        )
        admin.close()
        kafka_ok = True
    except Exception:
        pass
    
    # 2. Elasticsearch Status
    es_status = "inactive"
    es_doc_count = 0
    try:
        if es_client.ping():
            es_status = "active"
            count_res = es_client.count(index=INDEX_NAME)
            es_doc_count = count_res['count']
            
            # Get index stats for storage size
            stats = es_client.indices.stats(index=INDEX_NAME)
            store_size = stats['_all']['primaries']['store']['size_in_bytes']
    except:
        store_size = 0
        pass

    # 3. Direct Collector Check
    # ... (rest same)
    
    active_pipeline = "kafka" if kafka_ok else "direct"
    
    result = {
        "kafka": {
            "status": "active" if kafka_ok else "inactive"
        },
        "direct": {
            "status": "active" if not kafka_ok else "standby" 
        },
        "elasticsearch": {
            "status": es_status,
            "total_docs": es_doc_count,
            "store_size": store_size
        },
        "active_pipeline": active_pipeline
    }
    # [성능 최적화] 결과를 함수 속성에 30초 캐싱
    get_pipeline_status._cache = result
    get_pipeline_status._cache_time = now
    return result

@router.get("/stream")
async def get_logs_stream(
    service: Optional[str] = None,
    level: Optional[str] = None,
    keyword: Optional[str] = None,
    size: int = Query(100, le=500)
):
    """
    실시간 로그 스트림 조회
    - service: 서비스명 필터 (예: airflow, spark)
    - level: 로그 레벨 필터 (예: ERROR, WARN)
    - keyword: 메시지 키워드 검색
    - size: 반환 개수 (기본 100, 최대 500)
    """
    must_conditions = []
    
    if service and service != "ALL":
        must_conditions.append({"term": {"service": service}})
    
    if level and level != "ALL":
        must_conditions.append({"term": {"level": level}})
        
    if keyword:
        must_conditions.append({"match": {"message": keyword}})

    query = {
        "bool": {
            "must": must_conditions
        }
    } if must_conditions else {"match_all": {}}

    body = {
        "query": query,
        "sort": [{"timestamp": {"order": "desc"}}],
        "size": size
    }

    try:
        response = es_client.search(index=INDEX_NAME, body=body)
        hits = response['hits']['hits']
        
        logs = [hit['_source'] for hit in hits]
        total = response['hits']['total']['value']
        
        return {
            "total": total,
            "logs": logs
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Elasticsearch 조회 실패: {str(e)}")

@router.get("/stats")
async def get_log_stats():
    """
    최근 1시간 기준 로그 통계 조회
    - 서비스별 로그 건수
    - 레벨별 로그 건수
    """
    # 최근 1시간 범위 설정
    one_hour_ago = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    
    body = {
        "query": {
            "range": {
                "timestamp": {
                    "gte": one_hour_ago
                }
            }
        },
        "size": 0,  # 문서 내용은 필요 없음, 집계만 수행
        "aggs": {
            "by_service": {
                "terms": {"field": "service", "size": 20}
            },
            "by_level": {
                "terms": {"field": "level", "size": 10}
            }
        }
    }

    try:
        response = es_client.search(index=INDEX_NAME, body=body)
        aggregations = response['aggregations']
        
        by_service = {bucket['key']: bucket['doc_count'] for bucket in aggregations['by_service']['buckets']}
        by_level = {bucket['key']: bucket['doc_count'] for bucket in aggregations['by_level']['buckets']}
        
        return {
            "by_service": by_service,
            "by_level": by_level,
            "last_updated": datetime.utcnow().isoformat()
        }
    except Exception as e:
        # 인덱스가 아직 없는 경우 등 예외 처리
        print(f"Stats 조회 실패: {e}")
        return {
            "by_service": {},
            "by_level": {},
            "last_updated": datetime.utcnow().isoformat()
        }

@router.get("/errors")
async def get_recent_errors(size: int = Query(50, le=100)):
    """
    최근 에러 로그 조회 (ERROR, CRITICAL)
    """
    body = {
        "query": {
            "bool": {
                "should": [
                    {"term": {"level": "ERROR"}},
                    {"term": {"level": "CRITICAL"}}
                ],
                "minimum_should_match": 1
            }
        },
        "sort": [{"timestamp": {"order": "desc"}}],
        "size": size
    }

    try:
        response = es_client.search(index=INDEX_NAME, body=body)
        hits = response['hits']['hits']
        
        logs = [hit['_source'] for hit in hits]
        total = response['hits']['total']['value']
        
        return {
            "total": total,
            "logs": logs
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"에러 로그 조회 실패: {str(e)}")


@router.get("/trend")
async def get_log_trend():
    """
    최근 24시간 시간대별 로그 레벨 추이
    - 1시간 단위로 ERROR, WARN, INFO 건수를 반환
    """
    twenty_four_hours_ago = (datetime.utcnow() - timedelta(hours=24)).isoformat()

    body = {
        "query": {"range": {"timestamp": {"gte": twenty_four_hours_ago}}},
        "size": 0,
        "aggs": {
            "over_time": {
                "date_histogram": {
                    "field": "timestamp",
                    "fixed_interval": "1h"
                },
                "aggs": {
                    "by_level": {
                        "terms": {"field": "level", "size": 10}
                    }
                }
            }
        }
    }

    try:
        resp = es_client.search(index=INDEX_NAME, body=body)
        buckets = resp['aggregations']['over_time']['buckets']

        trend = []
        for bucket in buckets:
            level_counts = {b['key']: b['doc_count'] for b in bucket['by_level']['buckets']}
            trend.append({
                "time": bucket['key_as_string'],
                "ERROR": level_counts.get("ERROR", 0) + level_counts.get("CRITICAL", 0),
                "WARN": level_counts.get("WARN", 0),
                "INFO": level_counts.get("INFO", 0),
            })

        return {"trend": trend}
    except Exception as e:
        print(f"Trend 조회 실패: {e}")
        return {"trend": []}


@router.get("/top-errors")
async def get_top_errors(hours: int = Query(24, le=168)):
    """
    최근 N시간 동안 가장 빈번한 에러 메시지 Top 5
    동일한 메시지를 그룹핑하여 빈도순 정렬
    """
    since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()

    body = {
        "query": {
            "bool": {
                "must": [
                    {"range": {"timestamp": {"gte": since}}},
                    {"bool": {
                        "should": [
                            {"term": {"level": "ERROR"}},
                            {"term": {"level": "CRITICAL"}}
                        ],
                        "minimum_should_match": 1
                    }}
                ]
            }
        },
        "size": 0,
        "aggs": {
            "by_message": {
                "terms": {
                    "field": "message.keyword",
                    "size": 5,
                    "order": {"_count": "desc"}
                },
                "aggs": {
                    "by_service": {
                        "terms": {"field": "service", "size": 5}
                    },
                    "latest": {
                        "top_hits": {
                            "size": 1,
                            "sort": [{"timestamp": {"order": "desc"}}],
                            "_source": ["timestamp", "container"]
                        }
                    }
                }
            }
        }
    }

    try:
        resp = es_client.search(index=INDEX_NAME, body=body)
        buckets = resp['aggregations']['by_message']['buckets']

        top_errors = []
        for bucket in buckets:
            services = [b['key'] for b in bucket['by_service']['buckets']]
            latest_hit = bucket['latest']['hits']['hits'][0]['_source'] if bucket['latest']['hits']['hits'] else {}
            top_errors.append({
                "message": bucket['key'],
                "count": bucket['doc_count'],
                "services": services,
                "last_seen": latest_hit.get("timestamp", ""),
                "container": latest_hit.get("container", "")
            })

        return {"top_errors": top_errors}
    except Exception as e:
        print(f"Top errors 조회 실패: {e}")
        return {"top_errors": []}


@router.get("/service-health")
async def get_service_health():
    """
    서비스별 헬스 상태 (최근 1시간 기준)
    - total: 전체 로그 수
    - errors: ERROR + CRITICAL 수
    - error_rate: 에러율 (%)
    - status: healthy / warning / critical
    """
    one_hour_ago = (datetime.utcnow() - timedelta(hours=1)).isoformat()

    body = {
        "query": {"range": {"timestamp": {"gte": one_hour_ago}}},
        "size": 0,
        "aggs": {
            "by_service": {
                "terms": {"field": "service", "size": 20},
                "aggs": {
                    "by_level": {
                        "terms": {"field": "level", "size": 10}
                    }
                }
            }
        }
    }

    try:
        resp = es_client.search(index=INDEX_NAME, body=body)
        buckets = resp['aggregations']['by_service']['buckets']

        services = []
        for bucket in buckets:
            level_counts = {b['key']: b['doc_count'] for b in bucket['by_level']['buckets']}
            total = bucket['doc_count']
            errors = level_counts.get("ERROR", 0) + level_counts.get("CRITICAL", 0)
            warns = level_counts.get("WARN", 0)
            error_rate = round((errors / total) * 100, 2) if total > 0 else 0

            if error_rate > 10 or errors > 20:
                status = "critical"
            elif error_rate > 3 or warns > 10:
                status = "warning"
            else:
                status = "healthy"

            services.append({
                "service": bucket['key'],
                "total": total,
                "errors": errors,
                "warns": warns,
                "error_rate": error_rate,
                "status": status
            })

        return {"services": services}
    except Exception as e:
        print(f"Service health 조회 실패: {e}")
        return {"services": []}


# ─── Slack 알림 & 자동 복구 설정 API ───

from pydantic import BaseModel

class SlackConfigRequest(BaseModel):
    webhook_url: Optional[str] = None
    enabled: Optional[bool] = None
    min_alert_level: Optional[str] = None
    active_hours_start: Optional[int] = None
    active_hours_end: Optional[int] = None
    excluded_services: Optional[list] = None
    spike_threshold: Optional[int] = None
    spike_window_sec: Optional[int] = None
    critical_cooldown: Optional[int] = None
    error_spike_cooldown: Optional[int] = None


class RecoveryConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    restart_threshold: Optional[int] = None
    error_window_sec: Optional[int] = None
    max_restarts_per_hour: Optional[int] = None
    restart_cooldown: Optional[int] = None


@router.get("/alerts/config")
async def get_alert_config():
    """Slack 알림 전체 설정 조회"""
    from ..services.slack_notifier import get_slack_notifier
    notifier = get_slack_notifier()
    return notifier.get_config()


@router.post("/alerts/config")
async def set_alert_config(req: SlackConfigRequest):
    """Slack 알림 설정 업데이트"""
    from ..services.slack_notifier import get_slack_notifier
    notifier = get_slack_notifier()

    if req.webhook_url is not None:
        notifier.set_webhook_url(req.webhook_url)

    if req.enabled is not None:
        notifier.set_enabled(req.enabled)

    settings = req.dict(exclude_none=True, exclude={"webhook_url", "enabled"})
    if settings:
        notifier.update_settings(settings)

    return {
        "success": True,
        "config": notifier.get_config(),
        "message": "설정이 업데이트되었습니다."
    }


@router.post("/alerts/test")
async def test_alert():
    """Slack 테스트 메시지 전송"""
    from ..services.slack_notifier import get_slack_notifier
    notifier = get_slack_notifier()
    result = notifier.send_test_message()
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "전송 실패"))
    return result


@router.get("/alerts/status")
async def get_alert_status():
    """런타임 알림 상태 (에러 윈도우, 쿨다운, 이력)"""
    from ..services.slack_notifier import get_slack_notifier
    notifier = get_slack_notifier()
    return notifier.get_status()


# ─── 자동 복구 API ───

@router.get("/recovery/config")
async def get_recovery_config():
    """자동 복구 설정 조회"""
    from ..services.auto_recovery import get_auto_recovery
    recovery = get_auto_recovery()
    return recovery.get_config()


@router.post("/recovery/config")
async def set_recovery_config(req: RecoveryConfigRequest):
    """자동 복구 설정 업데이트"""
    from ..services.auto_recovery import get_auto_recovery
    recovery = get_auto_recovery()

    if req.enabled is not None:
        recovery.set_enabled(req.enabled)

    settings = req.dict(exclude_none=True, exclude={"enabled"})
    if settings:
        recovery.update_settings(settings)

    return {
        "success": True,
        "config": recovery.get_config(),
        "message": "자동 복구 설정이 업데이트되었습니다."
    }


@router.get("/recovery/status")
async def get_recovery_status():
    """자동 복구 런타임 상태 (에러 카운트, 재시작 이력)"""
    from ..services.auto_recovery import get_auto_recovery
    recovery = get_auto_recovery()
    return recovery.get_status()


