"""
문의 게시판 Pydantic 모델
- inquiry_board (게시글) + comments (댓글) 구조
"""
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


# ──────────────────────────────────────
# Request 모델
# ──────────────────────────────────────
class InquiryCreateRequest(BaseModel):
    """문의글 작성"""
    title: str = Field(..., min_length=1, max_length=200, description="제목")
    content: str = Field(..., min_length=1, description="문의 내용")


class InquiryUpdateRequest(BaseModel):
    """문의글 수정"""
    title: Optional[str] = Field(None, max_length=200)
    content: Optional[str] = None


class InquiryAnswerRequest(BaseModel):
    """관리자 답변 (댓글) 작성"""
    answer: str = Field(..., min_length=1, description="답변 내용")


# ──────────────────────────────────────
# Response 모델
# ──────────────────────────────────────
class InquiryCommentResponse(BaseModel):
    """문의 댓글 응답"""
    comment_id: int
    inquiry_board_id: int
    author_id: Optional[str] = None
    author_name: Optional[str] = None
    comment_text: Optional[str] = None
    create_dt: Optional[datetime] = None


class InquiryResponse(BaseModel):
    """문의글 응답"""
    inquiry_board_id: int
    title: str
    content: Optional[str] = None
    author_id: Optional[str] = None
    author_name: Optional[str] = None
    view_count: int = 0
    comment_count: int = 0
    create_dt: Optional[datetime] = None
    update_dt: Optional[datetime] = None


class InquiryDetailResponse(BaseModel):
    """문의글 상세 (댓글 포함)"""
    post: InquiryResponse
    comments: List[InquiryCommentResponse] = []


class InquiryListResponse(BaseModel):
    """문의글 목록 (페이징)"""
    items: List[InquiryResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
