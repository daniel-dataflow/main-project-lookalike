"""
게시판 라우터 - 게시글 CRUD + 댓글
"""
from fastapi import APIRouter, HTTPException, Query, status
import math
import logging

from ..database import get_pg_cursor
from ..models.inquiry_board import (
    PostCreateRequest,
    PostUpdateRequest,
    CommentCreateRequest,
    PostResponse,
    PostDetailResponse,
    PostListResponse,
    CommentResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/posts", tags=["게시판"])


# ──────────────────────────────────────
# 게시글 목록
# ──────────────────────────────────────
@router.get("", response_model=PostListResponse)
async def list_posts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    notice_only: bool = Query(False, description="공지만 조회"),
):
    """게시글 목록 (공지 우선, 최신순)"""
    try:
        offset = (page - 1) * page_size
        where = "WHERE is_notice = TRUE" if notice_only else ""

        with get_pg_cursor() as cur:
            cur.execute(f"SELECT COUNT(*) as cnt FROM inquiry_board {where}")
            total = cur.fetchone()["cnt"]

            cur.execute(
                f"""
                SELECT inquiry_board_id, title, content, author_id, view_count,
                       is_notice, create_dt, update_dt
                FROM inquiry_board
                {where}
                ORDER BY is_notice DESC, create_dt DESC
                LIMIT %s OFFSET %s
                """,
                (page_size, offset),
            )
            rows = cur.fetchall()

        return PostListResponse(
            items=[PostResponse(**r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
            total_pages=math.ceil(total / page_size) if total > 0 else 0,
        )

    except Exception as e:
        logger.error(f"게시글 목록 조회 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# 게시글 상세 (댓글 포함)
# ──────────────────────────────────────
@router.get("/{inquiry_board_id}", response_model=PostDetailResponse)
async def get_post(inquiry_board_id: int):
    """게시글 상세 조회 (조회수 +1, 댓글 포함)"""
    try:
        with get_pg_cursor() as cur:
            # 조회수 증가 + 데이터 반환
            cur.execute(
                """
                UPDATE inquiry_board SET view_count = view_count + 1
                WHERE inquiry_board_id = %s
                RETURNING inquiry_board_id, title, content, author_id, view_count,
                          is_notice, create_dt, update_dt
                """,
                (inquiry_board_id,),
            )
            post_row = cur.fetchone()

            if not post_row:
                raise HTTPException(status_code=404, detail="게시글을 찾을 수 없습니다")

            # 댓글 조회
            cur.execute(
                """
                SELECT comment_id, inquiry_board_id, author_id, comment_text, create_dt
                FROM comments
                WHERE inquiry_board_id = %s
                ORDER BY create_dt ASC
                """,
                (inquiry_board_id,),
            )
            comment_rows = cur.fetchall()

        return PostDetailResponse(
            post=PostResponse(**post_row),
            comments=[CommentResponse(**c) for c in comment_rows],
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"게시글 상세 조회 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# 게시글 작성
# ──────────────────────────────────────
@router.post("", response_model=PostResponse, status_code=status.HTTP_201_CREATED)
async def create_post(
    req: PostCreateRequest,
    author_id: str = Query(..., description="작성자 ID"),
):
    """게시글 작성"""
    try:
        with get_pg_cursor() as cur:
            cur.execute(
                """
                INSERT INTO inquiry_board (title, content, author_id, is_notice)
                VALUES (%s, %s, %s, %s)
                RETURNING inquiry_board_id, title, content, author_id, view_count,
                          is_notice, create_dt, update_dt
                """,
                (req.title, req.content, author_id, req.is_notice),
            )
            row = cur.fetchone()

        return PostResponse(**row)

    except Exception as e:
        logger.error(f"게시글 작성 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# 게시글 수정
# ──────────────────────────────────────
@router.put("/{inquiry_board_id}", response_model=PostResponse)
async def update_post(inquiry_board_id: int, req: PostUpdateRequest):
    """게시글 수정"""
    try:
        updates = []
        values = []

        if req.title is not None:
            updates.append("title = %s")
            values.append(req.title)
        if req.content is not None:
            updates.append("content = %s")
            values.append(req.content)
        if req.is_notice is not None:
            updates.append("is_notice = %s")
            values.append(req.is_notice)

        if not updates:
            raise HTTPException(status_code=400, detail="수정할 항목이 없습니다")

        updates.append("update_dt = NOW()")
        values.append(inquiry_board_id)

        with get_pg_cursor() as cur:
            cur.execute(
                f"""
                UPDATE inquiry_board SET {', '.join(updates)}
                WHERE inquiry_board_id = %s
                RETURNING inquiry_board_id, title, content, author_id, view_count,
                          is_notice, create_dt, update_dt
                """,
                values,
            )
            row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="게시글을 찾을 수 없습니다")

        return PostResponse(**row)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"게시글 수정 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# 게시글 삭제
# ──────────────────────────────────────
@router.delete("/{inquiry_board_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_post(inquiry_board_id: int):
    """게시글 삭제 (댓글도 CASCADE 삭제)"""
    try:
        with get_pg_cursor() as cur:
            cur.execute(
                "DELETE FROM inquiry_board WHERE inquiry_board_id = %s RETURNING inquiry_board_id",
                (inquiry_board_id,),
            )
            row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="게시글을 찾을 수 없습니다")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"게시글 삭제 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# 댓글 작성
# ──────────────────────────────────────
@router.post("/{inquiry_board_id}/comments", response_model=CommentResponse, status_code=status.HTTP_201_CREATED)
async def create_comment(
    inquiry_board_id: int,
    req: CommentCreateRequest,
    author_id: str = Query(..., description="작성자 ID"),
):
    """댓글 작성"""
    try:
        with get_pg_cursor() as cur:
            # 게시글 존재 확인
            cur.execute("SELECT inquiry_board_id FROM inquiry_board WHERE inquiry_board_id = %s", (inquiry_board_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="게시글을 찾을 수 없습니다")

            cur.execute(
                """
                INSERT INTO comments (inquiry_board_id, author_id, comment_text)
                VALUES (%s, %s, %s)
                RETURNING comment_id, inquiry_board_id, author_id, comment_text, create_dt
                """,
                (inquiry_board_id, author_id, req.comment_text),
            )
            row = cur.fetchone()

        return CommentResponse(**row)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"댓글 작성 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# 댓글 삭제
# ──────────────────────────────────────
@router.delete("/{inquiry_board_id}/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_comment(inquiry_board_id: int, comment_id: int):
    """댓글 삭제"""
    try:
        with get_pg_cursor() as cur:
            cur.execute(
                "DELETE FROM comments WHERE comment_id = %s AND inquiry_board_id = %s RETURNING comment_id",
                (comment_id, inquiry_board_id),
            )
            row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="댓글을 찾을 수 없습니다")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"댓글 삭제 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")
