"""
인증 라우터 - 회원가입, 로그인, 사용자 정보
"""
from fastapi import APIRouter, HTTPException, status
from datetime import datetime
import hashlib
import logging

from ..database import get_pg_cursor
from ..models.user import (
    UserRegisterRequest,
    UserLoginRequest,
    UserUpdateRequest,
    UserResponse,
    LoginResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["회원가입/로그인"])


def _hash_password(password: str) -> str:
    """간단한 비밀번호 해싱 (운영 시 bcrypt 권장)"""
    return hashlib.sha256(password.encode()).hexdigest()


# ──────────────────────────────────────
# 회원가입
# ──────────────────────────────────────
@router.post("/register", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
async def register(req: UserRegisterRequest):
    """회원가입"""
    try:
        with get_pg_cursor() as cur:
            # 중복 확인
            cur.execute("SELECT user_id FROM users WHERE user_id = %s", (req.user_id,))
            if cur.fetchone():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="이미 존재하는 사용자 ID입니다",
                )

            # 이메일 중복 확인
            if req.email:
                cur.execute("SELECT user_id FROM users WHERE email = %s", (req.email,))
                if cur.fetchone():
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="이미 사용 중인 이메일입니다",
                    )

            # 사용자 등록
            cur.execute(
                """
                INSERT INTO users (user_id, password, name, email)
                VALUES (%s, %s, %s, %s)
                RETURNING user_id, name, email, role, create_dt
                """,
                (req.user_id, _hash_password(req.password), req.name, req.email),
            )
            row = cur.fetchone()

        return LoginResponse(
            success=True,
            message="회원가입 성공",
            user=UserResponse(**row),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"회원가입 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# 로그인
# ──────────────────────────────────────
@router.post("/login", response_model=LoginResponse)
async def login(req: UserLoginRequest):
    """로그인"""
    try:
        with get_pg_cursor() as cur:
            cur.execute(
                """
                SELECT user_id, name, email, role, last_login, create_dt, password
                FROM users WHERE user_id = %s
                """,
                (req.user_id,),
            )
            row = cur.fetchone()

            if not row:
                return LoginResponse(success=False, message="존재하지 않는 사용자입니다")

            if row["password"] != _hash_password(req.password):
                return LoginResponse(success=False, message="비밀번호가 일치하지 않습니다")

            # 마지막 로그인 시간 갱신
            cur.execute(
                "UPDATE users SET last_login = NOW() WHERE user_id = %s",
                (req.user_id,),
            )

        user_data = {k: v for k, v in row.items() if k != "password"}
        return LoginResponse(
            success=True,
            message="로그인 성공",
            user=UserResponse(**user_data),
        )

    except Exception as e:
        logger.error(f"로그인 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


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
                SELECT user_id, name, email, role, last_login, create_dt
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

        if not updates:
            raise HTTPException(status_code=400, detail="수정할 항목이 없습니다")

        updates.append("update_dt = NOW()")
        values.append(user_id)

        with get_pg_cursor() as cur:
            cur.execute(
                f"""
                UPDATE users SET {', '.join(updates)}
                WHERE user_id = %s
                RETURNING user_id, name, email, role, last_login, create_dt
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
