"""
인증 라우터 - 소셜 로그인(구글/네이버/카카오) + 이메일 회원가입/로그인
Redis 세션 기반 인증 관리
"""
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from datetime import datetime
import hashlib
import uuid
import json
import logging

import httpx
from passlib.context import CryptContext

from ..database import get_pg_cursor, get_redis
from ..models.user import (
    UserRegisterRequest,
    UserLoginRequest,
    UserUpdateRequest,
    UserResponse,
    LoginResponse,
    OAuthConfigResponse,
)
from ..config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["인증"])

# ──────────────────────────────────────
# 비밀번호 해싱 (bcrypt)
# ──────────────────────────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _hash_password(password: str) -> str:
    """bcrypt 비밀번호 해싱"""
    return pwd_context.hash(password)


def _verify_password(plain: str, hashed: str) -> bool:
    """bcrypt 비밀번호 검증"""
    return pwd_context.verify(plain, hashed)


# ──────────────────────────────────────
# Redis 세션 관리
# ──────────────────────────────────────
def _create_session(response: Response, user_data: dict) -> str:
    """Redis에 세션을 생성하고 쿠키에 토큰을 설정"""
    settings = get_settings()
    token = str(uuid.uuid4())
    session_data = json.dumps(user_data, default=str, ensure_ascii=False)

    try:
        redis_client = get_redis()
        redis_client.setex(
            f"session:{token}",
            settings.SESSION_EXPIRE_HOURS * 3600,
            session_data,
        )
    except Exception as e:
        logger.warning(f"Redis 세션 저장 실패 (fallback 없음): {e}")
        raise HTTPException(status_code=500, detail="세션 생성 실패")

    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        max_age=settings.SESSION_EXPIRE_HOURS * 3600,
        samesite="lax",
        path="/",
    )
    return token


def _get_session(request: Request) -> dict | None:
    """쿠키에서 토큰을 읽어 Redis 세션 데이터 반환"""
    token = request.cookies.get("session_token")
    if not token:
        return None

    try:
        redis_client = get_redis()
        data = redis_client.get(f"session:{token}")
        if data:
            return json.loads(data)
    except Exception as e:
        logger.warning(f"Redis 세션 조회 실패: {e}")

    return None


def _delete_session(request: Request, response: Response):
    """Redis 세션 삭제 + 쿠키 삭제"""
    token = request.cookies.get("session_token")
    if token:
        try:
            redis_client = get_redis()
            redis_client.delete(f"session:{token}")
        except Exception as e:
            logger.warning(f"Redis 세션 삭제 실패: {e}")

    response.delete_cookie(key="session_token", path="/")


def _user_row_to_dict(row: dict) -> dict:
    """DB 행을 세션 저장용 딕셔너리로 변환"""
    return {
        "user_id": row["user_id"],
        "name": row.get("name"),
        "email": row.get("email"),
        "role": row.get("role", "USER"),
        "provider": row.get("provider", "email"),
        "profile_image": row.get("profile_image"),
    }


# ──────────────────────────────────────
# 이메일 회원가입
# ──────────────────────────────────────
@router.post("/register", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
async def register(req: UserRegisterRequest, response: Response):
    """이메일 회원가입"""
    # 비밀번호 확인
    if req.password != req.password_confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="비밀번호가 일치하지 않습니다",
        )

    try:
        # user_id 생성: 이메일의 @ 앞부분 + 랜덤 4자리
        email_prefix = req.email.split("@")[0]
        user_id = f"{email_prefix}_{uuid.uuid4().hex[:4]}"

        with get_pg_cursor() as cur:
            # 이메일 중복 확인
            cur.execute("SELECT user_id FROM users WHERE email = %s", (req.email,))
            existing = cur.fetchone()
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="이미 사용 중인 이메일입니다",
                )

            # 사용자 등록
            cur.execute(
                """
                INSERT INTO users (user_id, password, name, email, provider)
                VALUES (%s, %s, %s, %s, 'email')
                RETURNING user_id, name, email, role, provider, profile_image, create_dt
                """,
                (user_id, _hash_password(req.password), req.name, req.email),
            )
            row = cur.fetchone()

        user_data = _user_row_to_dict(row)
        _create_session(response, user_data)

        return LoginResponse(
            success=True,
            message="회원가입이 완료되었습니다",
            user=UserResponse(**row),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"회원가입 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류가 발생했습니다")


# ──────────────────────────────────────
# 이메일 로그인
# ──────────────────────────────────────
@router.post("/login", response_model=LoginResponse)
async def login(req: UserLoginRequest, response: Response):
    """이메일 로그인"""
    try:
        with get_pg_cursor() as cur:
            cur.execute(
                """
                SELECT user_id, name, email, role, provider, profile_image,
                       last_login, create_dt, password
                FROM users WHERE email = %s AND provider = 'email'
                """,
                (req.email,),
            )
            row = cur.fetchone()

            if not row:
                return LoginResponse(success=False, message="존재하지 않는 이메일입니다")

            if not row["password"] or not _verify_password(req.password, row["password"]):
                return LoginResponse(success=False, message="비밀번호가 일치하지 않습니다")

            # 마지막 로그인 시간 갱신
            cur.execute(
                "UPDATE users SET last_login = NOW() WHERE user_id = %s",
                (row["user_id"],),
            )

        user_data = _user_row_to_dict(row)
        _create_session(response, user_data)

        return LoginResponse(
            success=True,
            message="로그인 성공",
            user=UserResponse(**{k: v for k, v in row.items() if k != "password"}),
        )

    except Exception as e:
        logger.error(f"로그인 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류가 발생했습니다")


# ──────────────────────────────────────
# 로그아웃
# ──────────────────────────────────────
@router.post("/logout")
async def logout(request: Request, response: Response):
    """로그아웃 - Redis 세션 삭제 + 쿠키 삭제"""
    _delete_session(request, response)
    return {"success": True, "message": "로그아웃 되었습니다"}


# ──────────────────────────────────────
# 현재 로그인 사용자 정보
# ──────────────────────────────────────
@router.get("/me", response_model=LoginResponse)
async def get_current_user(request: Request):
    """현재 로그인한 사용자 정보 조회 (세션 기반)"""
    session = _get_session(request)
    if not session:
        return LoginResponse(success=False, message="로그인이 필요합니다")

    return LoginResponse(
        success=True,
        message="인증된 사용자",
        user=UserResponse(**session),
    )


# ──────────────────────────────────────
# OAuth 제공자 활성화 상태
# ──────────────────────────────────────
@router.get("/oauth/providers", response_model=OAuthConfigResponse)
async def get_oauth_providers():
    """활성화된 OAuth 제공자 목록 조회"""
    settings = get_settings()
    return OAuthConfigResponse(
        google=settings.is_oauth_configured("google"),
        naver=settings.is_oauth_configured("naver"),
        kakao=settings.is_oauth_configured("kakao"),
    )


# ──────────────────────────────────────
# OAuth2: 소셜 로그인 시작 (→ 제공자로 리다이렉트)
# ──────────────────────────────────────
OAUTH_CONFIGS = {
    "google": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://www.googleapis.com/oauth2/v2/userinfo",
        "scope": "openid email profile",
    },
    "naver": {
        "auth_url": "https://nid.naver.com/oauth2.0/authorize",
        "token_url": "https://nid.naver.com/oauth2.0/token",
        "userinfo_url": "https://openapi.naver.com/v1/nid/me",
        "scope": "",
    },
    "kakao": {
        "auth_url": "https://kauth.kakao.com/oauth/authorize",
        "token_url": "https://kauth.kakao.com/oauth/token",
        "userinfo_url": "https://kapi.kakao.com/v2/user/me",
        "scope": "profile_nickname profile_image account_email",
    },
}


@router.get("/oauth/{provider}")
async def oauth_login(provider: str):
    """소셜 로그인 시작 - OAuth 제공자로 리다이렉트"""
    settings = get_settings()

    if provider not in OAUTH_CONFIGS:
        raise HTTPException(status_code=400, detail="지원하지 않는 OAuth 제공자입니다")

    if not settings.is_oauth_configured(provider):
        raise HTTPException(status_code=400, detail=f"{provider} 로그인이 설정되지 않았습니다")

    config = OAUTH_CONFIGS[provider]
    redirect_uri = f"{settings.OAUTH_REDIRECT_BASE}/api/auth/oauth/{provider}/callback"

    # 클라이언트 ID 가져오기
    client_id = getattr(settings, f"{provider.upper()}_CLIENT_ID")

    # state 파라미터 (CSRF 방지)
    state = uuid.uuid4().hex

    try:
        redis_client = get_redis()
        redis_client.setex(f"oauth_state:{state}", 600, provider)
    except Exception:
        pass

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
    }

    if config["scope"]:
        params["scope"] = config["scope"]

    # 네이버는 별도 파라미터
    if provider == "naver":
        params["response_type"] = "code"

    query = "&".join(f"{k}={v}" for k, v in params.items())
    return RedirectResponse(url=f"{config['auth_url']}?{query}")


# ──────────────────────────────────────
# OAuth2: 콜백 처리
# ──────────────────────────────────────
@router.get("/oauth/{provider}/callback")
async def oauth_callback(provider: str, code: str, state: str = "", response: Response = None):
    """OAuth 콜백 - 토큰 교환 → 사용자 정보 조회 → 로그인/가입"""
    settings = get_settings()

    if provider not in OAUTH_CONFIGS:
        raise HTTPException(status_code=400, detail="지원하지 않는 OAuth 제공자입니다")

    config = OAUTH_CONFIGS[provider]
    redirect_uri = f"{settings.OAUTH_REDIRECT_BASE}/api/auth/oauth/{provider}/callback"
    client_id = getattr(settings, f"{provider.upper()}_CLIENT_ID")
    client_secret = getattr(settings, f"{provider.upper()}_CLIENT_SECRET")

    # 1. 인가 코드 → 액세스 토큰 교환
    token_data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "code": code,
    }

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(config["token_url"], data=token_data)

        if token_resp.status_code != 200:
            logger.error(f"OAuth 토큰 교환 실패 ({provider}): {token_resp.text}")
            return RedirectResponse(url="/?error=oauth_failed")

        token_json = token_resp.json()
        access_token = token_json.get("access_token")

        if not access_token:
            logger.error(f"액세스 토큰 없음 ({provider}): {token_json}")
            return RedirectResponse(url="/?error=oauth_failed")

        # 2. 액세스 토큰 → 사용자 정보 조회
        headers = {"Authorization": f"Bearer {access_token}"}
        userinfo_resp = await client.get(config["userinfo_url"], headers=headers)

        if userinfo_resp.status_code != 200:
            logger.error(f"사용자 정보 조회 실패 ({provider}): {userinfo_resp.text}")
            return RedirectResponse(url="/?error=oauth_failed")

        userinfo = userinfo_resp.json()

    # 3. 제공자별 사용자 정보 파싱
    social_id, name, email, profile_image = _parse_oauth_userinfo(provider, userinfo)

    if not social_id:
        logger.error(f"소셜 ID 추출 실패 ({provider}): {userinfo}")
        return RedirectResponse(url="/?error=oauth_failed")

    # 4. DB Upsert (기존 사용자면 업데이트, 신규면 생성)
    try:
        with get_pg_cursor() as cur:
            # 기존 사용자 조회
            cur.execute(
                "SELECT * FROM users WHERE provider = %s AND social_id = %s",
                (provider, social_id),
            )
            existing = cur.fetchone()

            if existing:
                # 기존 사용자 → 로그인 시간 갱신
                cur.execute(
                    """
                    UPDATE users SET last_login = NOW(), name = COALESCE(%s, name),
                           profile_image = COALESCE(%s, profile_image)
                    WHERE provider = %s AND social_id = %s
                    RETURNING user_id, name, email, role, provider, profile_image, create_dt
                    """,
                    (name, profile_image, provider, social_id),
                )
                row = cur.fetchone()
            else:
                # 신규 사용자 → 생성
                user_id = f"{provider}_{uuid.uuid4().hex[:8]}"
                cur.execute(
                    """
                    INSERT INTO users (user_id, name, email, provider, social_id, profile_image)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING user_id, name, email, role, provider, profile_image, create_dt
                    """,
                    (user_id, name, email, provider, social_id, profile_image),
                )
                row = cur.fetchone()

        # 5. 세션 생성 + 홈으로 리다이렉트
        user_data = _user_row_to_dict(row)
        redirect = RedirectResponse(url="/", status_code=302)
        _create_session(redirect, user_data)
        return redirect

    except Exception as e:
        logger.error(f"OAuth 사용자 처리 실패 ({provider}): {e}")
        return RedirectResponse(url="/?error=oauth_failed")


def _parse_oauth_userinfo(provider: str, info: dict) -> tuple:
    """제공자별 사용자 정보 파싱 → (social_id, name, email, profile_image)"""
    if provider == "google":
        return (
            info.get("id"),
            info.get("name"),
            info.get("email"),
            info.get("picture"),
        )
    elif provider == "naver":
        resp = info.get("response", {})
        return (
            resp.get("id"),
            resp.get("name") or resp.get("nickname"),
            resp.get("email"),
            resp.get("profile_image"),
        )
    elif provider == "kakao":
        kakao_account = info.get("kakao_account", {})
        profile = kakao_account.get("profile", {})
        return (
            str(info.get("id")),
            profile.get("nickname"),
            kakao_account.get("email"),
            profile.get("profile_image_url"),
        )
    return (None, None, None, None)


# ──────────────────────────────────────
# 사용자 정보 조회
# ──────────────────────────────────────
@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(user_id: str):
    """사용자 정보 조회"""
    try:
        with get_pg_cursor() as cur:
            cur.execute(
                """
                SELECT user_id, name, email, role, provider, profile_image,
                       last_login, create_dt
                FROM users WHERE user_id = %s
                """,
                (user_id,),
            )
            row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")

        return UserResponse(**row)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"사용자 조회 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# 사용자 정보 수정
# ──────────────────────────────────────
@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(user_id: str, req: UserUpdateRequest):
    """사용자 정보 수정"""
    try:
        updates = []
        values = []

        if req.name is not None:
            updates.append("name = %s")
            values.append(req.name)
        if req.email is not None:
            updates.append("email = %s")
            values.append(req.email)
        if req.profile_image is not None:
            updates.append("profile_image = %s")
            values.append(req.profile_image)

        if not updates:
            raise HTTPException(status_code=400, detail="수정할 항목이 없습니다")

        updates.append("update_dt = NOW()")
        values.append(user_id)

        with get_pg_cursor() as cur:
            cur.execute(
                f"""
                UPDATE users SET {', '.join(updates)}
                WHERE user_id = %s
                RETURNING user_id, name, email, role, provider, profile_image,
                          last_login, create_dt
                """,
                values,
            )
            row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")

        return UserResponse(**row)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"사용자 수정 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")
