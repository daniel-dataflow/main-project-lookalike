"""
문의 게시판 라우터 - 유저용/관리자용 API
- inquiry_board (게시글) + comments (댓글) 구조
- 유저: 본인 문의글 CRUD
- 관리자: 모든 문의글 조회 + 답변(댓글) 작성

⚠️ 라우트 순서 중요: /admin/* 경로가 /{inquiry_board_id} 보다 먼저 정의되어야 함
"""
from fastapi import APIRouter, HTTPException, Query, Request, status
import math
import logging
import json

from ..database import get_pg_cursor, get_redis
from ..models.inquiry import (
    InquiryCreateRequest,
    InquiryUpdateRequest,
    InquiryAnswerRequest,
    InquiryResponse,
    InquiryDetailResponse,
    InquiryCommentResponse,
    InquiryListResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/inquiries", tags=["문의 게시판"])


# ──────────────────────────────────────
# 세션 헬퍼
# ──────────────────────────────────────
def _get_session(request: Request) -> dict | None:
    """쿠키에서 세션 데이터 조회"""
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


def _require_login(request: Request) -> dict:
    """로그인 필수 - 세션 없으면 401"""
    session = _get_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")
    return session


def _require_admin(request: Request) -> dict:
    """관리자 전용 - 세션의 is_admin 플래그 확인"""
    session = _require_login(request)
    if not session.get("is_admin"):
        raise HTTPException(status_code=403, detail="관리자 인증이 필요합니다")
    return session


# ══════════════════════════════════════
# 관리자 전용 API (/{inquiry_board_id} 보다 먼저 정의)
# ══════════════════════════════════════

# ──────────────────────────────────────
# [관리자] 전체 문의글 목록
# ──────────────────────────────────────
@router.get("/admin/list", response_model=InquiryListResponse)
async def admin_list_inquiries(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """[관리자] 전체 문의글 목록"""
    _require_admin(request)

    try:
        offset = (page - 1) * page_size

        with get_pg_cursor() as cur:
            cur.execute("SELECT COUNT(*) as cnt FROM inquiry_board")
            total = cur.fetchone()["cnt"]

            cur.execute(
                """
                SELECT ib.inquiry_board_id, ib.title, ib.content, ib.author_id,
                       u.name as author_name,
                       ib.view_count,
                       COALESCE(cc.cnt, 0) as comment_count,
                       ib.create_dt, ib.update_dt
                FROM inquiry_board ib
                LEFT JOIN users u ON ib.author_id = u.user_id
                LEFT JOIN (
                    SELECT inquiry_board_id, COUNT(*) as cnt FROM comments GROUP BY inquiry_board_id
                ) cc ON ib.inquiry_board_id = cc.inquiry_board_id
                ORDER BY ib.create_dt DESC
                LIMIT %s OFFSET %s
                """,
                (page_size, offset),
            )
            rows = cur.fetchall()

        return InquiryListResponse(
            items=[InquiryResponse(**r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
            total_pages=math.ceil(total / page_size) if total > 0 else 0,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[관리자] 문의글 목록 조회 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# [관리자] 문의글 상세 조회 (댓글 포함)
# ──────────────────────────────────────
@router.get("/admin/{inquiry_board_id}", response_model=InquiryDetailResponse)
async def admin_get_inquiry(inquiry_board_id: int, request: Request):
    """[관리자] 문의글 상세 (댓글 포함)"""
    _require_admin(request)

    try:
        with get_pg_cursor() as cur:
            cur.execute(
                """
                SELECT ib.inquiry_board_id, ib.title, ib.content, ib.author_id,
                       u.name as author_name,
                       ib.view_count,
                       ib.create_dt, ib.update_dt
                FROM inquiry_board ib
                LEFT JOIN users u ON ib.author_id = u.user_id
                WHERE ib.inquiry_board_id = %s
                """,
                (inquiry_board_id,),
            )
            row = cur.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="문의글을 찾을 수 없습니다")

            # 댓글 조회
            cur.execute(
                """
                SELECT c.comment_id, c.inquiry_board_id, c.author_id,
                       u.name as author_name,
                       c.comment_text, c.create_dt
                FROM comments c
                LEFT JOIN users u ON c.author_id = u.user_id
                WHERE c.inquiry_board_id = %s
                ORDER BY c.create_dt ASC
                """,
                (inquiry_board_id,),
            )
            comment_rows = cur.fetchall()

        return InquiryDetailResponse(
            post=InquiryResponse(**row, comment_count=len(comment_rows)),
            comments=[InquiryCommentResponse(**c) for c in comment_rows],
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[관리자] 문의글 상세 조회 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# [관리자] 답변 작성 (댓글로 등록)
# ──────────────────────────────────────
@router.post("/admin/{inquiry_board_id}/answer", response_model=InquiryCommentResponse)
async def admin_answer_inquiry(
    inquiry_board_id: int, req: InquiryAnswerRequest, request: Request
):
    """[관리자] 문의글에 답변(댓글) 작성"""
    session = _require_admin(request)
    admin_id = session["user_id"]

    try:
        with get_pg_cursor() as cur:
            # 게시글 존재 확인
            cur.execute(
                "SELECT inquiry_board_id FROM inquiry_board WHERE inquiry_board_id = %s",
                (inquiry_board_id,),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="문의글을 찾을 수 없습니다")

            # 댓글로 답변 등록
            cur.execute(
                """
                INSERT INTO comments (inquiry_board_id, author_id, comment_text)
                VALUES (%s, %s, %s)
                RETURNING comment_id, inquiry_board_id, author_id, comment_text, create_dt
                """,
                (inquiry_board_id, admin_id, req.answer),
            )
            row = cur.fetchone()

            # 작성자 이름 조회
            cur.execute(
                "SELECT name FROM users WHERE user_id = %s", (admin_id,)
            )
            user_row = cur.fetchone()

        return InquiryCommentResponse(
            **row,
            author_name=user_row["name"] if user_row else None,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[관리자] 답변 작성 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ══════════════════════════════════════
# 유저 API
# ══════════════════════════════════════

# ──────────────────────────────────────
# [유저] 내 문의글 목록
# ──────────────────────────────────────
@router.get("", response_model=InquiryListResponse)
async def list_my_inquiries(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """유저 본인의 문의글 목록 (최신순)"""
    session = _require_login(request)
    user_id = session["user_id"]

    try:
        offset = (page - 1) * page_size

        with get_pg_cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) as cnt FROM inquiry_board WHERE author_id = %s",
                (user_id,),
            )
            total = cur.fetchone()["cnt"]

            cur.execute(
                """
                SELECT ib.inquiry_board_id, ib.title, ib.content, ib.author_id,
                       u.name as author_name,
                       ib.view_count,
                       COALESCE(cc.cnt, 0) as comment_count,
                       ib.create_dt, ib.update_dt
                FROM inquiry_board ib
                LEFT JOIN users u ON ib.author_id = u.user_id
                LEFT JOIN (
                    SELECT inquiry_board_id, COUNT(*) as cnt FROM comments GROUP BY inquiry_board_id
                ) cc ON ib.inquiry_board_id = cc.inquiry_board_id
                WHERE ib.author_id = %s
                ORDER BY ib.create_dt DESC
                LIMIT %s OFFSET %s
                """,
                (user_id, page_size, offset),
            )
            rows = cur.fetchall()

        return InquiryListResponse(
            items=[InquiryResponse(**r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
            total_pages=math.ceil(total / page_size) if total > 0 else 0,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"문의글 목록 조회 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# [유저] 문의글 작성
# ──────────────────────────────────────
@router.post("", response_model=InquiryResponse, status_code=status.HTTP_201_CREATED)
async def create_inquiry(req: InquiryCreateRequest, request: Request):
    """문의글 작성"""
    session = _require_login(request)
    user_id = session["user_id"]

    try:
        with get_pg_cursor() as cur:
            cur.execute(
                """
                INSERT INTO inquiry_board (title, content, author_id)
                VALUES (%s, %s, %s)
                RETURNING inquiry_board_id, title, content, author_id, view_count,
                          create_dt, update_dt
                """,
                (req.title, req.content, user_id),
            )
            row = cur.fetchone()

        return InquiryResponse(**row, author_name=session.get("name"))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"문의글 작성 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# [유저] 문의글 상세 조회 (댓글 포함)
# ──────────────────────────────────────
@router.get("/{inquiry_board_id}", response_model=InquiryDetailResponse)
async def get_inquiry(inquiry_board_id: int, request: Request):
    """문의글 상세 (본인 글만, 관리자는 전체)"""
    session = _require_login(request)
    user_id = session["user_id"]
    is_admin = session.get("is_admin", False)

    try:
        with get_pg_cursor() as cur:
            # 조회수 증가 + 데이터 반환
            cur.execute(
                """
                UPDATE inquiry_board SET view_count = view_count + 1
                WHERE inquiry_board_id = %s
                RETURNING inquiry_board_id, title, content, author_id, view_count,
                          create_dt, update_dt
                """,
                (inquiry_board_id,),
            )
            row = cur.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="문의글을 찾을 수 없습니다")

            # 본인 글이 아니고 관리자도 아니면 403
            if row["author_id"] != user_id and not is_admin:
                raise HTTPException(status_code=403, detail="접근 권한이 없습니다")

            # 작성자 이름 조회
            cur.execute(
                "SELECT name FROM users WHERE user_id = %s",
                (row["author_id"],),
            )
            author_row = cur.fetchone()

            # 댓글 조회
            cur.execute(
                """
                SELECT c.comment_id, c.inquiry_board_id, c.author_id,
                       u.name as author_name,
                       c.comment_text, c.create_dt
                FROM comments c
                LEFT JOIN users u ON c.author_id = u.user_id
                WHERE c.inquiry_board_id = %s
                ORDER BY c.create_dt ASC
                """,
                (inquiry_board_id,),
            )
            comment_rows = cur.fetchall()

        return InquiryDetailResponse(
            post=InquiryResponse(
                **row,
                author_name=author_row["name"] if author_row else None,
                comment_count=len(comment_rows),
            ),
            comments=[InquiryCommentResponse(**c) for c in comment_rows],
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"문의글 상세 조회 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# [유저] 문의글 수정
# ──────────────────────────────────────
@router.put("/{inquiry_board_id}", response_model=InquiryResponse)
async def update_inquiry(inquiry_board_id: int, req: InquiryUpdateRequest, request: Request):
    """문의글 수정 (본인만)"""
    session = _require_login(request)
    user_id = session["user_id"]

    try:
        with get_pg_cursor() as cur:
            # 기존 글 확인
            cur.execute(
                "SELECT author_id FROM inquiry_board WHERE inquiry_board_id = %s",
                (inquiry_board_id,),
            )
            existing = cur.fetchone()

            if not existing:
                raise HTTPException(status_code=404, detail="문의글을 찾을 수 없습니다")
            if existing["author_id"] != user_id:
                raise HTTPException(status_code=403, detail="본인의 글만 수정할 수 있습니다")

            updates = []
            values = []

            if req.title is not None:
                updates.append("title = %s")
                values.append(req.title)
            if req.content is not None:
                updates.append("content = %s")
                values.append(req.content)

            if not updates:
                raise HTTPException(status_code=400, detail="수정할 항목이 없습니다")

            updates.append("update_dt = NOW()")
            values.append(inquiry_board_id)

            cur.execute(
                f"""
                UPDATE inquiry_board SET {', '.join(updates)}
                WHERE inquiry_board_id = %s
                RETURNING inquiry_board_id, title, content, author_id, view_count,
                          create_dt, update_dt
                """,
                values,
            )
            row = cur.fetchone()

        return InquiryResponse(**row, author_name=session.get("name"))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"문의글 수정 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# [유저] 문의글 삭제
# ──────────────────────────────────────
@router.delete("/{inquiry_board_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inquiry(inquiry_board_id: int, request: Request):
    """문의글 삭제 (본인만, 댓글도 CASCADE 삭제)"""
    session = _require_login(request)
    user_id = session["user_id"]

    try:
        with get_pg_cursor() as cur:
            cur.execute(
                "SELECT author_id FROM inquiry_board WHERE inquiry_board_id = %s",
                (inquiry_board_id,),
            )
            existing = cur.fetchone()

            if not existing:
                raise HTTPException(status_code=404, detail="문의글을 찾을 수 없습니다")
            if existing["author_id"] != user_id:
                raise HTTPException(status_code=403, detail="본인의 글만 삭제할 수 있습니다")

            cur.execute(
                "DELETE FROM inquiry_board WHERE inquiry_board_id = %s",
                (inquiry_board_id,),
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"문의글 삭제 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")
