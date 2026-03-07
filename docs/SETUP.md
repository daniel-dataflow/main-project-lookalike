# 프로젝트 로컬 환경 셋업 가이드 (SETUP.md)

> **문서 목적**: 신규 개발자 온보딩 및 인프라 이전을 대비하여, `Lookalike` 백엔드 코어망 및 분산 파이프라인 생태계를 로컬 머신(Ubuntu/Mac 환경)에 셋업하고 무결성 테스트를 완료하는 일련의 표준 구성 지침을 서술함. 

---

## 1. 사전 필수 요구 사항 (Prerequisites)

시스템 환경 구축을 위해 다음 도구 및 하드웨어 스펙이 선행 준비되어야 함.
- **Docker & Docker Compose**: 버전 `v2.20+` 이상 (멀티 컨테이너 클러스터 오케스트레이션 필수)
- **Python 환경**: 버전 `3.11+` (호스트 머신 디버깅 및 가상 환경 구동 목적)
- **Git**: 소스 코드 리비전 컨트롤 시스템
- **하드웨어 제원 (권장)**: 최소 16GB RAM 이상 허용 권장 (Kafka, ES, Spark, ML 모델 동시 구동 시 메모리 병목 우려)

## 2. 보안 환경 변수 제어 (.env 파일 세팅)

루트 디렉토리 (`/`) 하단에 시스템 포트 및 시크릿 키 등을 정의하는 환경 변수 파일(`.env`) 복사 생성이 절대적으로 선행되어야 함. 템플릿 양식은 아래 블록을 따름.

```bash
# 터미널에서 구동
cp .env.example .env
```

**[ 주요 코어 변수 명세 ]**
- `APP_ENV`: 런타임 환경망 식별 지표 (`local` / `production`)
- `POSTGRES_USER` / `POSTGRES_PASSWORD`: 코어 RDBMS 마스터 계정
- `ADMIN_PASSWORD`: 시스템 웹 관리자 접근용 마스터키 패스워드
- `MONGO_URI`: MongoDB 커넥션 통신선 DSN
- `ELASTICSEARCH_URL`: 검색/로그 타겟 ES 도메인 `http://elasticsearch:9200`
- `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET`: 외부 데이터 크롤링 수집용 API 키페어
- `SLACK_WEBHOOK_URL_LOCAL` / `PRODUCTION`: 시스템 모니터링 알림 타전 웹훅 도메인 인자

## 3. 도커 클러스터 통합 구동 (권장 컴포즈 방식)

전체 생태계는 `docker-compose.yml`을 메인 노드로 탑재하여 원클릭 분산 구동됨. 현재 시스템은 "Build-once" 최적화가 적용되어 최초 빌드 이후엔 초고속 부팅(Fast Boot)이 보장됨.

### 3.1 전체 파이프라인 무결성 기동 (Full Mesh 스위칭)
에어플로우 파이프라인 체인 및 ML 인퍼런스 머신, 로깅 큐망까지 전체를 구동 처리함.
```bash
# 최초 실행 시 커스텀 이미지 빌드로 3~5분 소요, 이후 실행은 캐싱을 통해 1분 내 부팅 완료
$ docker-compose up -d --build
```

### 3.2 런타임 종료 및 볼륨 초기화
```bash
$ docker-compose down
# 데이터베이스 스토리지 볼륨까지 전부 파기할 경우 (주의 요망)
$ docker-compose down -v
```

## 4. 수동 인프라 개별 기동 명령어 (매뉴얼 디버깅 목적)

컴포즈 없이 격리된 단일 컨테이너 디버깅이나 개별 엔진 스핀업이 필요할 시 아래의 표준 `docker run` 명령어를 통해 수동 조작함.

**[1] PostgreSQL (코어 RDBMS)**
```bash
$ docker run -d --name postgres-main \
  -e POSTGRES_USER=datauser -e POSTGRES_PASSWORD=***REMOVED*** -e POSTGRES_DB=datadb \
  -p 5432:5432 postgres:15
```

**[2] MongoDB (NoSQL 메타데이터)**
```bash
$ docker run -d --name mongo-main \
  -p 27017:27017 mongo:6.0
```

**[3] Redis (인메모리 세션/캐시)**
```bash
$ docker run -d --name redis-main \
  -p 6379:6379 redis:7-alpine
```

**[4] Elasticsearch (벡터 검색 엔진)**
```bash
$ docker run -d --name elasticsearch \
  -e "discovery.type=single-node" -e "xpack.security.enabled=false" \
  -p 9200:9200 -p 9300:9300 docker.elastic.co/elasticsearch/elasticsearch:8.11.1
```

**[5] FastAPI 메인 백엔드 (로컬 호스트 디버깅 실행 방식)**
도커망 대신 로컬 터미널에서 런타임을 킬 경우 가상환경 격리 스와핑을 거침.
```bash
$ pip install -r web/backend/requirements.txt
$ POSTGRES_HOST=localhost MONGODB_HOST=localhost REDIS_HOST=localhost \
  python -m uvicorn web.backend.app.main:app --host 0.0.0.0 --port 8900 --reload
```

## 5. 초기 스키마 셋업 (DB 마이그레이션)

RDBMS 컨테이너가 뜬 후, 초기 뼈대 생성 및 더미 테이블 세팅을 강제로 수행 주입.
```bash
$ bash scripts/apply_db_changes.sh
# 완료 후 "users", "inquiry_board", "products" 등 Core 엔터티 정상 마이그레이션 성공 메시지 필히 확인 요망
```

## 6. 포트 번호 바인딩 네트워크 맵

- **`8900` 포트**: 코어 백엔드 API 엔진 (FastAPI) 및 프론트 웹 접속 랜딩 포트
- **`8080` 포트**: Airflow 웹 대시보드망 (데이터 파이프라인 관리 모니터)
- **`8001` 포트**: ML 인퍼런스 추론 API 전용 포트
- **`5432` 포트**: PostgreSQL 소켓 직결망
- **`9200` 포트**: Elasticsearch REST API 망
- **`6379` 포트**: Redis 인메모리 연결망

---

## 7. 셋업 과정 주요 발생 에러 및 트러블슈팅 안내

환경 구축 중 흔히 직면하는 전형적 엣지 케이스와 대처 방안 요약.

| 발생 에러 및 증상 | 디버그 원인 통찰 | 해결 및 조치 방안 (Fix) |
|---|---|---|
| **`Address already in use` (포트 바인딩 충돌 병목 마비)** | `8900` 통신 포트 등 호스트 네트워크 포트가 이미 타 프로세스에 의해 점유 선점된 상태. VS Code 포트 포워딩 데몬이 백그라운드에서 데드락 점유하는 경우가 잦음. | `lsof -i :8900`으로 점유 프로세스를 찾아 `kill -9 <PID>` 명령으로 강제 락 해제 후 도커를 재기동함. |
| **`Connection reset by peer` (ES 연동 실패 앱 크래시)** | Elasticsearch 컨테이너 노드가 무거운 자원 구조로 인해 JVM 초기화가 늦어져, FastAPI가 부팅 중 타임아웃 맞고 동반 셧다운 뻗어버림. | `.env`에서 ES 연결 주소를 재점검하고, 시스템 생태계를 컨테이너 재시작 없이 그대로 1분가량 대기시키면 백엔드 앱이 ES가 올라온 직후 연결 재개함. |
| **`FileNotFoundError` (.env 유실 누락 통신)** | `.env` 커스텀 보안 설정 파일이 루트에 미결측 누락 창설되지 않아 자격 증명 파라미터가 비어있는 상태로 부팅 강행 시도됨. | 문서 2장의 가이드대로 `cp .env.example .env` 조치 후 `ADMIN_PASSWORD` 등 필수 내역 무조건 수동 배정 부여. |
| **`Illegal Instruction` (Miniconda 도커 내 기동 오류)** | 호스트 로컬 머신 아키텍처(예: Mac ARM64)와 배포된 리눅스 이미지 간 Python/Conda C-바이너리 비호환 발생. | 가상 환경 컨테이너 셋업 때 Conda 대신 오리지널 `pip install -r requirements.txt` 빌딩 시스템으로 의존성 구축. |
| **`passlib vs bcrypt 5.x` 컴포넌트 버전 격돌 파괴** | 버전 락킹을 무시하고 신규 `bcrypt 5.x` 컴포넌트를 난입 설치 시 코어 호환성으로 인해 HTTP 500 마비 응답. | 통합 규약대로 무조건 `requirements.txt`에 락킹된 `bcrypt==4.1.2` 순종 고정 버전을 강제 수용할 것. |

---

### 작성자 : 한대성
> 최종 확인: 2026-03-07
