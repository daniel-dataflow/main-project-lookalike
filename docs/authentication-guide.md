# 🔐 인증 시스템 구현 가이드

> **작성일**: 2026-02-11  
> **구현 범위**: 이메일 회원가입/로그인 + 소셜 로그인(Google, Naver, Kakao)  
> **세션 관리**: Redis 기반 httpOnly 쿠키 세션

---

## 📑 목차

1. [시스템 아키텍처](#1-시스템-아키텍처)
2. [파일 구조](#2-파일-구조)
3. [⭐ Redis 세션 관리 (핵심)](#3--redis-세션-관리-핵심)
4. [인증 플로우](#4-인증-플로우)
5. [DB 스키마](#5-db-스키마)
6. [OAuth 소셜 로그인](#6-oauth-소셜-로그인)
7. [환경 변수 설정](#7-환경-변수-설정)
8. [API 엔드포인트](#8-api-엔드포인트)
9. [프론트엔드 연동](#9-프론트엔드-연동)
10. [트러블슈팅](#10-트러블슈팅)

---

## 1. 시스템 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                        브라우저 (Frontend)                       │
│  base.html + script.js + style.css                              │
│  - 로그인/회원가입 모달 (Bootstrap 5)                              │
│  - 소셜 로그인 버튼 (Google, Naver, Kakao)                        │
│  - 세션 기반 로그인 상태 유지 (httpOnly 쿠키)                       │
└──────────────┬──────────────────────────────────┬───────────────┘
               │ API 호출                          │ OAuth 리다이렉트
               ▼                                   ▼
┌──────────────────────────┐        ┌─────────────────────────────┐
│   FastAPI Backend        │        │   OAuth 제공자               │
│   (localhost:8900)       │◄───────│   - Google OAuth 2.0        │
│                          │ 콜백    │   - Naver Login API         │
│   routers/auth.py        │        │   - Kakao Login API         │
│   - 이메일 가입/로그인        │        └─────────────────────────────┘
│   - OAuth 콜백 처리        │
│   - 세션 생성/검증/삭제      │
└──────┬───────────┬───────┘
       │           │
       ▼           ▼
┌────────────┐ ┌────────────┐
│ PostgreSQL │ │   Redis    │
│  (5432)    │ │  (6379)    │
│            │ │            │
│ users 테이블│ │ 세션 저장소  │
│ - user_id  │ │ - session: │
│ - email    │ │   {token}  │
│ - provider │ │ - TTL: 24h │
│ - social_id│ │            │
└────────────┘ └────────────┘
```

---

## 2. 파일 구조

```
web/
├── backend/app/
│   ├── main.py              # FastAPI 앱 진입점, 라우터 등록, lifespan 관리
│   ├── config.py            # 환경 변수 로딩 (DB, OAuth, 세션 설정)
│   ├── database.py          # PostgreSQL, MongoDB, Redis 연결 관리
│   ├── models/
│   │   └── user.py          # Pydantic 모델 (요청/응답 스키마)
│   └── routers/
│       └── auth.py          # ⭐ 인증 라우터 (핵심 파일)
│
└── frontend/
    ├── templates/
    │   └── base.html         # 메인 HTML (로그인 모달 포함)
    └── static/
        ├── js/script.js      # 프론트엔드 인증 로직
        └── css/style.css     # 소셜 로그인 버튼 스타일
```

---

## 3. ⭐ Redis 세션 관리 (핵심)

### 3.1 왜 Redis를 세션 저장소로 사용하는가?

| 비교 항목 | 서버 메모리 세션 | DB 세션 | ⭐ Redis 세션 |
|-----------|----------------|---------|-------------|
| **속도** | 매우 빠름 | 느림 (디스크 I/O) | **매우 빠름 (메모리)** |
| **서버 재시작 시** | ❌ 세션 소멸 | ✅ 유지 | ✅ **유지** |
| **다중 서버** | ❌ 공유 불가 | ✅ 공유 가능 | ✅ **공유 가능** |
| **TTL 자동 만료** | ❌ 직접 구현 | ❌ 직접 구현 | ✅ **내장 기능** |
| **확장성** | 낮음 | 보통 | ✅ **높음** |

### 3.2 세션 생명주기

```
[로그인 성공] ──► [Redis에 세션 저장] ──► [쿠키 발급] ──► [요청마다 세션 검증] ──► [로그아웃 시 삭제]
```

#### 상세 흐름:

```
1. 사용자가 로그인 (이메일 또는 소셜)
   │
2. 서버에서 UUID 토큰 생성
   │  token = "6eef3a78-2bdd-4f90-9675-4ca9a54009d2"
   │
3. Redis에 세션 데이터 저장
   │  KEY:   "session:6eef3a78-2bdd-4f90-9675-4ca9a54009d2"
   │  VALUE: {"user_id":"google_8aa0","name":"Daniel","email":"daniel@gmail.com",...}
   │  TTL:   86400초 (24시간 후 자동 삭제)
   │
4. 브라우저에 httpOnly 쿠키 설정
   │  Set-Cookie: session_token=6eef3a78-...; HttpOnly; SameSite=Lax; Path=/
   │
5. 이후 모든 요청에 쿠키가 자동으로 포함됨
   │  Cookie: session_token=6eef3a78-...
   │
6. 서버에서 쿠키의 토큰으로 Redis 조회 → 사용자 정보 반환
   │
7. 로그아웃 시: Redis에서 삭제 + 쿠키 제거
```

### 3.3 핵심 코드 분석

#### 세션 생성 (`auth.py` - `_create_session`)

```python
def _create_session(response: Response, user_data: dict) -> str:
    """로그인 성공 후 Redis에 세션을 생성하고 쿠키를 발급합니다."""
    settings = get_settings()
    
    # 1) 고유한 세션 토큰 생성 (UUID v4)
    token = str(uuid.uuid4())
    
    # 2) 사용자 정보를 JSON 직렬화
    session_data = json.dumps(user_data, default=str, ensure_ascii=False)

    # 3) Redis에 저장 (SETEX = SET + EXPIRE)
    redis_client = get_redis()
    redis_client.setex(
        f"session:{token}",                    # Key: "session:{uuid}"
        settings.SESSION_EXPIRE_HOURS * 3600,  # TTL: 24시간 (초 단위)
        session_data,                          # Value: JSON 문자열
    )

    # 4) 브라우저에 httpOnly 쿠키 설정
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,       # ⭐ JavaScript에서 접근 불가 (XSS 방어)
        max_age=settings.SESSION_EXPIRE_HOURS * 3600,
        samesite="lax",      # ⭐ CSRF 방어
        path="/",
    )
    return token
```

#### 세션 검증 (`auth.py` - `GET /api/auth/me`)

```python
@router.get("/me")
async def get_current_user(request: Request):
    """현재 로그인된 사용자 정보를 반환합니다."""
    
    # 1) 쿠키에서 세션 토큰 추출
    token = request.cookies.get("session_token")
    if not token:
        return {"success": False, "message": "로그인이 필요합니다", "user": None}

    # 2) Redis에서 세션 데이터 조회
    redis_client = get_redis()
    session_data = redis_client.get(f"session:{token}")
    
    if not session_data:
        # TTL 만료되었거나 세션이 없음
        return {"success": False, "message": "세션이 만료되었습니다", "user": None}

    # 3) JSON 파싱 후 사용자 정보 반환
    user = json.loads(session_data)
    return {"success": True, "user": user}
```

#### 세션 삭제 (`auth.py` - `POST /api/auth/logout`)

```python
@router.post("/logout")
async def logout(request: Request, response: Response):
    """로그아웃 - Redis 세션 삭제 + 쿠키 제거"""
    token = request.cookies.get("session_token")
    
    if token:
        # 1) Redis에서 세션 삭제
        redis_client = get_redis()
        redis_client.delete(f"session:{token}")

    # 2) 브라우저 쿠키 제거
    response.delete_cookie(key="session_token", path="/")
    return {"success": True, "message": "로그아웃 되었습니다"}
```

### 3.4 Redis 세션 데이터 구조

```
┌─────────────────────────────────────────────────────────────┐
│ Redis Database                                              │
├─────────────────────────────────────┬───────────────────────┤
│ Key                                 │ Value (JSON)          │
├─────────────────────────────────────┼───────────────────────┤
│ session:6eef3a78-2bdd-4f90-...     │ {                     │
│   TTL: 86400초 (24시간)             │   "user_id": "goo_8a",│
│                                     │   "name": "Daniel",   │
│                                     │   "email": "d@g.com", │
│                                     │   "role": "USER",     │
│                                     │   "provider": "google"│
│                                     │   "profile_image":... │
│                                     │ }                     │
├─────────────────────────────────────┼───────────────────────┤
│ session:cedbec43-35d3-406c-...     │ { ... 다른 사용자 ... } │
│   TTL: 86400초                      │                       │
└─────────────────────────────────────┴───────────────────────┘
```

### 3.5 Redis CLI로 세션 확인하기

```bash
# Redis 컨테이너에 접속
docker exec -it redis-main redis-cli -a DataPass2024!

# 모든 세션 키 조회
KEYS session:*

# 특정 세션 데이터 확인
GET session:6eef3a78-2bdd-4f90-9675-4ca9a54009d2

# 세션 남은 TTL 확인 (초)
TTL session:6eef3a78-2bdd-4f90-9675-4ca9a54009d2

# 세션 수 조회
DBSIZE
```

### 3.6 보안 설계

| 보안 항목 | 구현 방법 | 설명 |
|-----------|---------|------|
| **XSS 방어** | `httponly=True` | JavaScript에서 쿠키 접근 차단 |
| **CSRF 방어** | `samesite="lax"` | 외부 사이트에서의 쿠키 전송 제한 |
| **세션 하이재킹 방지** | UUID v4 토큰 | 예측 불가능한 랜덤 토큰 사용 |
| **자동 만료** | Redis TTL | 24시간 후 세션 자동 삭제 |
| **비밀번호 보호** | bcrypt 해싱 | 단방향 해시로 저장 |

---

## 4. 인증 플로우

### 4.1 이메일 회원가입

```
브라우저                    FastAPI                     PostgreSQL       Redis
  │                          │                            │               │
  │── POST /api/auth/register ──►                         │               │
  │   {email, password,      │                            │               │
  │    password_confirm, name}│                            │               │
  │                          │                            │               │
  │                          │── 이메일 중복 확인 ──────────►│               │
  │                          │◄── 결과 ──────────────────── │               │
  │                          │                            │               │
  │                          │── bcrypt 해싱 ──►           │               │
  │                          │── INSERT users ────────────►│               │
  │                          │                            │               │
  │                          │── 세션 생성 ─────────────────────────────────►│
  │                          │                            │               │
  │◄── Set-Cookie + 사용자 정보 │                            │               │
```

### 4.2 소셜 로그인 (OAuth 2.0)

```
브라우저          FastAPI           OAuth 제공자          PostgreSQL    Redis
  │                │                    │                   │           │
  │── GET /oauth/  │                    │                   │           │
  │   google/login │                    │                   │           │
  │                │                    │                   │           │
  │◄── 302 Redirect to Google ─────────►│                   │           │
  │                │                    │                   │           │
  │── 사용자가 Google에서 로그인/동의 ───►│                   │           │
  │                │                    │                   │           │
  │◄── 302 Redirect + code ◄───────────│                   │           │
  │                │                    │                   │           │
  │── GET /oauth/google/callback?code=  │                   │           │
  │                │                    │                   │           │
  │                │── POST token_url ──►│                   │           │
  │                │◄── access_token ───│                   │           │
  │                │                    │                   │           │
  │                │── GET userinfo_url ►│                   │           │
  │                │◄── 사용자 정보 ─────│                   │           │
  │                │                    │                   │           │
  │                │── UPSERT user ─────────────────────────►│           │
  │                │── 세션 생성 ──────────────────────────────────────── ►│
  │                │                    │                   │           │
  │◄── 302 Redirect to / + Set-Cookie   │                   │           │
```

---

## 5. DB 스키마

### users 테이블

```sql
CREATE TABLE IF NOT EXISTS users (
    user_id       VARCHAR(50)  PRIMARY KEY,       -- 시스템 생성 ID
    password      VARCHAR(255),                    -- bcrypt 해시 (소셜 로그인은 NULL)
    name          VARCHAR(50),                     -- 사용자 이름/닉네임
    email         VARCHAR(100) UNIQUE,             -- 이메일 (카카오는 NULL 가능)
    role          VARCHAR(20)  DEFAULT 'USER',     -- 역할 (USER/ADMIN)
    provider      VARCHAR(20)  DEFAULT 'email',    -- 로그인 제공자
    social_id     VARCHAR(255),                    -- 소셜 제공자의 고유 ID
    profile_image VARCHAR(512),                    -- 프로필 이미지 URL
    last_login    TIMESTAMP    DEFAULT NOW(),
    create_dt     TIMESTAMP    DEFAULT NOW(),
    update_dt     TIMESTAMP    DEFAULT NOW()
);

-- 소셜 로그인 사용자를 고유하게 식별하는 인덱스
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_social ON users(provider, social_id);
```

### 사용자 식별 방식

| 로그인 방식 | user_id 생성 | 고유 식별 |
|-----------|-------------|---------|
| **Email** | `{email앞부분}_{uuid4자리}` | `email` 컬럼 UNIQUE |
| **Google** | `google_{uuid4자리}` | `provider='google'` + `social_id` |
| **Naver** | `naver_{uuid4자리}` | `provider='naver'` + `social_id` |
| **Kakao** | `kakao_{uuid4자리}` | `provider='kakao'` + `social_id` |

---

## 6. OAuth 소셜 로그인

### 6.1 제공자별 설정

| 항목 | Google | Naver | Kakao |
|------|--------|-------|-------|
| **Auth URL** | accounts.google.com/o/oauth2/v2/auth | nid.naver.com/oauth2.0/authorize | kauth.kakao.com/oauth/authorize |
| **Token URL** | oauth2.googleapis.com/token | nid.naver.com/oauth2.0/token | kauth.kakao.com/oauth/token |
| **UserInfo URL** | googleapis.com/oauth2/v2/userinfo | openapi.naver.com/v1/nid/me | kapi.kakao.com/v2/user/me |
| **Scope** | openid email profile | *(없음)* | profile_nickname profile_image |
| **이메일 제공** | ✅ 항상 | ✅ 항상 | ❌ 비즈앱만 |

### 6.2 제공자별 사용자 정보 파싱

```python
# Google: 응답 최상위에 정보
{"id": "12345", "name": "Daniel", "email": "d@g.com", "picture": "https://..."}

# Naver: response 객체 안에 정보
{"response": {"id": "abc123", "name": "한대성", "email": "u@h.net", "profile_image": "..."}}

# Kakao: kakao_account > profile 안에 정보
{"id": 9876, "kakao_account": {"profile": {"nickname": "한대성", "profile_image_url": "..."}}}
```

### 6.3 OAuth 설정 방법 (개발자 콘솔)

#### Google Cloud Console
1. https://console.cloud.google.com/apis/credentials
2. OAuth 2.0 클라이언트 ID 생성
3. 승인된 리디렉션 URI: `http://localhost:8900/api/auth/oauth/google/callback`
4. 승인된 JavaScript 출처: `http://localhost:8900`

#### Naver Developers
1. https://developers.naver.com/apps
2. 애플리케이션 등록 → 네이버 로그인 API 추가
3. Callback URL: `http://localhost:8900/api/auth/oauth/naver/callback`

#### Kakao Developers
1. https://developers.kakao.com/console/app
2. 앱 생성 → 카카오 로그인 활성화
3. Redirect URI: `http://localhost:8900/api/auth/oauth/kakao/callback`
4. **동의항목 설정**: profile_nickname(필수), profile_image(선택)
5. ⚠️ account_email은 **비즈니스 앱 전환** 필요

---

## 7. 환경 변수 설정

`.env` 파일에 다음 변수들이 필요합니다:

```env
# ── DB 설정 ──
POSTGRES_HOST=localhost       # Docker: postgresql
POSTGRES_PORT=5432
POSTGRES_DB=datadb
POSTGRES_USER=datauser
POSTGRES_PASSWORD=DataPass2024!

REDIS_HOST=localhost          # Docker: redis
REDIS_PORT=6379
REDIS_PASSWORD=DataPass2024!

MONGODB_HOST=localhost        # Docker: mongodb
MONGODB_PORT=27017

# ── FastAPI 설정 ──
FASTAPI_PORT=8900

# ── 세션 설정 ──
SESSION_EXPIRE_HOURS=24       # 세션 만료 시간 (시간)

# ── OAuth 설정 ──
GOOGLE_CLIENT_ID=xxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxx

NAVER_CLIENT_ID=xxxx
NAVER_CLIENT_SECRET=xxxx

KAKAO_CLIENT_ID=xxxx
KAKAO_CLIENT_SECRET=xxxx
```

---

## 8. API 엔드포인트

| Method | Path | 설명 | 인증 필요 |
|--------|------|------|----------|
| `POST` | `/api/auth/register` | 이메일 회원가입 | ❌ |
| `POST` | `/api/auth/login` | 이메일 로그인 | ❌ |
| `POST` | `/api/auth/logout` | 로그아웃 | ✅ |
| `GET` | `/api/auth/me` | 현재 사용자 정보 | ✅ |
| `PUT` | `/api/auth/profile` | 프로필 수정 | ✅ |
| `GET` | `/api/auth/oauth/providers` | 활성 OAuth 제공자 목록 | ❌ |
| `GET` | `/api/auth/oauth/{provider}/login` | 소셜 로그인 시작 | ❌ |
| `GET` | `/api/auth/oauth/{provider}/callback` | OAuth 콜백 처리 | ❌ |

---

## 9. 프론트엔드 연동

### 세션 확인 (페이지 로드 시)

```javascript
// script.js - 페이지 로드 시 자동 호출
async function checkLoginStatus() {
    const resp = await fetch('/api/auth/me', { credentials: 'same-origin' });
    const data = await resp.json();
    
    if (data.success && data.user) {
        updateUIForLoggedIn(data.user);   // 프로필 드롭다운 표시
    } else {
        updateUIForLoggedOut();            // 로그인 버튼 표시
    }
}
```

### 소셜 로그인 버튼

```javascript
// 소셜 로그인 버튼 클릭 → 서버로 리다이렉트 → OAuth 제공자로 리다이렉트
function startSocialLogin(provider) {
    window.location.href = `/api/auth/oauth/${provider}/login`;
}
```

---

## 10. 트러블슈팅

### 해결된 이슈 기록

| 이슈 | 원인 | 해결 |
|------|------|------|
| `passlib` + `bcrypt` 에러 | `passlib 1.7.4`와 `bcrypt 5.x` 비호환 | `bcrypt`를 직접 사용하도록 변경 |
| 기존 유저 로그인 실패 | 비밀번호가 SHA256으로 저장됨 | bcrypt 해시로 업데이트 |
| 카카오 KOE205 에러 | 동의항목 미설정 | scope에서 `account_email` 제거 + 동의항목 설정 |
| Google redirect_uri_mismatch | 콘솔에 콜백 URL 미등록 | Google Cloud Console에서 redirect URI 추가 |
| OAuth 콜백 에러 시 500 | `code` 파라미터가 required | `code`를 Optional로 변경, 에러 핸들링 추가 |
| 포트 8900 충돌 | VS Code 포트 포워딩 | VS Code Ports 패널에서 포워딩 해제 |

### 새 PC / 서버에서 처음 세팅하기

새 환경에서는 아래 순서대로 진행합니다.

```bash
# 1) 프로젝트 클론
git clone <repo-url>
cd main-project-lookalike

# 2) .env 파일 설정 (OAuth 키 포함)

# 3) Docker 서비스 시작 (DB + Redis 자동 초기화)
docker compose up -d postgresql mongodb redis init-db

# 4) Conda 환경 활성화 후 패키지 설치
conda activate ml-env
pip install -r web/backend/requirements.txt

# 5) 서버 실행
POSTGRES_HOST=localhost MONGODB_HOST=localhost REDIS_HOST=localhost \
python -m uvicorn web.backend.app.main:app --host 0.0.0.0 --port 8900 --reload
```

> ✅ `docker-compose.yml`의 `init-db` 서비스에 최신 스키마가 포함되어 있어,
> `CREATE TABLE IF NOT EXISTS`로 OAuth 컬럼(`provider`, `social_id`, `profile_image`)이
> 포함된 테이블이 자동 생성됩니다.

> ⚠️ **주의**: `bcrypt==4.1.2`로 버전이 고정되어 있습니다. `bcrypt 5.x`는 호환 문제가 있으므로
> 반드시 `requirements.txt`를 통해 설치하세요.

### 기존 DB 마이그레이션 (구 스키마 → 신 스키마)

기존 PC에서 이미 구 스키마의 `users` 테이블이 있는 경우, `CREATE TABLE IF NOT EXISTS`는
기존 테이블을 수정하지 않습니다. 이 경우 아래 SQL을 수동으로 실행해야 합니다:

```sql
-- 기존 users 테이블에 OAuth 컬럼 추가
ALTER TABLE users ADD COLUMN IF NOT EXISTS provider VARCHAR(20) DEFAULT 'email';
ALTER TABLE users ADD COLUMN IF NOT EXISTS social_id VARCHAR(255);
ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_image VARCHAR(512);

-- password 컬럼의 NOT NULL 제약 해제 (소셜 로그인은 비밀번호 없음)
ALTER TABLE users ALTER COLUMN password DROP NOT NULL;

-- 소셜 로그인 유저를 고유하게 식별하는 인덱스
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_social ON users(provider, social_id);
```

실행 방법:

```bash
# Docker PostgreSQL에서 실행
docker exec -it postgres-main psql -U datauser -d datadb

# 또는 한 줄로 실행
docker exec postgres-main psql -U datauser -d datadb -c "
  ALTER TABLE users ADD COLUMN IF NOT EXISTS provider VARCHAR(20) DEFAULT 'email';
  ALTER TABLE users ADD COLUMN IF NOT EXISTS social_id VARCHAR(255);
  ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_image VARCHAR(512);
  ALTER TABLE users ALTER COLUMN password DROP NOT NULL;
  CREATE UNIQUE INDEX IF NOT EXISTS idx_users_social ON users(provider, social_id);
"
```

### 로컬 서버 실행 명령

```bash
# 프로젝트 루트에서 실행
cd ~/dev/data-engineer/main-project-lookalike

# Conda 환경 활성화
conda activate ml-env

# 서버 시작
POSTGRES_HOST=localhost MONGODB_HOST=localhost REDIS_HOST=localhost \
python -m uvicorn web.backend.app.main:app --host 0.0.0.0 --port 8900 --reload
```

### 필수 패키지 (requirements.txt)

패키지는 `web/backend/requirements.txt`로 관리됩니다:

```bash
# Conda 환경에서 설치
conda activate ml-env
pip install -r web/backend/requirements.txt
```

> ⚠️ `passlib`은 사용하지 않음 — `bcrypt`를 직접 사용  
> ⚠️ `bcrypt`는 반드시 **4.1.2** 버전 사용 (5.x 호환 문제)

