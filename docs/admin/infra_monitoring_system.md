# 통합 인프라 모니터링 시스템 아키텍처 및 워크플로우 명세서

> **문서 목적**: 본 문서는 Lookalike 프로젝트 관제 환경의 핵심 파이프라인인 인프라 모니터링 시스템의 설계 배경, 아키텍처 구조, 실행 흐름(Workflow), 기술적 의사결정 제반, 그리고 성능 최적화 진화 과정을 종합 기술함.

---

## 1. 관제 시스템 및 아키텍처 개요

### 1.1 프로젝트 배경 및 요구사항

Lookalike 플랫폼은 패션 유사 상품 딥러닝 검색 등 연산 부하가 높은 다수의 마이크로서비스가 고립된 분산 Docker 컨테이너 클러스터 위에서 구동되는 아키텍처임. 서비스 생존성과 트래픽 안정성을 보장하기 위해 **초단위 실시간 인프라 모니터링 시스템** 도입이 타결되었고, 다음의 3가지 핵심 백본 요구사항을 100% 충족시켜야 했음:

| 요구사항 속성 | 해결 목표 및 설명 |
|---------|------|
| **실시간성(Real-time)** | 물리 컨테이너별 CPU 코어 및 메인 메모리 사용률을 15초 단위 주기 스캐닝을 통해 무결점 수집 달성 |
| **시계열 분석(Time-series)** | 과거 적재 데이터를 기반으로 타임라인 추이 리포트 차트 시각화 제공 |
| **초저지연 응답(Low Latency)** | 관리자 인프라 대시보드 브라우저 렌더링을 API 래그(Lag) 없이 1초 이내 초고속 달성 보장 |

모니터링 시스템은 **Backend(데이터 수집 및 API 프로바이딩)**와 **Frontend(시각화 표출)** 계층으로 분리 구성되어 있음. 시스템, 컨테이너, 데이터베이스의 런타임 상태 정보를 실시간 수집하여 관리자가 직관적으로 파악할 수 있도록 통합 대시보드 형태로 제공함.

```text
┌─────────────────────────────────────────────┐
│             모니터링 관제 타겟 서비스 그룹        │
├──────────┬──────────────────────────────────┤
│ airflow  │ airflow-webserver, scheduler     │
│ spark    │ spark-master, spark-worker-1     │
│ hadoop   │ namenode, datanode               │
│ kafka    │ kafka, zookeeper                 │
│ database │ postgres, mongo, redis           │
│ search   │ elasticsearch                    │
│ api      │ fastapi                          │
└──────────┴──────────────────────────────────┘
```

---

## 2. 모니터링 전체 실행 플로우 명세 (Workflow)

서버 기동 → 메트릭 수집 → Kafka 전송 → ES 저장 → API 조회 → 프론트 렌더링에 이르는 총 6단계의 실행 흐름을 물리적 파일명 및 함수명 기준으로 서술함.

### 2.1 서버 기동 파이프라인 (`main.py · lifespan()`)

```text
main.py  lifespan()
│
├─ init_all_databases()             # core/database.py
│    └─ PG / Mongo / Redis 커넥션 팩토리 연결 초기화 진행
│
├─ init_elasticsearch_index()       # core/elasticsearch_setup.py
│    └─ es.indices.create("container-metrics") ─ 타겟 인덱스 매핑 생성 (멱등성 보장)
│
├─ MetricCollector.__init__()       # services/metric_collector.py
│    ├─ docker.from_env()           ─ 호스트 Docker 소켓 인터페이스 연결 보장
│    ├─ topic = "system-metrics"
│    └─ producer = None             ─ Lazy 로드 방식 초기화
│
├─ asyncio.create_task(metric_collector.start()) ─ 비동기 15초 슬립 주기 무한 루프 스케줄러 등록
│
└─ KafkaMetricConsumer().start()    # services/kafka_metric_consumer.py
     └─ threading.Thread(daemon=True).start()   ─ 메인과 분리된 별도 스레드 풀에서 워커 동작
```

### 2.2 메트릭 수집 파이프라인망 (`services/metric_collector.py`)

Python 생태계 기반의 **FastAPI** 서버 내장 모듈이 시스템 호스트 및 서비스 상태를 실시간 수집함.

*   `psutil`: CPU, Memory, Disk, Uptime 등 호스트 Bare-metal 리소스 직접 수집.
*   `docker`: 호스트 Docker 소켓(`unix:///var/run/docker.sock`) 바이파이프 통신. 실행 중인 컨테이너 상태 정보 조회.
*   `psycopg2`, `pymongo`, `redis-py`: 코어 DB의 활성 연결 수, 용량, 헬스 체크 상태를 동기 핑 테스트로 조회.

```text
start()
└─ while True:
    collect_metrics()
    └─ for container_name in monitored_containers:   ─ 14개 대상 컨테이너 순회 조회
        container = docker_client.containers.get(container_name)
        stats = container.stats(stream=False)        ─ 내부적으로 ~1초 딜레이 대기 발생 (CPU 사용량 샘플 2회 델타 비교 목적)
        │
        ├─ _get_cpu_percent(stats)
        ├─ _get_memory_percent(stats)
        ├─ _get_memory_usage(stats)
        └─ _publish_to_kafka({timestamp, container, service, cpu_percent, memory_percent, memory_usage})
             ├─ producer가 None 객체일 시 KafkaProducer 최초 연결 시도 발동 (Lazy Init)
             ├─ producer.send("system-metrics", data)
             └─ 네트워크 실패 직면 시 producer = None 처리 ─ 다음 15초 주기에 재연결 시도 (Fail-Safe 정책)
    │
    asyncio.sleep(15)
```

수집 크론 주기 타깃을 1초(단기) 대신 **15초**로 포지셔닝한 이유는 백엔드 CPU 마비 부하를 방지하고 최적의 데이터 해상도와 안정성을 보장하기 위함임.

### 2.3 브로커 전송 → 스토리지 저장 플로우 (`services/kafka_metric_consumer.py`)

```text
KafkaMetricConsumer (비동기 병렬 스레드 작동)
consume_and_index()
└─ KafkaConsumer("system-metrics", auto_offset_reset="latest")
    for message in consumer:
    ├─ buffer.append({"_index":"container-metrics", "_source": message.value})
    └─ len(buffer) ≥ 10 조건 스레시홀드 OR 마지막 flush 경과 시간 ≥ 2s
         → helpers.bulk(es_client, buffer)   ─ 엘라스틱서치로 벌크 인덱싱 강제 플러시 전송
         → buffer = []                       ─ 메모리 버퍼 비움
```

**Elasticsearch 스토리지 인덱싱 설계 스키마 (`container-metrics` 규칙):**
1. **타입 최적화**: `timestamp`(date), `service/container`(keyword), `cpu/memory_percent`(float), `memory_usage`(long).
2. **ILM (Index Lifecycle Management)**: 7일 생명 보관 사이클 이후 자동 영구 삭제 처리하여 디스크 폭주 셧다운 폴트를 사전 방어함.

---

## 3. 핵심 아키텍처 진화 및 점진적 구조 개선 (Technical Decisions)

### 3.1 1세대 레거시 뷰 → 2세대 파이프라인 진화

기존 **1세대 방식**(`admin.py` 라우터)은 호스트 Docker 구동 코어 소켓을 동기식 API로 찔러 가져오는 직결 폴링 구조였으나, 매 호출당 25초 이상의 핑 지연, CPU 폭증, 시계열 분석의 부재가 치명적인 장애 요소로 남아있었음. 이를 해결하기 위해 트래픽을 처리하는 수집망(Write)과 조회망(Read)을 격리하는 2세대 구축을 강행함.

**2세대 코어 구조: Kafka + Elasticsearch 파이프라인 망 완성**
*   로컬 버퍼 + 벌크 API 단일 콤보로 10건 통짜 데이터셋을 약 단 15ms 미만 초속으로 적재 수용함.
*   백그라운드 Kafka 워커 스레드는 `threading.Thread(daemon=True)`를 통해 메인과 완벽히 디커플링됨.
*   로딩 속도가 25~60초 대에서 **0.3~1초 내외** 수준 쾌속으로 약 60배 고속화 개선됨.

### 3.2 Kafka 브릿지망 도입 결정 근거 (다이렉트 ES 쓰기 vs Kafka 큐 버퍼링)

ES로 바로 직결하지 않고 중간에 **Kafka 버퍼 큐망**을 굳이 경유한 아키텍처적 결정 사유:
1. **내결함성 보장(Fault Tolerance)**: ES 노드가 셧다운 장애 발발 시에도, Kafka 큐 디스크에 패킷이 보존되어 무손실 데이터 방어 복구 기동 지원.
2. **무한 확장의 폭 유연성**: Kafka 토픽 Pub/Sub 채널 구조를 취하므로, 향후 슬랙 알람 챗봇 데몬 등 다른 종류의 컨슈머(Consumer) 노드를 손쉽게 통신 분기 확장 가능함.

---

## 4. API 엔드포인트 조회망 설계 및 프론트엔드 연동 (Read Path)

### 4.1 백엔드 리딩 통신망 (`routers/metrics.py · admin.py`)

| 네트워크 엔드포인트 | 바인딩된 백엔드 함수명 | 내부 동작 상세 |
|-----------|------|------|
| `GET /api/metrics/stream` | `get_metrics_stream` | ES bool+sort 복합 쿼리 타격, 타임스탬프 역순 정렬 스냅샷 반환 |
| `GET /api/metrics/stats` | `get_metric_stats` | `size: 0` 제한 스펙 발동 후 오로지 `terms` 및 `avg` 수학 집계 결과치만 고속 압축 반환 (약 50ms 이내) |
| `GET /api/admin/infra/dashboard` | `get_infra_dashboard` | OS system(psutil) 스탯 + 각 RDBMS 생사 상태 통합 1회 왕복 체크 후 패키징 묶음 반환 **메모리 캐싱** |
| `GET /api/admin/docker/containers` | `get_docker_containers` | 통신 부하가 큰 Docker stats 직렬 연산본 캐시 즉시 리턴 |

**백그라운드 선 쟁탈 스케줄러(Pre-Warming Auto Scheduler) 적용:**
60초의 API 타임 캐시 임계치가 만료(80% 도달) 지정 직전 시퀀스일 때, 시스템 스스로 비동기 `asyncio.create_task()` 루틴을 동작 시켜 클라이언트 요청 이전에 최신 상태를 강제 백그라운드 리로드 탈취함. 따라서 100% 영구적 Cache Hit 결괏값을 0.0s의 일말 지연 없이 실시간급으로 반환 응답함.

### 4.2 대시보드 프론트엔드 UI 폭포수 렌더링 (`templates/admin_infra.html`)

프론트엔드 DOM 로딩 타임 딜레이를 병목 우회 회피하기 위해 다중 분산 비동기 브릿지망 기반의 **2 Phase 점진 분할 로딩(Progressive Waterfall Step Rendering)** 로직을 신규 이식함.

1. **Phase 1 관문 (초고속 타점)**: `loadInfraDashboard()`, `fetchStats()`, `fetchStream()` 등 100~300ms 쾌속 응답 API들을 `Promise.all()` 다발 동시 스레드 발동으로 우선 스캐폴딩 렌더링 장악함. (Chart.js 라인 차트 브러싱, OS 가용 상태 뱃지 체결)
2. **Phase 2 렌더 (지연 유배망)**: `loadDockerStatus()` 처럼 태생적으로 속골 병목 무거운 레이어를 지닌 API만을 await 록킹 해제하여 고의로 단일 후열 이관 렌더 격리 지시함. 이로 인해 브라우저 DOM 렌더 파단 먹통 마비 타임이 원천 방어 파기됨.

추가 최적화로 Chart.js 콤포넌트 등에 브라우저 지연 차단용 `defer` 속성 바인딩 마커 도금 및 웹 성능 표준 `preconnect` 메타 프리패치 힌트 기술을 프론트에 전부 적용 완료함. 세션 권한 `sessionStorage` 고속 임시 캐싱 토큰 또한 5분 유효로 설정하여 불필요 워터폴 네트워크 낭비 핑 지불을 일절 근절 마감함.

---
### 작성자 : 한대성
> 최종 커밋 업데이트 라인: 2026-03-07
