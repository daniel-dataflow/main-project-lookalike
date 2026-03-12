# 핵심 백엔드 시스템 및 API 아키텍처 명세서

> **문서 목적**: 본 시스템의 메인 백엔드(FastAPI) 아키텍처, 데이터베이스 스키마, 통합 인증(Auth), 그리고 주요 페이지별 핵심 데이터 플로우를 하나로 통합하여 명세함. 작성 톤 앤 매너는 관리자 열람용 공식 매뉴얼 형태를 엄수함.

---

## 1. 코어 백엔드 아키텍처 개요

해당 시스템은 외부 클라이언트 요청을 오케스트레이션하는 **Main FastAPI (API Gateway)** 서버를 주축으로 구동됨. 무거운 추론 연산은 내부망의 ML FastAPI 컨테이너로 위임하고, 자체적인 비즈니스 로직(인증, 검색 라우팅, 게시판, 어드민)을 처리함.

### 주요 데이터베이스 구성
- **PostgreSQL (`datadb`)**: 트랜잭션이 요구되는 핵심 원장 (유저, 상품, 가격, 검색 이력 스냅샷, 게시판).
- **Redis**: 분산 세션 워커 및 캐시 메모리 (24시간 TTL 기반 세션 클러스터링).
- **MongoDB**: 크롤링된 비정형 원시 덤프 데이터 보관.

---

## 2. 페이지별 핵심 동작 플로우 및 백엔드 설정 (Page-by-Page Core Flows)

사용자의 주요 화면 이동 시나리오에 따른 백엔드 연결 플로우 및 설정값을 정의함.

### 2.1 메인 스토어 및 검색 페이지 (`/`)
- **페이지 소개**: 유저가 검색어를 입력하거나 이미지를 업로드하여 유사 의류 상품을 타색하는 메인 랜딩 포털 기능 수행.
- **사용자 이동 체계 (Movements)**: 업로드 폼 제출 $\rightarrow$ 비동기 로딩 스피너 작동 $\rightarrow$ 검색 결과 렌더링.
- **백엔드 코어 플로우**:
  1. `/api/search/by-image` 또는 `/by-text` 라우터가 인입된 페이로드 수신.
  2. 이미지 존재 시 내부 ML 내부망 핑을 통한 인퍼런스 연산 릴레이 수행 후 텐서 벡터 반환받음.
  3. **3단계 검색 라우팅 전략 (Strategy Pattern)** 발동:
     - 1순위: ES kNN 밀집 벡터 검색.
     - 2순위: ES 텍스트 풀 스캔 매칭.
     - 3순위: 인프라 마비 시 PostgreSQL 순수 RDBMS Fallback 우회.
  4. 검색 결과는 영속성 볼륨 보존을 위해 `search_logs` 및 `search_results` (조회 시점 스냅샷 구조) 테이블에 히스토리 기록.

### 2.2 로그인 및 마이페이지 (`/login`, `/mypage`)
- **페이지 소개**: 자체 이메일 계정 가입 및 외부 소셜 인가(Google, Naver, Kakao)를 거쳐 시스템 인증을 획득하고 개인 이력을 확인하는 프라이빗 구역.
- **사용자 이동 체계 (Movements)**: OAuth 모달 오버레이 클릭 $\rightarrow$ 서드파티 인가 $\rightarrow$ 마이페이지 내 최근 본 상품 렌더 및 찜 목록 열람.
- **백엔드 코어 플로우**:
  - **인증 (Auth)**: Redis 기반 인메모리 세션 스토리지 아키텍처 채택. UUID v4 난수를 Key (`session:{uuid}`)로 사용.
  - **보안 설정**: 발급된 세션 쿠키는 `httponly=True` (XSS 방어), `samesite="lax"` (CSRF 방어), 24시간 TTL 만료 설정 강제.
  - **DB 처리**: 소셜 로그인의 경우 `users` 테이블의 `provider`(google, naver 등)와 `social_id` 복합 유니크 인덱스를 통해 다중 가입자 충돌을 통제함.

### 2.3 고객 문의 게시판 (`/inquiry`)
- **페이지 소개**: 인가된 일반 사용자가 시스템 측에 질의/CS 요청을 남기고 관리자의 피드백을 수신하는 1:1 게시판 구역.
- **사용자 이동 체계 (Movements)**: 문의글 작성 $\rightarrow$ 대기중(Pending) 상태 확인 $\rightarrow$ 관리자 답변(Answered) 후 상태 변경 렌더링.
- **백엔드 코어 플로우**:
  - `inquiry_board` (메인 게시글 공간) $\rightarrow$ `comments` (관리자 답변 종속 공간) 1:N 2-Tier 분리 구조 채용.
  - 고객 본인에게 귀속된 글에 한해서만 CRUD 권한 인가 체크.

### 2.4 관리자 종합 대시보드 및 CS 관제 (`/admin/*`)
- **페이지 소개**: 관리자 권한을 취득한 시스템 옵저버가 인프라 상태를 관측(Observability)하고 적체된 문의를 응대하는 루트 구역.
- **사용자 이동 체계 (Movements)**: 마스터 비밀번호 인증 모달 $\rightarrow$ 인프라 메트릭/로그 SSE 관측 $\rightarrow$ 문의 리스트 대기열 응답.
- **백엔드 코어 플로우**:
  - **슈퍼유저 권한 승급 인증**: 사전에 일반 사용자 세션이 잡혀있는 상태에서 POST `/api/auth/admin/login` 호출 시, 기존 Redis 세션 값 안에 `is_admin: true` 휘발성 마스터 플래그를 추가로 인젝션함.
  - **접근 제어**: `_require_admin` 미들웨어 파이프라인이 전면 배치되어, API 요청 시 해당 뱃지 유무를 검열함.
  - **설정 동기화**: 마스터 패스워드 로직은 `.env`의 `ADMIN_PASSWORD` 해시값을 통해 인증 점검됨.

---

## 3. 데이터베이스 시스템 스키마 요약도 (ERD)

주요 트랜잭션 도메인별 릴레이션 관계 테이블 토폴로지.

### [1] 대상 상품 및 검색 스토어
- `products`: 크롤링 의류 물리 정보 집합체.
- `naver_prices`: 특정 상품의 N:1 외부 벤더 가격 종속 테이블.
- `product_features`: 상품 VLM 묘사 특징 등 메타데이터 확충 기록.
- **`search_logs` & `search_results`**: 상호 1:N 이력 트래킹. FK 실시간 조인을 회피하여 원본 글이 훼손되더라도 검색 시점 당시의 가격/상품명 스냅샷(반정규화 설계)을 영속적으로 보존.

### [2] 인증 인가 유저 스토어
- `users`: 이메일과 소셜 회원 통합 다형성 보관처. `(provider, social_id)` 콤포지트 인덱스로 식별 분리.

### [3] 유저-관리자 통합 CS 게시판 스토어
- `inquiry_board`: 루트 문의글 컨테이너 박스. `author_id` 색인 인덱스 적용.
- `comments`: 게시글에 완전 종속되어 피드백을 등록하는 시스템 관리자 답변 전용 테이블 뎁스 (ON DELETE CASCADE 연쇄 삭제 룰 규정 완료).

---

## 4. 핵심 환경 변수 구성 요건 (`.env`)

백엔드 파이프라인 가동 및 보안 인가를 위해 코어 서버로 마운트되어야 하는 불변 설정값:

```dotenv
# 인프라 DB 바인딩 통신 설정
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
REDIS_HOST=localhost

# 관리자 인증 통제 마스터키 패스워드
ADMIN_PASSWORD=***REMOVED***

# 세션 락킹 룰셋 제어기
SESSION_EXPIRE_HOURS=24

# OAuth 서드파티 제휴 엑세스 키 체인
GOOGLE_CLIENT_ID=xxxxxxxxxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxx
NAVER_CLIENT_ID=xxxxxxxxxxxx
KAKAO_CLIENT_ID=xxxxxxxxxxxx
```

---

### 작성자 : 한대성
> 최종 업데이트: 2026-03-07
