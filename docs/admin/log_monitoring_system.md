# 통합 로그 모니터링 시스템 아키텍처 및 워크플로우 명세서

> **문서 목적**: 실시간 컨테이너 에러 로그 모니터링, 알림망 통제, 수집 이중화 및 시스템 자동 복구(Auto-Healing)를 아우르는 통합 로그 관제망의 아키텍처 설계와 실행 워크플로우를 기술함.

## 1. 시스템 통합 개요 및 아키텍처

14개 분산 Docker 컨테이너 클러스터에서 파생되는 에러 스트림 및 접근 로그를 **Filebeat → Kafka → Elasticsearch** 메인 백본 파이프라인과 **Docker SDK 직접 수집(Fallback 톨게이트)** 이중 경로 트랙으로 교차 수집·저장하고, 이를 기반으로 실시간 모니터링·Slack 알림망 전파·자동 복구를 수행하는 시스템 통합 관리망임.

| 아키텍처 계층 | 도입 기술 규격 | 배정 역할 및 기능 수행 |
|------|------|------|
| **로그 수집 에이전트** | Filebeat 8.x | 물리 컨테이너 파일 시스템 로그 tailing 도출 → Kafka 브로커 큐 전송 |
| **메시지 중계 큐** | Apache Kafka | 토픽 기반 대용량 로그 버퍼링 댐 역할 + 비동기 스트리밍 전달망 |
| **직접 수집 톨백 엔진** | Docker SDK | Docker API 네이티브 소켓을 활용한 컨테이너 stdout/stderr 비상 체제 핑 직접 수집망 |
| **영구 저장 스토리지망** | Elasticsearch 8.x | Lucene 기반의 텍스트 전문 검색(Full-text) + 수학 통계 집계망 |
| **코어 백엔드 API 엔진** | FastAPI | 메인 비동기 REST API 포트망 + 알림 및 자동 복구 처리기 모듈 연결 |

### 1.1 플랫폼 전체 데이터 스트림 플로우 조감도

```text
┌─────────────────────────────────────────────────────────────┐
│                    Docker Host 머신 (14개 타겟 컨테이너)      │
│       │ stdout/stderr → /var/lib/docker/containers/*/*.log  │
└───────┼─────────────────────────────────────────────────────┘
        │
        ├──── [수집 경로 A: Filebeat → Kafka 파이프라인 (기본 메인)] ─┐
        │  Filebeat 에이전트 (container input 훅)                  │
        │    ├─ JSON 필드 강제 디코딩 파싱 & 쓰레기 노이즈 필터링         │
        │    └─ 예외: ERROR/CRITICAL 식별 시 필터 무조건 면제 통과  │
        │         ▼                                           │
        │    Kafka 브로커 (매핑 토픽명: container-logs)           │
        │         ▼                                           │
        │    KafkaLogConsumer 백그라운드 워커 (Python 기반)          │
        │    ├─ Filebeat 송신 메시지 파싱 정제 (이름 해석 폴백 로직)    │
        │    └─ 50건 청킹 단위 버퍼링 or 10초 인터벌 타이머 강제 플러시 │
        │                                                     │
        ├──── [수집 경로 B: Docker SDK 직접 수집망 (Fallback 비상 타개책)] ┐
        │  LogCollector 비상 데몬 (Python, 10초 주기 루프 핑)      │
        │    ├─ docker.from_env() 소켓 교섭 → Docker Engine API 직결 │
        │    ├─ MD5 해시 알고리즘 기반 데이터 중복 적재 방어 락(_id 생성) │
        │    ├─ SlackNotifier.check_and_alert() 감시 알람 모듈 훅 트리거 │
        │    └─ AutoRecovery.track_error() 감시 스니퍼 훅 트리거     │
        │                                                     │
        └────────────────────┬────────────────────────────────┘
                             ▼                                 
                    Elasticsearch 통합 적재 엔진 저장고          
                    (타겟 index명: container-logs / ILM 정책: 7일 후 파기 / interval: 30초)
                             │                                 
                             ▼                                 
                    FastAPI REST API / 프론트 대시보드 (Bootstrap 5)
                    ├─ /api/logs/stream 터널     (실시간 로그 스트리밍) 
                    ├─ /api/logs/dashboard       (stats, trend, errors 일괄 반환)
```

### 1.2 이중 듀얼 수집 경로망 설계 의도

| 비교 관점 | 메인 경로 A (Filebeat → Kafka 브릿지망) | 비상 경로 B (Docker SDK 직접 폴링 수집망) |
|---|---|---|
| **수집 동작 방식** | 물리 로그 파일 tailing 감시 (Push 푸시 타입) | 호스트 Docker API 엔진 조립 폴링 (Pull 풀 타입) |
| **통신 지연 (Latency)** | ~1초 내외 (초실시간 Near Real-time 보장) | ~10초 (폴링 주기 루프 간격 한계치) |
| **특장점** | 고성능 저부하 압축 엔진, 백프레셔(Backpressure) 지원 | Kafka 장애 시에도 꿋꿋하게 독립 생존(Fallback 톨게이트 발동) |

---

## 2. 통합 워크플로우 및 파이프라인 상세 명세 (Workflow)

서버 기동부터 화면 렌더링에 이르는 로그 처리기 6단계 파이프라인 실행 흐름임.

### 2.1 서버 부팅 및 데몬 초기화 (`main.py`)
- Elasticsearch 인덱스(`container-logs`, `container-metrics`) 맵핑 멱등적 생성.
- `LogCollector` 및 알림/복구 싱글턴 객체 확보, 비동기 스레드 무한 수집 타이머 훅 가동.
- `KafkaMetricConsumer` / `KafkaLogConsumer` 백그라운드 스레드 풀 데몬 분기 기동 시작.

### 2.2 하위 로그 정제 체계 및 노이즈 필터링
Filebeat와 LogCollector 엔진에서 각각 필터링 및 등급 판별이 발생함.
- **제거 대상**: 헬스체크 정상 핑 로그, Java/Hadoop 무의미 반복 스택트레이스 파편.
- **예외 권한 통과**: 내용 중 `ERROR` / `CRITICAL` 문구가 파싱될 경우 무조건 살려 필터망 패스.

### 2.3 메시지 큐 소비 및 멱등화 저장 (Elasticsearch 적재)
**KafkaConsumer 수신단 (50건 벌크 플러시)**
- 파편화된 컨테이너 이름 해석 보정을 위해 4단계 이중화 폴백망(ID→Name 매핑 변환 해싱) 가동.
- `helpers.bulk()` 엔진망으로 벌크 인덱싱 강제 송신.

**멱등성 데이터 중복 방어 구조**: 로그 라인 메타가 양쪽 수집 톨백 양단으로 중복 유입되더라도 `doc_id = "{container}_{timestamp}_{msg_hash(8자리)}"` 로직 기반으로 배타적 덮어쓰기 실시. (100% 중복 삽입 무결성 해결)

### 2.4 외부 알림 전파 & 오토 힐링 복구 훅 (Slack & Auto-Recovery)

**[Slack 관제망 지시]**
- `check_and_alert()`: CRITICAL 발생 시 즉각 타전. ERROR 다발 스파이크 시 요약 경보 묶어서 타전. (서버 워밍업 시간, 화이트리스트 쿨다운 룰셋, Rate Limiting 방어 통제 통과 필요).

**[오토 셀프-힐링 봇 지시 (`auto_recovery.py`)]**
- `track_error(log)` 엔진에서 치명 로그 지속 적중 시, 에러 한계 임계선 돌파 여부 확인.
- 최종 돌파 판정 시 `docker_client.containers.get().restart(timeout=30)` 30초 내 강제 킬 재부팅 전격 하달. 성공 시 슬랙망에 "우회 자가 복구 성공 타전" 보고.

### 2.5 API 관제망 (백엔드 ↔ 프론트엔드 연동)

| 통신 네트워크 엔드포인트 | बा인딩된 백엔드 코어 기능 | 내부 동작 상세 및 캐싱 전략 |
|-----------|------|------|
| `GET /api/logs/dashboard` | `get_log_dashboard` | ES `msearch` 병합 쿼리 1팡 타전 (stats/trend/top-errors). 인메모리 30초 강력 캐시 래핑 동반. |
| `GET /api/logs/stream` | `get_log_stream` | 실시간 로그 객체 스냅샷 페이징/조건 필터 덤프 반환. |
| `GET/POST /api/logs/alerts/config` | `get/update_alerts_config` | Slack 알랍 온오프 스위치 및 타겟 조준 강제 오버라이드. |

**프론트엔드 렌더링 최적화:** `admin_logs.html` 지면에서는 브라우저 렌더 워터폴 병목을 삭감하기 위해 선행 `window.__preload` 스캐폴딩 스피드 장악을 사용. 무거운 실시간 차트 작화(`Chart.js`) 구역과 가벼운 통계 카드 구역으로 병렬 비동기 API 파이프 교섭을 나눔.

---

### 작성자 : 한대성
> 최종 업데이트: 2026-03-07
