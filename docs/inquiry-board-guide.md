# 📋 문의 게시판 구현 가이드

> **작성일**: 2026-02-11  
> **구현 범위**: 유저 문의 게시판 + 관리자 문의 관리 + 어드민 비밀번호 인증  

---

## 📑 목차

1. [시스템 개요](#1-시스템-개요)
2. [파일 구조](#2-파일-구조)
3. [DB 스키마](#3-db-스키마)
4. [어드민 인증 흐름](#4-어드민-인증-흐름)
5. [API 엔드포인트](#5-api-엔드포인트)
6. [페이지 구성](#6-페이지-구성)
7. [DB 마이그레이션](#7-db-마이그레이션)
8. [환경 변수](#8-환경-변수)
9. [트러블슈팅](#9-트러블슈팅)

---

## 1. 시스템 개요

```
┌──────────────────────────────────────────────────┐
│                 브라우저 (Frontend)                 │
│                                                    │
│   [유저] inquiry.html        [관리자] admin_inquiry │
│   - 게시글 열람/작성           - 모든 게시글 열람      │
│   - 댓글 작성/삭제             - 답변(댓글) 작성      │
│   - 공지사항 확인              - 상태 필터링           │
└───────────┬──────────────────────┬────────────────┘
            │ API 호출              │ API 호출
            ▼                      ▼
┌──────────────────────────────────────────────────┐
│              FastAPI Backend                       │
│                                                    │
│   routers/posts.py         (게시판 CRUD + 댓글)     │
│   routers/inquiries.py     (문의 게시판 라우터)      │
│                                                    │
│   routers/auth.py                                  │
│   ├── POST /api/auth/admin/login   (관리자 인증)     │
│   ├── POST /api/auth/admin/logout  (권한 해제)       │
│   └── GET  /api/auth/admin/check   (인증 상태 확인)  │
└───────────┬──────────────────────┬────────────────┘
            │                      │
            ▼                      ▼
      ┌────────────┐        ┌────────────┐
      │ PostgreSQL │        │   Redis    │
      │            │        │            │
      │ inquiry_   │        │ 세션 저장소  │
      │   board    │        │ is_admin   │
      │ + comments │        │ 플래그 저장  │
      └────────────┘        └────────────┘
```

### 핵심 설계 결정

| 항목 | 결정 | 이유 |
|------|------|------|
| **권한 분리** | DB `role` 컬럼이 아닌 **세션 `is_admin` 플래그** | 별도 관리자 계정 없이 비밀번호만으로 인증 |
| **테이블** | `inquiry_board` (게시글) + `comments` (댓글) 분리 구조 | 일반적인 게시판 패턴, 확장성 있는 1:N 관계 |
| **답변 방식** | 별도 `comments` 테이블에 댓글로 답변 | 다수 댓글/답변 가능, 유연한 구조 |
| **라우트 순서** | `/admin/*` 경로를 `/{inquiry_id}` 보다 먼저 정의 | FastAPI 경로 매칭 충돌 방지 |

---

## 2. 파일 구조

```
web/
├── backend/app/
│   ├── config.py               # ADMIN_PASSWORD 설정 추가
│   ├── models/
│   │   ├── post.py             # 게시판 Pydantic 모델 (PostResponse, CommentResponse)
│   │   └── inquiry.py          # 문의 Pydantic 모델
│   └── routers/
│       ├── auth.py             # 관리자 인증 API 3개 추가 (553~629줄)
│       ├── posts.py            # ⭐ 게시판 라우터 (게시글 CRUD + 댓글)
│       └── inquiries.py        # 문의 게시판 라우터 (유저+관리자)
│
├── frontend/templates/
│   ├── admin_base.html         # 관리자 인증 오버레이 + 사이드바 메뉴
│   ├── admin_inquiry.html      # 관리자 문의 관리 페이지
│   └── inquiry.html            # 유저 문의 게시판 페이지
│
scripts/
└── apply_db_changes.sh         # DB 마이그레이션 스크립트 (posts → inquiry_board)

.env                            # ADMIN_PASSWORD 설정
```

---

## 3. DB 스키마

### inquiry_board 테이블 (게시글)

> 기존 `posts` 테이블에서 이름이 변경되었습니다.

```sql
CREATE TABLE IF NOT EXISTS inquiry_board (
    post_id     BIGSERIAL PRIMARY KEY,
    title       VARCHAR(200) NOT NULL,
    content     TEXT,
    author_id   VARCHAR(50) REFERENCES users(user_id),
    view_count  INTEGER DEFAULT 0,
    is_notice   BOOLEAN DEFAULT FALSE,
    create_dt   TIMESTAMP DEFAULT NOW(),
    update_dt   TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_inquiry_board_author_id ON inquiry_board(author_id);
```

### comments 테이블 (댓글)

```sql
CREATE TABLE IF NOT EXISTS comments (
    comment_id   BIGSERIAL PRIMARY KEY,
    post_id      BIGINT REFERENCES inquiry_board(post_id) ON DELETE CASCADE,
    author_id    VARCHAR(50) REFERENCES users(user_id),
    comment_text TEXT,
    create_dt    TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_comments_post_id ON comments(post_id);
```

### 테이블 관계

```
inquiry_board (1) ──── (N) comments
    │ post_id              │ post_id (FK)
    │                      │ comment_id (PK)
    └── author_id ──→ users(user_id)
```

---

## 4. 어드민 인증 흐름

유저 DB의 `role` 컬럼을 사용하지 않고, **별도의 비밀번호 기반 인증**을 사용합니다.

```
1. 관리자 페이지 (/admin/*) 접속
   │
2. admin_base.html → GET /api/auth/admin/check 호출
   │
   ├── is_admin: true → 관리자 콘텐츠 표시
   │
   └── is_admin: false → 비밀번호 입력 오버레이 표시
                              │
                         3. 비밀번호 입력 후 POST /api/auth/admin/login
                              │
                         4. .env의 ADMIN_PASSWORD와 비교
                              │
                              ├── 일치 → 세션에 is_admin: true 저장 → 페이지 reload
                              └── 불일치 → 에러 메시지 표시
```

### 핵심 코드 (`auth.py`)

```python
@router.post("/admin/login")
async def admin_login(request: Request):
    """관리자 비밀번호 인증 → 세션에 is_admin 플래그 저장"""
    # 1) 로그인 상태 확인 (기존 사용자 세션 필요)
    # 2) .env ADMIN_PASSWORD와 입력값 비교
    # 3) 세션에 is_admin: true 추가
```

### 관리자 권한 확인 (`inquiries.py`)

```python
def _require_admin(request: Request) -> dict:
    """세션의 is_admin 플래그 확인"""
    session = _require_login(request)
    if not session.get("is_admin"):
        raise HTTPException(status_code=403, detail="관리자 인증이 필요합니다")
    return session
```

---

## 5. API 엔드포인트

### 게시판 API (`/api/posts`)

| Method | Path | 설명 | 인증 |
|--------|------|------|------|
| `GET` | `/api/posts` | 게시글 목록 | 없음 |
| `POST` | `/api/posts` | 게시글 작성 | 로그인 |
| `GET` | `/api/posts/{id}` | 게시글 상세 (댓글 포함) | 없음 |
| `PUT` | `/api/posts/{id}` | 게시글 수정 | 로그인 |
| `DELETE` | `/api/posts/{id}` | 게시글 삭제 (댓글 CASCADE) | 로그인 |
| `POST` | `/api/posts/{id}/comments` | 댓글 작성 | 로그인 |
| `DELETE` | `/api/posts/{id}/comments/{cid}` | 댓글 삭제 | 로그인 |

### 문의 게시판 유저용 API (`/api/inquiries`)

| Method | Path | 설명 | 인증 |
|--------|------|------|------|
| `GET` | `/api/inquiries` | 내 문의글 목록 | 로그인 |
| `POST` | `/api/inquiries` | 문의글 작성 | 로그인 |
| `GET` | `/api/inquiries/{id}` | 문의글 상세 (본인만) | 로그인 |
| `PUT` | `/api/inquiries/{id}` | 문의글 수정 (답변 전만) | 로그인 |
| `DELETE` | `/api/inquiries/{id}` | 문의글 삭제 (답변 전만) | 로그인 |

### 문의 게시판 관리자용 API

| Method | Path | 설명 | 인증 |
|--------|------|------|------|
| `GET` | `/api/inquiries/admin/list` | 전체 문의글 목록 | 관리자 |
| `GET` | `/api/inquiries/admin/{id}` | 문의글 상세 | 관리자 |
| `POST` | `/api/inquiries/admin/{id}/answer` | 답변 작성/수정 | 관리자 |

### 어드민 인증 API

| Method | Path | 설명 |
|--------|------|------|
| `POST` | `/api/auth/admin/login` | 관리자 비밀번호 인증 |
| `POST` | `/api/auth/admin/logout` | 관리자 권한 해제 |
| `GET` | `/api/auth/admin/check` | 관리자 인증 상태 확인 |

---

## 6. 페이지 구성

### 유저 문의 게시판 (`/inquiry`)
- **접근**: 마이페이지에서 "문의 게시판" 링크
- **기능**: 본인 문의글 목록 → 상세 (답변 확인) → 작성/수정/삭제 (SPA 형태)
- **제한**: 답변이 완료된 글은 수정/삭제 불가

### 관리자 문의 관리 (`/admin/inquiry`)
- **접근**: 어드민 사이드바 "문의 관리" 메뉴
- **기능**: 전체 유저 문의글 열람 → 상태별 필터(전체/대기중/답변완료) → 답변 작성/수정
- **정렬**: 미답변 글 우선 표시

---

## 7. DB 마이그레이션

### 방법 1: 통합 마이그레이션 스크립트 (권장)

```bash
# 프로젝트 루트에서 실행
bash scripts/apply_db_changes.sh
```

이 스크립트는 다음을 **멱등적으로** 실행합니다:
1. ✅ Airflow DB (airflowdb) 분리
2. ✅ users 테이블 소셜 로그인 컬럼 추가
3. ✅ inquiry_board 게시판 테이블 마이그레이션:
   - 기존 답변 내장형 `inquiry_board` 테이블 감지 → 삭제
   - `posts` 테이블 → `inquiry_board`로 이름 변경
   - `inquiry_board`가 없을 경우 새로 생성
   - `comments` 테이블 확인 및 생성

### 방법 2: docker-compose init-db

```bash
# 처음 설치 시 자동 실행
docker compose up -d init-db
```

`docker-compose.yml`의 `init-db` 서비스에 `CREATE TABLE IF NOT EXISTS inquiry_board`와 `comments`가 포함되어 있습니다.

---

## 8. 환경 변수

`.env`에 추가된 설정:

```env
# === 관리자 설정 ===
ADMIN_PASSWORD=***REMOVED***    # 관리자 인증 비밀번호 (운영 시 반드시 변경)
```

`config.py`에서 로드:

```python
class Settings(BaseSettings):
    ADMIN_PASSWORD: str = "***REMOVED***"
```

---

## 9. 트러블슈팅

### 해결된 이슈

| 이슈 | 원인 | 해결 |
|------|------|------|
| 관리자 페이지에서 문의글 안 보임 (403) | 모든 유저 `role`이 `USER` → `_require_admin`이 DB role 검사 | 세션 `is_admin` 플래그 방식으로 전환 |
| FastAPI 라우트 충돌 | `/admin/list`가 `/{inquiry_id}`로 매칭 | 관리자 라우트를 먼저 정의 |
| 사이드바 활성 탭 안 보임 | Bootstrap `.nav-link.active` 기본 스타일이 커스텀 CSS 덮어씀 | CSS 선택자 우선순위 강화 (`!important`) |
| 웹서버 응답 없음 | VS Code 포트 포워딩이 localhost:8900 가로챔 | Docker 컨테이너로 실행 전환 |
| Docker에서 miniconda 실행 불가 | macOS용 바이너리는 Linux 컨테이너에서 사용 불가 | `pip install -r requirements.txt`로 변경 |
| `posts` 테이블과 `inquiry_board` 중복 | 기존에 `posts` + 답변 내장형 `inquiry_board` 공존 | `posts` → `inquiry_board`로 통합, 답변은 `comments` 테이블로 분리 |

### ⚠️ 주의사항

1. **라우트 순서**: `inquiries.py`에서 `/admin/*` 경로는 반드시 `/{inquiry_id}` 보다 **먼저** 정의
2. **관리자 비밀번호**: `.env`의 `ADMIN_PASSWORD`를 운영 환경에서 반드시 변경
3. **세션 의존**: 관리자 인증은 기존 로그인 세션 위에 `is_admin` 플래그를 추가하는 방식 → 먼저 일반 로그인이 필요
4. **Docker 환경**: FastAPI는 Docker 내에서 `pip install`로 패키지 설치 (miniconda 사용 안 함)
5. **DB 테이블 이름**: 기존 `posts` 테이블은 `inquiry_board`로 변경됨. API 경로(`/api/posts`)는 변경 없음
