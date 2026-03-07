# 프로젝트 통합 아키텍처 및 시스템 요약 명세서 (SUMMARY.md)

> **문서 목적**: 본 명세서는 `Lookalike` 통합 데이터 파이프라인 및 백엔드/신경망 아키텍처의 전체 조감도(Overview)를 체계적으로 요약 제공함. 각 챕터별 상세 구현 내역은 하위 문서 링크를 참조 요망. 작성 구조는 공식 매뉴얼 형태를 따름.

---

## 1. 시스템 핵심 컴포넌트 구성도

당사 시스템은 **[코어 백엔드망]**, **[데이터 파이프라인망]**, **[검색 및 ML 추론망]**, **[인프라 관제 모니터링망]** 4대 축으로 구성된 초거대 분산 클러스팅 환경임.

### 1.1 코어 백엔드 시스템 및 API망 (Core Backend Service)
- **프론트엔드 (UI/UX)**: 순수 HTML/JS 기반 초경량 SSR 렌더링 프레임워크 지원.
- **백엔드 (API Core)**: `FastAPI` (Python) 기반 완전 비동기 REST API (API Gateway).
- **메인 DB 레이어**: 
  - `PostgreSQL`: 유저 정보, 게시판 등 정형 트랜잭션 원장.
  - `MongoDB`: 비정형 크롤링 원시 덤프 스토어.
  - `Redis`: 사용자 인증 세션 워커 및 캐싱 저장소 (24h TTL).

> 🔗 **관련 상세 문서**:
> - `docs/architecture/core_backend_system.md` (API 설계, DB 스키마, 소셜 인증 및 게시판 코어 플로우 종합 명세)

### 1.2 통합 데이터 파이프라인망 (Data Engineering)
- **기반 엔진**: `Apache Airflow` 스케줄러 기반의 9단계 완전 자동화 분산 제어망.
- **수집 및 정제**: Playwright 웹 크롤러 ➔ PySpark 분산 클러스터 전처리 ➔ HDFS 및 PostgreSQL 적재.
- **멀티모달 융합**: 이미지 YOLO 크롭 ➔ 고차원(512/384) 텐서 임베딩 전처리 ➔ VLM 텍스트화 ➔ Elasticsearch 최종 검색망 하이브리드 동기화 수행.

> 🔗 **관련 상세 문서**:
> - `docs/architecture/data_pipeline_system.md` (데이터 수집부터 ML 융합 적재까지의 전체 워크플로우 아키텍처)

### 1.3 하이브리드 검색 및 ML 추론망 (Search & ML Inference)
- **비전 전처리 (Pre-Cropping)**: YOLOv8 파인튜닝을 통해 무작위 환경 이미지의 노이즈 배경을 쳐내고 의류 타겟 객체만 추출.
- **시각 특징 추출 (CNN)**: 추출된 픽셀을 고차원 부동소수점 벡터(Vector) 임베딩으로 인코딩화.
- **검색 3단계 전략 라우팅**: 사용자 실시간 업로드 ➔ ML 컨테이너 Inference ➔ `Elasticsearch` kNN 유사도 역인덱스 스캔 ➔ 텍스트 폴백 ➔ DB 랜덤 폴백 순으로 3단계 자동 유연 생존 방어 탐색기 운용.

> 🔗 **관련 상세 문서**:
> - `docs/architecture/search_and_ml_system.md` (머신러닝 서빙 파이프라인 및 백엔드 3단계 하이브리드 검색 연결망 구조 명세)

### 1.4 인프라 통합 모니터링 및 복구망 (Monitoring & Observability)
- **메트릭 관제 (Infra)**: Docker SDK 기반 볼륨, 네트워크, CPU 자원을 Kafka 채널을 경유해 수집하는 고속 폴링망.
- **로그 수집망 (Log)**: `Filebeat` 데몬 ➔ `Apache Kafka` 분산 버퍼 ➔ `Elasticsearch` 무중단 적재 아키텍처 (Fall-back 채널 보유).
- **알림 및 자가 복구 (Alert & Recovery)**: 인시던트(Error/Critical) 감지 시 유예 쿨다운 타임 및 서킷 브레이커를 탑재한 초정밀 Slack 봇망 & 데드락 컨테이너 생명 연장(Recovery).

> 🔗 **관련 상세 문서**:
> - `docs/admin/infra_monitoring_system.md` (하드웨어 및 시스템 자원 통합 관제 메커니즘)
> - `docs/admin/log_monitoring_system.md` (컨테이너 분산 로그 수집 듀얼 트랙 파이프라인)
> - `docs/admin/alert_monitoring_system.md` (스팸 제어 Slack 알람 훅 및 자가 복구 봇 망)

---

## 2. 문서 맵 (Document Directory Map)

| 분류 구분 | 주요 문서 경로 | 명세 담당 내용 |
| :--- | :--- | :--- |
| **튜토리얼/셋업 (Tutorial)** | `docs/SETUP.md` | 로컬 환경 Docker 셋업 및 문제 해결 워크스페이스 구축 가이드 |
| **시스템 아키텍처 (Core)** | `docs/architecture/` | 전체 시스템 통합 인프라, 백엔드 API, 검색/ML, 에어플로우 데이터 파이프라인 통합 명세 |
| **운영/서버 모니터링 (Admin)** | `docs/admin/` | 인프라 관제, 로그 수집, 알람 통제 시스템 상세 메커니즘 및 엣지 트러블슈팅 이력망 |

---
### 작성자 : 한대성
> 최종 커밋 업데이트 라인: 2026-03-07
