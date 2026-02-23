"""
사용자 관련 Pydantic 모델
소셜 로그인(구글/네이버/카카오) + 이메일 인증 지원
"""
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, Literal
from datetime import datetime


# ──────────────────────────────────────
# Request 모델
# ──────────────────────────────────────
class UserRegisterRequest(BaseModel):
    """이메일 회원가입 요청"""
    email: EmailStr = Field(..., description="이메일 주소")
    password: str = Field(..., min_length=4, max_length=255, description="비밀번호")
    password_confirm: str = Field(..., description="비밀번호 확인")
    name: str = Field(..., min_length=1, max_length=50, description="이름")


class UserLoginRequest(BaseModel):
    """이메일 로그인 요청"""
    email: EmailStr = Field(..., description="이메일 주소")
    password: str = Field(..., description="비밀번호")


class UserUpdateRequest(BaseModel):
    """사용자 정보 수정"""
    name: Optional[str] = Field(None, max_length=50)
    email: Optional[EmailStr] = Field(None, max_length=100)
    profile_image: Optional[str] = Field(None, max_length=512)
    current_password: Optional[str] = Field(None, description="현재 비밀번호 (비밀번호 변경 시 필수)")
    new_password: Optional[str] = Field(None, min_length=4, max_length=255, description="새 비밀번호")


# ──────────────────────────────────────
# Response 모델
# ──────────────────────────────────────
class UserResponse(BaseModel):
    """사용자 정보 응답"""
    user_id: str
    name: Optional[str] = None
    email: Optional[str] = None
    role: str = "USER"
    provider: str = "email"
    profile_image: Optional[str] = None
    last_login: Optional[datetime] = None
    create_dt: Optional[datetime] = None


class LoginResponse(BaseModel):
    """로그인/회원가입 응답"""
    success: bool
    message: str
    user: Optional[UserResponse] = None


class OAuthConfigResponse(BaseModel):
    """OAuth 제공자 활성화 상태 응답"""
    google: bool = False
    naver: bool = False
    kakao: bool = False
