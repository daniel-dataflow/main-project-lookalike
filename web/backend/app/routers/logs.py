"""
Elasticsearch에 누적된 시스템 로그를 조회 및 통계화하여 어드민 대시보드에 제공하는 뷰 라우터 모듈.
- 단일/개별 API 통신으로 발생하는 오버헤드를 막기 위해 msearch 기반의 복합 쿼리 엔드포인트를 제공함.
- 장애 대응 및 사후 분석을 위해 에러 빈도 추적, 실시간 스트림, CSV 다운로드 등의 연계 기능을 포함.
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List, Dict
from datetime import datetime, timedelta
import time
from ..config.logging import LOG_INDEX_NAME, DASHBOARD_CACHE_TTL
from ..core.elasticsearch_setup import get_es_client
from kafka import KafkaConsumer, KafkaAdminClient
from kafka.errors import KafkaError
from ..services.log_collector import LogCollector, set_global_purge_time
from ..services.slack_notifier import get_slack_notifier
from ..services.auto_recovery import get_auto_recovery

router = APIRouter(
    prefix="/api/logs",
    tags=["logs"],
    responses={404: {"description": "Not found"}},
)

es_client = get_es_client()
INDEX_NAME = LOG_INDEX_NAME

# ──────────────────────────────────────
# [성능] 대시보드 통합 캐시 (DASHBOARD_CACHE_TTL 초)
# ──────────────────────────────────────
_dashboard_cache = None
_dashboard_cache_time: float = 0
_DASHBOARD_CACHE_TTL = DASHBOARD_CACHE_TTL


@router.get("/dashboard")
async def get_log_dashboard():
    """
    모니터링 대시보드 초기 렌더링에 필요한 주요 통계(Stats, Trend, Top Errors, Health)를 한 번에 조회함.
    Elasticsearch의 멀티 서치(msearch) 기능을 사용해 다수 쿼리를 통합 발송함으로써 I/O 비용을 획기적으로 낮춤.

    Returns:
        dict: 부문별(추이, 헬스, 통계 등) 파싱된 ES 응답 결괏값.
    """
    global _dashboard_cache, _dashboard_cache_time

    now = time.time()
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
    """
    Kafka 연동 상태 및 Elasticsearch 엔진의 논리적 문서 개수/스토리지 크기를 진단해 반환함.
    로그 파이프라인(Producer -> Kafka -> Consumer -> ES) 중 어느 구간에 병목이나 끊김이 발생했는지 추적하기 위함.

    Returns:
        dict: 파이프라인 구성 요소들의 활성화 상태 및 ES 데이터 볼륨 현황.
    """
    
    # [성능 최적화] 30초 캐싱: Kafka 연결 타임아웃(최대 1초)을 반복 호출마다 지불하지 않도록
    now = time.time()
    if (hasattr(get_pipeline_status, '_cache')
            and now - get_pipeline_status._cache_time < 30):
        return get_pipeline_status._cache
    
    kafka_ok = False
    direct_ok = False
    
    # 1. Kafka Check (Real connection)
    # [성능 최적화] request_timeout_ms: 3000 → 1000 ms 단축
    try:
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
    실시간 발생하는 로그 내역들을 필터 조건(서비스, 레벨, 검색어)에 맞춰 페이지네이션 없이 단건 배열로 반환함.
    장애 발생 시 특정 컨테이너나 키워드의 최신 발생 로그를 빠르게 검토하기 위한 용도.

    Args:
        service (Optional[str], optional): 서비스 명칭(예: API_BE). ALL은 전체 조회.
        level (Optional[str], optional): 로그 중요도 (ERROR, WARN 등).
        keyword (Optional[str], optional): 메시지에 포함될 자유 텍스트.
        size (int, optional): 조회 크기 (최대 500개).

    Returns:
        dict: 조회된 전체 카운트와 도큐먼트 소스 배열.
    """
    must_conditions = []
    
    if service and service != "ALL":
        must_conditions.append({"match_phrase": {"service": service}})
    
    if level and level != "ALL":
        must_conditions.append({"match_phrase": {"level": level}})
        
    if keyword:
        must_conditions.append({"match_phrase": {"message": keyword}})

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

from fastapi.responses import StreamingResponse

@router.get("/download")
async def get_logs_download(
    service: Optional[str] = None,
    level: Optional[str] = None,
    keyword: Optional[str] = None,
    size: int = Query(10000, le=50000)
):
    """
    대용량 로그를 스트리밍 방식으로 텍스트(txt) 파일 형태다운로드할 수 있도록 헤더를 변환함.
    어드민 화면상에서 파악하기 힘든 방대한 양의 로그를 로컬 에디터에서 분석해야 할 때 사용.

    Args:
        service (Optional[str], optional): 서비스 명칭.
        level (Optional[str], optional): 로그 등급.
        keyword (Optional[str], optional): 검색 메시지.
        size (int, optional): 수출할 라인 수 (최대 5만 개).

    Returns:
        StreamingResponse: Chunk 단위로 스트리밍 되는 raw text 응답 포맷.
    """
    must_conditions = []
    
    if service and service != "ALL":
        must_conditions.append({"match_phrase": {"service": service}})
    
    if level and level != "ALL":
        must_conditions.append({"match_phrase": {"level": level}})
        
    if keyword:
        must_conditions.append({"match_phrase": {"message": keyword}})

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
        
        def iter_logs():
            for hit in hits:
                src = hit['_source']
                ts = src.get('timestamp', '')[:19].replace('T', ' ')
                lvl = src.get('level', 'INFO')
                svc = src.get('service', 'unknown')
                cnt = src.get('container', 'unknown')
                msg = src.get('message', '').replace('\n', '  ')
                yield f"[{ts}] [{lvl}] [{svc}] {cnt} - {msg}\n"
                
        headers = {
            "Content-Disposition": f"attachment; filename=admin_logs_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt"
        }
        
        return StreamingResponse(iter_logs(), media_type="text/plain", headers=headers)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"로그 다운로드 중 오류 발생: {str(e)}")

@router.get("/stats")
async def get_log_stats():
    """
    최근 1시간 내에 생성된 로그의 레벨별, 서비스별 총 발생 건수 분포를 단순 조회함.
    `get_log_dashboard`의 msearch에서 수행하는 범주와 일부 겹치지만, 부분적 컴포넌트 업데이트 시 재사용 가능함.

    Returns:
        dict: 서비스별 비율, 레벨별 발생 빈도 매핑 객체.
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
    현재 시간 기준, 가장 상단에 위치한 에러/크리티컬 레벨의 로그만 추출하여 반환함.
    오류 리포트 패널이나 알림 시스템의 검토용 소스로 활용하기 위함.

    Args:
        size (int, optional): 폴링 사이즈 제한.

    Returns:
        dict: 전체 에러 카운트와 해당 로그 배열.
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
    24시간 타임라인에서 1시간 단위로 시계열 히스토그램을 작성하여 각 구간별 에러/경고 양을 반환함.
    특정 스케줄러(Cron) 시간대에 에러가 집중되는지 상관관계(Trend)를 시각화하기 위해 사용.

    Returns:
        dict: 타임스탬프 문자열과 각 레벨별 카운팅을 지닌 객체 리스트.
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
    최근 지정된 시간 내에 빈번히 나타난 치명적 오류 메시지(ERROR, CRITICAL)들의 패턴을 그룹핑하여 상위 리스트를 반환함.
    반복되는 오류(예: DB 타임아웃, 포트 충돌)의 노이즈 속에서 가장 시급히 해결해야 할 메인 이슈를 특정하기 위해 사용.

    Args:
        hours (int, optional): 탐색할 시간 범위. 기본값 24.

    Returns:
        dict: 장애 원인 텍스트와 빈도수, 마지막 발견 시간이 포함된 Top 오류 배열.
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
    최근 1시간 기준으로 각 컴포넌트(서비스)들의 정상 동작 비율을 평가하여 상태 등급(Healthy, Warning, Critical)을 산정함.
    마이크로서비스 환경에서 어떤 파드가 전체 시스템 결함의 진앙지인지 색상 구분(레드/옐로우)으로 렌더링하도록 프론트에 지표를 전송함.

    Returns:
        dict: 서비스별 에러 비율 및 판정된 헬스 상태 문자열.
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
    # [NEW] 폭주 방지 설정
    startup_grace_sec: Optional[int] = None
    max_alerts_per_window: Optional[int] = None
    rate_window_sec: Optional[int] = None
    circuit_open_sec: Optional[int] = None


class RecoveryConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    restart_threshold: Optional[int] = None
    error_window_sec: Optional[int] = None
    max_restarts_per_hour: Optional[int] = None
    restart_cooldown: Optional[int] = None


@router.get("/alerts/config")
async def get_alert_config():
    """
    현재 설정되어 동작 중인 Slack Webhook URL 및 알림 발생 임계치 등의 전역 환경변수(설정)를 조회함.
    관리자가 어드민 뷰에서 수동으로 알림 민감도를 조정할 수 있도록 UI에 바인딩됨.

    Returns:
        dict: SlackNotifier 서비스 인스턴스의 설정 직렬화 객체.
    """
    notifier = get_slack_notifier()
    return notifier.get_config()


@router.post("/alerts/config")
async def set_alert_config(req: SlackConfigRequest):
    """
    운영 환경 재시작 없이, 런타임 중에 Slack 알림 차단/오픈 및 각종 허들링(Cooldown, Threshold) 매개변수를 교체함.
    새벽 시간대나 대대적 점검 시 무분별한 알림 핑(Ping)을 차단하기 위한 통제 수단.

    Args:
        req (SlackConfigRequest): 변경할 슬랙 관련 파라미터 셋.

    Returns:
        dict: 성공 여부 및 최신 설정값 스냅샷.
    """
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
    """
    등록된 슬랙 채널에 정상적으로 메시지가 도달하는지 확인하기 위해 인위적 페이로드를 주입하여 훅 발송을 테스트함.
    초기 구축 및 URL 변경 이후 온전한 연동 상황을 보장하기 위함.

    Returns:
        dict: 슬랙 API 전송 결과 및 세부 성공 맵.
    """
    notifier = get_slack_notifier()
    result = notifier.send_test_message()
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "전송 실패"))
    return result


@router.get("/alerts/status")
async def get_alert_status():
    """런타임 알림 상태 (에러 윈도우, 쿨다운, 이력)"""
    notifier = get_slack_notifier()
    return notifier.get_status()


# ─── 자동 복구 API ───

@router.get("/recovery/config")
async def get_recovery_config():
    """
    자동 복구(재시작) 루틴을 수행하기 위한 백그라운드 봇의 활성화 상태 및 에러 허용 한계(Threshold) 설정치를 조회함.

    Returns:
        dict: AutoRecovery 인스턴스 전역 설정값.
    """
    recovery = get_auto_recovery()
    return recovery.get_config()


@router.post("/recovery/config")
async def set_recovery_config(req: RecoveryConfigRequest):
    """
    컨테이너 자동 재시작 정책을 On/Off 하거나, 서비스 장애에 따른 재기동 간격(Cooldown)을 런타임에 튜닝함.
    코드 수정/배포 없이 즉석에서 잦은 강제 재기동에 따른 서비스 단절을 제어하기 위함.

    Args:
        req (RecoveryConfigRequest): 덮어씌울 복구 제한 기준(횟수/시간 등).

    Returns:
        dict: 변경 완료 메시지 및 최신 설정 내역.
    """
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
    """
    현재 AutoRecovery 시스템이 최근에 감지한 치명적 로그 수 및 실행했던 과거 재부팅 이력(History) 배열을 전달함.
    모니터링 화면 내에서 '최근 재기동 내역' 컴포넌트에 사용.

    Returns:
        dict: 런타임 제어 상태(Queue) 및 복구 로그 이력.
    """
    recovery = get_auto_recovery()
    return recovery.get_status()


# ──────────────────────────────────────
# 과거 로그 일괄 초기화 (Purge)
# ──────────────────────────────────────
@router.delete("/purge")
async def purge_all_logs():
    """
    Elasticsearch 내 누적된 `container-logs` 도큐먼트들을 물리적으로 일괄 제거함.
    배포 초기 디버깅 단계에서 의도적으로 발생한 테스트 에러들이 대시보드 통계를 흐리는 것을 방지하고, 다시 백지상태에서 새로운 오류들을 점검할 수 있게 하기 위한 강제 클리어 기능.
    삭제 후, 수집기 인스턴스의 타임스탬프 커서를 리셋해 과거 좀비 데이터의 부활을 차단함.

    Returns:
        dict: 초기화 성공 여부와 지워진 데이터 양 통계.
    """
    try:
        # Match_all 쿼리로 인덱스 내의 모든 문서 삭제
        response = es_client.delete_by_query(
            index=INDEX_NAME,
            body={
                "query": {
                    "match_all": {}
                }
            },
            request_timeout=30.0,
            # 강제로 즉시 Refresh 수행하여 UI 최신화 보장
            refresh=True
        )
        
        # [중요] 대시보드 메모리 캐시 강제 무효화
        global _dashboard_cache, _dashboard_cache_time
        _dashboard_cache = None
        _dashboard_cache_time = 0
        
        # [중요 핵심부] 백그라운드 수집기가 과거 찌꺼기를 다시 퍼와서 10초 뒤 리스트에 부활시키는 좀비 현상 원천 차단
        purge_time_utc_str = datetime.utcnow().isoformat() + 'Z'
        set_global_purge_time(purge_time_utc_str)
        
        return {
            "success": True,
            "deleted_count": response.get("deleted", 0),
            "message": "모든 에러 로그 데이터가 성공적으로 초기화되었습니다."
        }
    except Exception as e:
        print(f"Log Purge Error: {e}")
        raise HTTPException(status_code=500, detail="로그 초기화 중 백엔드 오류가 발생했습니다.")
