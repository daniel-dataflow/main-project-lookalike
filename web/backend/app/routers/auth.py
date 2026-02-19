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
import bcrypt

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
# 비밀번호 해싱 (bcrypt 직접 사용)
# ──────────────────────────────────────


def _hash_password(password: str) -> str:
    """bcrypt 비밀번호 해싱"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(plain: str, hashed: str) -> bool:
    """bcrypt 비밀번호 검증"""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


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
    """현재 로그인한 사용자 정보 조회 (Redis 세션에서 직접 반환)
    
    [성능 최적화] 기존: Redis 세션 조회 → PostgreSQL DB 쿼리
                 변경: Redis 세션 데이터를 그대로 반환 (DB 쿼리 제거)
    - 세션에 name/email/role 등 필요한 정보가 이미 저장되어 있음
    - 로그아웃/재로그인 시 세션이 갱신되므로 정합성 문제 없음
    """
    session = _get_session(request)
    if not session:
        return LoginResponse(success=False, message="로그인이 필요합니다")

    return LoginResponse(
        success=True,
        message="인증된 사용자",
        user=UserResponse(
            user_id=session.get("user_id", ""),
            name=session.get("name"),
            email=session.get("email"),
            role=session.get("role", "USER"),
            provider=session.get("provider", "email"),
            profile_image=session.get("profile_image"),
            last_login=None,
            create_dt=None,
        ),
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
        "scope": "profile_nickname profile_image",
    },
}


@router.get("/oauth/{provider}")
async def oauth_login(provider: str, request: Request):
    """소셜 로그인 시작 - OAuth 제공자로 리다이렉트"""
    settings = get_settings()

    if provider not in OAUTH_CONFIGS:
        raise HTTPException(status_code=400, detail="지원하지 않는 OAuth 제공자입니다")

    if not settings.is_oauth_configured(provider):
        raise HTTPException(status_code=400, detail=f"{provider} 로그인이 설정되지 않았습니다")

    config = OAUTH_CONFIGS[provider]
    
    # 동적 리다이렉트 URI 생성 (접속한 도메인 기준)
    # request.url_for는 http/https 스킴을 자동으로 처리함 (프록시 설정 필요할 수 있음)
    redirect_uri = str(request.url_for("oauth_callback", provider=provider))
    
    # Docker/Nginx 환경에서 scheme이 반영되지 않을 경우를 대비해 강제로 설정이 필요할 수도 있음.
    # 여기서는 일단 request.url_for 결과를 신뢰하되, 외부 접속 문제 해결을 위해 
    # request.headers["host"]를 사용하여 직접 구성하는 방식을 혼용할 수도 있으나
    # 가장 표준적인 request.url_for를 우선 사용.
    
    # 만약 http/https 문제가 발생하면 아래와 같이 직접 구성:
    # scheme = request.headers.get("x-forwarded-proto", "http")
    # host = request.headers.get("host")
    # redirect_uri = f"{scheme}://{host}/api/auth/oauth/{provider}/callback"

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
async def oauth_callback(
    provider: str,
    request: Request,
    code: str = None,
    state: str = "",
    error: str = None,
    error_description: str = None,
    response: Response = None,
):
    """OAuth 콜백 - 토큰 교환 → 사용자 정보 조회 → 로그인/가입"""
    settings = get_settings()

    # OAuth 제공자가 에러를 반환한 경우
    if error or not code:
        logger.warning(f"OAuth 콜백 에러 ({provider}): error={error}, desc={error_description}")
        return RedirectResponse(url="/?error=oauth_failed")

    if provider not in OAUTH_CONFIGS:
        raise HTTPException(status_code=400, detail="지원하지 않는 OAuth 제공자입니다")

    config = OAUTH_CONFIGS[provider]
    
    # 토큰 교환 시에도 동일한 redirect_uri를 보내야 함
    redirect_uri = str(request.url_for("oauth_callback", provider=provider))
    
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
    provider_id, name, email, profile_image = _parse_oauth_userinfo(provider, userinfo)

    if not provider_id:
        logger.error(f"소셜 ID 추출 실패 ({provider}): {userinfo}")
        return RedirectResponse(url="/?error=oauth_failed")

    # 4. DB Upsert (기존 사용자면 업데이트, 신규면 생성)
    try:
        with get_pg_cursor() as cur:
            # 기존 사용자 조회
            cur.execute(
                "SELECT * FROM users WHERE provider = %s AND provider_id = %s",
                (provider, provider_id),
            )
            existing = cur.fetchone()

            if existing:
                # 기존 사용자 → 로그인 시간 갱신
                cur.execute(
                    """
                    UPDATE users SET last_login = NOW(), name = COALESCE(%s, name),
                           profile_image = COALESCE(%s, profile_image)
                    WHERE provider = %s AND provider_id = %s
                    RETURNING user_id, name, email, role, provider, profile_image, create_dt
                    """,
                    (name, profile_image, provider, provider_id),
                )
                row = cur.fetchone()
            else:
                # 신규 사용자 → 생성
                user_id = f"{provider}_{uuid.uuid4().hex[:8]}"
                cur.execute(
                    """
                    INSERT INTO users (user_id, name, email, provider, provider_id, profile_image)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING user_id, name, email, role, provider, profile_image, create_dt
                    """,
                    (user_id, name, email, provider, provider_id, profile_image),
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
    """제공자별 사용자 정보 파싱 → (provider_id, name, email, profile_image)"""
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
async def update_user(user_id: str, req: UserUpdateRequest, request: Request, response: Response):
    """사용자 정보 수정 (비밀번호 변경만 가능, 이메일 사용자 전용)"""
    # 1. 세션 확인 (로그인 여부)
    session = _get_session(request)
    if not session or session.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="권한이 없습니다")

    try:
        with get_pg_cursor() as cur:
            # 2. 현재 사용자 정보 조회
            cur.execute(
                "SELECT * FROM users WHERE user_id = %s",
                (user_id,),
            )
            current_user = cur.fetchone()
            if not current_user:
                raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")

            # 소셜 로그인 사용자는 수정 불가
            if current_user["provider"] != "email":
                raise HTTPException(status_code=403, detail="소셜 로그인 사용자는 회원정보를 수정할 수 없습니다")

            updates = []
            values = []

            # 3. 비밀번호 변경 로직
            if req.new_password:
                if not req.current_password:
                    raise HTTPException(status_code=400, detail="현재 비밀번호를 입력해주세요")
                
                if not _verify_password(req.current_password, current_user["password"]):
                    raise HTTPException(status_code=400, detail="현재 비밀번호가 일치하지 않습니다")
                
                updates.append("password = %s")
                values.append(_hash_password(req.new_password))
            
            # 이름, 이메일, 프로필 이미지 변경은 허용하지 않음
            pass

            if not updates:
                raise HTTPException(status_code=400, detail="수정할 내용이 없습니다 (비밀번호 변경만 가능합니다)")

            updates.append("update_dt = NOW()")
            values.append(user_id)

            # 4. DB 업데이트
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

            # 5. 비밀번호 변경 후 로그아웃 처리 (세션 삭제)
            # 보안을 위해 비밀번호 변경 시 기존 세션을 만료시킵니다.
            _delete_session(request, response)

        return UserResponse(**row)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"사용자 수정 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# 회원 탈퇴
# ──────────────────────────────────────
@router.delete("/users/{user_id}")
async def delete_user(user_id: str, request: Request, response: Response):
    """회원 탈퇴 (DB 삭제 + 로그아웃)"""
    # 1. 권한 확인
    session = _get_session(request)
    if not session or session.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="권한이 없습니다")

    try:
        with get_pg_cursor() as cur:
            # 2. 사용자 삭제
            cur.execute("DELETE FROM users WHERE user_id = %s", (user_id,))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
        
        # 3. 로그아웃 (세션 삭제)
        _delete_session(request, response)
        
        return {"success": True, "message": "회원 탈퇴가 완료되었습니다"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"회원 탈퇴 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ══════════════════════════════════════
# 관리자 인증 (별도 비밀번호 체크)
# ══════════════════════════════════════

@router.post("/admin/login")
async def admin_login(request: Request):
    """관리자 비밀번호 인증 → 세션에 is_admin 플래그 저장"""
    settings = get_settings()

    # 1) 로그인 체크
    token = request.cookies.get("session_token")
    if not token:
        raise HTTPException(status_code=401, detail="먼저 로그인해주세요")

    redis_client = get_redis()
    session_data = redis_client.get(f"session:{token}")
    if not session_data:
        raise HTTPException(status_code=401, detail="세션이 만료되었습니다")

    session = json.loads(session_data)

    # 2) 비밀번호 확인
    body = await request.json()
    password = body.get("password", "")

    if password != settings.ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="관리자 비밀번호가 올바르지 않습니다")

    # 3) 세션에 is_admin 플래그 추가
    session["is_admin"] = True
    redis_client.setex(
        f"session:{token}",
        settings.SESSION_EXPIRE_HOURS * 3600,
        json.dumps(session, default=str),
    )

    logger.info(f"✅ 관리자 인증 성공: {session.get('user_id')}")
    return {"success": True, "message": "관리자 인증 완료"}


@router.post("/admin/logout")
async def admin_logout(request: Request):
    """관리자 권한 해제 (세션에서 is_admin 제거)"""
    token = request.cookies.get("session_token")
    if not token:
        return {"success": True}

    redis_client = get_redis()
    session_data = redis_client.get(f"session:{token}")
    if session_data:
        session = json.loads(session_data)
        session.pop("is_admin", None)
        settings = get_settings()
        redis_client.setex(
            f"session:{token}",
            settings.SESSION_EXPIRE_HOURS * 3600,
            json.dumps(session, default=str),
        )

    return {"success": True, "message": "관리자 권한이 해제되었습니다"}


@router.get("/admin/check")
async def admin_check(request: Request):
    """현재 세션이 관리자 인증 상태인지 확인"""
    token = request.cookies.get("session_token")
    if not token:
        return {"is_admin": False}

    redis_client = get_redis()
    session_data = redis_client.get(f"session:{token}")
    if not session_data:
        return {"is_admin": False}

    session = json.loads(session_data)
    return {"is_admin": session.get("is_admin", False)}
