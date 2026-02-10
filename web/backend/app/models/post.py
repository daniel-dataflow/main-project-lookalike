"""
게시판 관련 Pydantic 모델
"""
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


# ──────────────────────────────────────
# Request 모델
# ──────────────────────────────────────
class PostCreateRequest(BaseModel):
    """게시글 작성"""
    title: str = Field(..., min_length=1, max_length=200, description="제목")
    content: Optional[str] = Field(None, description="내용")
    is_notice: bool = Field(False, description="공지 여부")


class PostUpdateRequest(BaseModel):
    """게시글 수정"""
    title: Optional[str] = Field(None, max_length=200)
    content: Optional[str] = None
    is_notice: Optional[bool] = None


class CommentCreateRequest(BaseModel):
    """댓글 작성"""
    comment_text: str = Field(..., min_length=1, description="댓글 내용")


# ──────────────────────────────────────
# Response 모델
# ──────────────────────────────────────
class CommentResponse(BaseModel):
    """댓글 응답"""
    comment_id: int
    post_id: int
    author_id: Optional[str] = None
    comment_text: Optional[str] = None
    create_dt: Optional[datetime] = None


class PostResponse(BaseModel):
    """게시글 응답"""
    post_id: int
    title: str
    content: Optional[str] = None
    author_id: Optional[str] = None
    view_count: int = 0
    is_notice: bool = False
    create_dt: Optional[datetime] = None
    update_dt: Optional[datetime] = None


class PostDetailResponse(BaseModel):
    """게시글 상세 (댓글 포함)"""
    post: PostResponse
    comments: List[CommentResponse] = []


class PostListResponse(BaseModel):
    """게시글 목록 (페이징)"""
    items: List[PostResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
