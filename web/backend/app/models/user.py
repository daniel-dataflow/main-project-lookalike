"""
사용자 관련 Pydantic 모델
"""
from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime


# ──────────────────────────────────────
# Request 모델
# ──────────────────────────────────────
class UserRegisterRequest(BaseModel):
    """회원가입 요청"""
    user_id: str = Field(..., min_length=3, max_length=50, description="사용자 ID")
    password: str = Field(..., min_length=4, max_length=255, description="비밀번호")
    name: Optional[str] = Field(None, max_length=50, description="이름")
    email: Optional[str] = Field(None, max_length=100, description="이메일")


class UserLoginRequest(BaseModel):
    """로그인 요청"""
    user_id: str = Field(..., description="사용자 ID")
    password: str = Field(..., description="비밀번호")


class UserUpdateRequest(BaseModel):
    """사용자 정보 수정"""
    name: Optional[str] = Field(None, max_length=50)
    email: Optional[str] = Field(None, max_length=100)


# ──────────────────────────────────────
# Response 모델
# ──────────────────────────────────────
class UserResponse(BaseModel):
    """사용자 정보 응답"""
    user_id: str
    name: Optional[str] = None
    email: Optional[str] = None
    role: str = "USER"
    last_login: Optional[datetime] = None
    create_dt: Optional[datetime] = None


class LoginResponse(BaseModel):
    """로그인 응답"""
    success: bool
    message: str
    user: Optional[UserResponse] = None
