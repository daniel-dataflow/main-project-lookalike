"""
사용자 세션 관리 및 인증(소셜 OAuth2, 이메일)을 총괄하는 핵심 라우터 모듈.
- JWT 대신 Redis 기반 Stateful 세션을 채택하여 즉각적인 세션 무효화(로그아웃/어드민 밴) 요건을 충족.
- 단일 진입점에서 여러 플랫폼(Google, Naver, Kakao, Email)의 로그인 상태를 규격화된 포맷으로 변환해 관리함.
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
from ..config.auth import OAUTH_CONFIGS

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
    """
    고유 난수 토큰을 발행해 사용자 사전 데이터를 Redis에 영속화(TTL)하고 이를 클라이언트 쿠키로 내려줌.
    JWT의 한계를 보완하여 서버 측에서 세션의 라이프사이클을 통제하기 위함.

    Args:
        response (Response): 쿠키 값을 세팅할 응답 객체.
        user_data (dict): 세션에 직렬화해 보관할 유저 메타 정보.

    Returns:
        str: 발급된 UUID4 형식의 세션 증명 토큰.
    """
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
    """
    모든 인증 필요 API가 호출될 때마다 유저를 식별하기 위해 쿠키에 담긴 토큰으로 Redis를 질의함.
    성능을 위해 RDBMS Join 없이 인증을 마칠 수 있도록 설계됨.

    Args:
        request (Request): 쿠키가 포함된 HTTP 리퀘스트 객체.

    Returns:
        dict | None: 검증된 유저 정보. 캐시 미스거나 형용 불가 시 None.
    """
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
    """
    클라이언트 로그아웃 시 쿠키 클리어 및 Redis 스토어 내 해당 키를 즉각 파기함.
    강제 로그아웃/만료 상태를 즉각 반영하여 보안을 강화.

    Args:
        request (Request): 세션 키 추출.
        response (Response): Set-Cookie 만료일 초기화(삭제) 지시.
    """
    token = request.cookies.get("session_token")
    if token:
        try:
            redis_client = get_redis()
            redis_client.delete(f"session:{token}")
        except Exception as e:
            logger.warning(f"Redis 세션 삭제 실패: {e}")

    response.delete_cookie(key="session_token", path="/")


# ──────────────────────────────────────
# Admin 전용 Redis 세션 관리
# ──────────────────────────────────────
def _create_admin_session(response: Response, user_data: dict) -> str:
    """
    일반 유저 계정과 어드민 계정의 탈취 리스크를 분리하기 위한 독립된 Namespace(`admin_session:`) 세션 생성 함수.
    이중 보안 및 강도 높은 권한 관리를 위해 사용.

    Args:
        response (Response): 클라이언트 쿠키 주입.
        user_data (dict): 어드민 고유 권한 데이터.

    Returns:
        str: 발급된 어드민 전용 토큰값.
    """
    settings = get_settings()
    token = str(uuid.uuid4())
    session_data = json.dumps(user_data, default=str, ensure_ascii=False)

    try:
        redis_client = get_redis()
        redis_client.setex(
            f"admin_session:{token}",
            settings.SESSION_EXPIRE_HOURS * 3600,
            session_data,
        )
    except Exception as e:
        logger.warning(f"Redis 어드민 세션 저장 실패: {e}")
        raise HTTPException(status_code=500, detail="어드민 세션 생성 실패")

    response.set_cookie(
        key="admin_session_token",
        value=token,
        httponly=True,
        max_age=settings.SESSION_EXPIRE_HOURS * 3600,
        samesite="lax",
        path="/",
    )
    return token


def _get_admin_session(request: Request) -> dict | None:
    """
    백오피스 전용 라우터 진입 전용 미들웨어가 소비하는 함수.
    기본 쿠키와 독립된 위치에서 검증하여 일반 사용자의 어드민 API 침투를 원천 봉쇄함.

    Args:
        request (Request): HTTP 요청.

    Returns:
        dict | None: 디코딩된 관리자 메타 객체.
    """
    token = request.cookies.get("admin_session_token")
    if not token:
        return None

    try:
        redis_client = get_redis()
        data = redis_client.get(f"admin_session:{token}")
        if data:
            return json.loads(data)
    except Exception as e:
        logger.warning(f"Redis 어드민 세션 조회 실패: {e}")

    return None


def _delete_admin_session(request: Request, response: Response):
    """
    어드민 대시보드 강제 로그아웃 또는 보안 검사 실패 시 백오피스 접근 권한을 즉시 파기함.
    
    Args:
        request (Request): 제거할 세션 존재 확인.
        response (Response): 쿠키 파기 응답.
    """
    token = request.cookies.get("admin_session_token")
    if token:
        try:
            redis_client = get_redis()
            redis_client.delete(f"admin_session:{token}")
        except Exception as e:
            logger.warning(f"Redis 어드민 세션 삭제 실패: {e}")

    response.delete_cookie(key="admin_session_token", path="/")


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
    """
    네이티브 로그인(자체 회원가입) 사용자의 자격 증명을 암호화하여 DB에 적재함.
    SNS가 아닌 이메일 기반 독자 플랫폼 회원을 유치하기 위함.

    Args:
        req (UserRegisterRequest): 클라이언트가 입력한 이메일/비밀번호/이름.
        response (Response): 회원가입 즉시 로그인 처리용 쿠키.

    Returns:
        LoginResponse: 회원 객체 모델 반환.
    """
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
                INSERT INTO users (user_id, password, user_name, email, provider)
                VALUES (%s, %s, %s, %s, 'email')
                RETURNING user_id, user_name as name, email, role, provider, profile_image, create_dt
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
    """
    클라이언트 제출 평문 패스워드와 RDBMS의 해시 암호문 대조 후 인증 토큰(Redis Session)을 발행함.

    Args:
        req (UserLoginRequest): 검증 이메일과 패스워드.
        response (Response): 검증 직후 세션을 구워낼 응답 객체.

    Returns:
        LoginResponse: 로그인 성공 여부 및 제한된 User 객체 (패스워드 제거).
    """
    try:
        with get_pg_cursor() as cur:
            cur.execute(
                """
                SELECT user_id, user_name as name, email, role, provider, profile_image,
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
    """
    이전 로그인 흔적(Redis 스토어)을 모두 삭제 처리해 Stateless 환경에서의 상태 초기화를 수행함.
    CSRF 공격 방어 및 공용 PC 보안 유지를 목적.
    """
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
# OAUTH_CONFIGS is now managed via config.auth.OAUTH_CONFIGS



@router.get("/oauth/{provider}")
async def oauth_login(provider: str, request: Request):
    """
    소셜 인증 시작(Authorize) 지점. 사용자를 선택한 서드파티 제공자 서버의 인증 화면으로 우회(Redirect)시킴.
    콜백 탈취를 막기 위한 OAuth 표준 CSRF State 토큰을 임시 캐시함.

    Args:
        provider (str): facebook, google, kakao 등 식별자.
        request (Request): 동적 리다이렉트 Callback URI 연산을 위한 Host Request.

    Returns:
        RedirectResponse: Oauth 제공자 도메인으로의 강제 라우팅.
    """
    settings = get_settings()

    if provider not in OAUTH_CONFIGS:
        raise HTTPException(status_code=400, detail="지원하지 않는 OAuth 제공자입니다")

    if not settings.is_oauth_configured(provider):
        raise HTTPException(status_code=400, detail=f"{provider} 로그인이 설정되지 않았습니다")

    config = OAUTH_CONFIGS[provider]
    
    # 프록시(Nginx 등) 환경에서 request.url_for 사용 시 http로 강제되는 현상을 막기 위해
    # 접속한 브라우저의 실제 헤더(scheme, host)를 기반으로 redirect_uri를 동적 생성함
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.hostname))
    redirect_uri = f"{scheme}://{host}/api/auth/oauth/{provider}/callback"

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
    """
    서드파티 제공자(OAuth)로부터 인가 코드(Authorization Code)를 수신해 내부 서버 대 서버 통신으로 액세스 토큰을 교환함.
    이후 토큰으로 프로필(이메일, 이름)을 가져와 RDBMS에 유저 정보를 Upsert(가입/로그인) 처리함.

    Args:
        provider (str): 호출한 소셜 로그인 제공자.
        request (Request): 내부 Host 파싱용.
        code (str, optional): OAuth Authorization Code.
        state (str, optional): 보안용 상태 검증 난수.
        error (str, optional): 제공자가 건네준 오류 코드.
        error_description (str, optional): 상세 오류 메시지.
        response (Response): 리다이렉트 시 세션 쿠키를 굽기 위함.

    Returns:
        RedirectResponse: 처리가 완료된 후 메인 페이지('/')로 이동.
    """
    settings = get_settings()

    # OAuth 제공자가 에러를 반환한 경우
    if error or not code:
        logger.warning(f"OAuth 콜백 에러 ({provider}): error={error}, desc={error_description}")
        return RedirectResponse(url="/?error=oauth_failed")

    if provider not in OAUTH_CONFIGS:
        raise HTTPException(status_code=400, detail="지원하지 않는 OAuth 제공자입니다")

    config = OAUTH_CONFIGS[provider]
    
    # 토큰 교환 시에도 동일한 방식을 사용하여 redirect_uri 불일치 방지
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.hostname))
    redirect_uri = f"{scheme}://{host}/api/auth/oauth/{provider}/callback"
    
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
                    UPDATE users SET last_login = NOW(), user_name = COALESCE(%s, user_name),
                           profile_image = COALESCE(%s, profile_image)
                    WHERE provider = %s AND provider_id = %s
                    RETURNING user_id, user_name as name, email, role, provider, profile_image, create_dt
                    """,
                    (name, profile_image, provider, provider_id),
                )
                row = cur.fetchone()
            else:
                # 신규 사용자 → 생성
                user_id = f"{provider}_{uuid.uuid4().hex[:8]}"
                cur.execute(
                    """
                    INSERT INTO users (user_id, user_name, email, provider, provider_id, profile_image)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING user_id, user_name as name, email, role, provider, profile_image, create_dt
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
    """
    각 소셜 플랫폼마다 다른 프로필 응답 JSON 구조를 하나로 정규화(Normalize)함.
    회원 관리 로직(DB Upsert 등)에서 플랫폼 의존성을 제거하기 위함.

    Args:
        provider (str): 로그인 제공자.
        info (dict): 서드파티로부터 받은 Raw JSON.

    Returns:
        tuple: (식별자, 이름, 이메일, 프로필 이미지 URL)의 형태.
    """
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
    """
    특정 1인의 상세 메타데이터를 RDBMS에서 꺼내 반환함.
    어드민 대시보드 내 유저 관리 탭 또는 회원의 마이페이지 상세에서 이용됨.

    Args:
        user_id (str): 찾고자 하는 대상 PK.

    Returns:
        UserResponse: Pydantic 포맷의 인증 객체.
    """
    try:
        with get_pg_cursor() as cur:
            cur.execute(
                """
                SELECT user_id, user_name as name, email, role, provider, profile_image,
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
    """
    네이티브 로그인 유저가 비밀번호를 갱신할 때 사용됨.
    소셜 로그인 주체의 비밀번호는 당사에 없으므로 정책상 차단(403)함. 

    Args:
        user_id (str): 대상 유저.
        req (UserUpdateRequest): 이전 비밀번호와 새 비밀번호.
        request (Request): 소유권 증명을 위한 로그인 세션 참조.
        response (Response): 비밀번호 변경 시 기존 세션 파괴용(로그아웃).

    Returns:
        UserResponse: 상태가 업데이트된 최신 정보.
    """
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
                RETURNING user_id, user_name as name, email, role, provider, profile_image,
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
async def admin_login(request: Request, response: Response):
    """관리자 아이디/비밀번호 인증 → 새로운 관리자 세션 생성"""
    settings = get_settings()

    body = await request.json()
    username = body.get("username", "")
    password = body.get("password", "")

    if not username or not password:
        raise HTTPException(status_code=400, detail="아이디와 비밀번호를 입력해주세요")

    if username != settings.ADMIN_USERNAME or password != settings.ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="관리자 아이디 또는 비밀번호가 올바르지 않습니다")

    user_data = {
        "user_id": settings.ADMIN_USERNAME,
        "name": "Admin",
        "role": "ADMIN",
        "provider": "system",
        "is_admin": True,
    }
    _create_admin_session(response, user_data)

    logger.info(f"✅ 관리자 인증 성공: {settings.ADMIN_USERNAME}")
    return {"success": True, "message": "관리자 인증 완료"}


@router.post("/admin/logout")
async def admin_logout(request: Request, response: Response):
    """관리자 로그아웃 (세션 삭제)"""
    _delete_admin_session(request, response)
    return {"success": True, "message": "관리자 권한이 해제되었습니다"}


@router.get("/admin/check")
async def admin_check(request: Request):
    """현재 세션이 관리자 인증 상태인지 확인"""
    session = _get_admin_session(request)
    if not session:
        return {"is_admin": False}

    return {"is_admin": session.get("is_admin", False)}
