"""
고객센터 문의(Q&A) 기능을 지원하는 라우터 모듈.
- 유저-어드민 간의 커뮤니케이션 채널을 DB(inquiry_board, comments) 기반으로 구현.
- 인증 주체(유저 본인 vs 관리자)에 따라 노출되는 리스트와 권한(수정/삭제/답변)이 엄격히 분리됨.

⚠️ 라우트 순서 주의: `/admin/*` 패턴이 동적 패스인 `/{inquiry_board_id}` 보다 먼저 정의되어야 FastAPI가 경로 충돌 없이 우선순위를 처리함.
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
    """
    HTTP Request의 쿠키 헤더에서 `session_token`을 추출해 Redis를 조회함으로써 현재 무상태(Stateless) 요청자의 인증 상태를 판별함.

    Args:
        request (Request): 미들웨어를 통과한 현재 API 요청 객체.

    Returns:
        dict | None: 검증 완료된 사용자 메타 세션 딕셔너리. 존재하지 않거나 만료 시 None.
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


def _require_login(request: Request) -> dict:
    """
    유저 권한이 필수인 엔드포인트(문의글 작성 등)에서 초기 필터링을 수행하기 위함.
    세션이 없으면 `401 Unauthorized` 예외를 던져 익명 접근을 즉시 차단함.

    Args:
        request (Request): HTTP 요청.

    Returns:
        dict: 유효함이 증명된 유저 세션.
    """
    session = _get_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")
    return session


def _require_admin(request: Request) -> dict:
    """
    어드민 뷰 전용 API(답변 달기 등)를 방어하기 위한 추가 가드레일 로직.
    로그인 통과 후, 해당 계정의 `is_admin` 플래그를 검증해 일반 고객의 어뷰징을 막음.

    Args:
        request (Request): HTTP 요청 객체.

    Returns:
        dict: 인증된 관리자 세션 데이터.
    """
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
    """
    관리자용 백오피스 대시보드에 전시하기 위해, 등록된 모든 사용자의 문의글을 최신 시간순으로 페이지네이션 처리하여 반환함.

    Args:
        request (Request): 어드민 여부 파악용 리퀘스트.
        page (int, optional): 타겟 페이지 숫자.
        page_size (int, optional): 한 페이지당 리스트 규모.

    Returns:
        InquiryListResponse: 문의글 메타데이터 리스트 및 페이지 요약 (Pydantic 모델).
    """
    _require_admin(request)

    try:
        offset = (page - 1) * page_size

        with get_pg_cursor() as cur:
            cur.execute("SELECT COUNT(*) as cnt FROM inquiry_board")
            total = cur.fetchone()["cnt"]

            cur.execute(
                """
                SELECT ib.inquiry_board_id, ib.title, ib.content, ib.author_id,
                       u.user_name as author_name,
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
    """
    관리자가 단일 고객의 불만/문의 사항을 상세 파악하기 위해, 게시글 원본과 누적된 답변(Comment) 배열을 한 번의 API 통신으로 묶어서 가져옴.

    Args:
        inquiry_board_id (int): 대상이 될 문의글 PK.
        request (Request): 어드민 검증용 세션.

    Returns:
        InquiryDetailResponse: 원글(Post)과 달린 답변(Comments) 리스트의 묶음 데이터.
    """
    _require_admin(request)

    try:
        with get_pg_cursor() as cur:
            cur.execute(
                """
                SELECT ib.inquiry_board_id, ib.title, ib.content, ib.author_id,
                       u.user_name as author_name,
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
                       u.user_name as author_name,
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
    """
    관리자 권한으로 특정 고객의 문의 게시물 하단에 답변 코멘트를 영구 기록함.
    CS 응대 활동을 DB `comments` 테이블로 적재하는 과정.

    Args:
        inquiry_board_id (int): 답변을 달 원글 PK.
        req (InquiryAnswerRequest): 답변 페이로드(내용 단일 필드).
        request (Request): 어드민 계정 추적용.

    Returns:
        InquiryCommentResponse: 성공적으로 인서트된 댓글 데이터 응답 모델.
    """
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
                "SELECT user_name as name FROM users WHERE user_id = %s", (admin_id,)
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
    """
    마이페이지 등에서 각 개별 사용자가 본인이 남긴 문의 내역만을 필터링해 확인하도록 제공됨.
    `WHERE author_id` 절을 통해 타인의 문의를 조회하지 못하게 격리 보호함.

    Args:
        request (Request): 접속 유저 신원 파악.
        page (int, optional): 페이지 번호.
        page_size (int, optional): 페이지 개수 제한.

    Returns:
        InquiryListResponse: 내 문의글 목록 모음 모델.
    """
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
                       u.user_name as author_name,
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
    """
    로그인을 인증받은 고객이 신규 문의/건의 사항을 시스템에 건네어 `inquiry_board`에 레코드로 적재함.

    Args:
        req (InquiryCreateRequest): 작성 요청 내용 (제목, 본문 등).
        request (Request): 작성자 주체 추적.

    Returns:
        InquiryResponse: Insert 완료된 PK 등을 담은 반환 데이터.
    """
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
    """
    클라이언트가 문의 게시판 항목을 클릭해 상세 내용을 열람할 때 호출됨.
    타인의 비공개 문의글을 읽지 못하도록 본인 검증(혹은 관리자 권한 여부)을 거친 후 게시글과 댓글을 응답함.
    조회 수(View Count)도 1회 증가시킴.

    Args:
        inquiry_board_id (int): 열람할 문의글 고유 ID.
        request (Request): 식별 세션용.

    Returns:
        InquiryDetailResponse: 메인 글과 하위 답변이 포함된 반환 객체.
    """
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
                "SELECT user_name as name FROM users WHERE user_id = %s",
                (row["author_id"],),
            )
            author_row = cur.fetchone()

            # 댓글 조회
            cur.execute(
                """
                SELECT c.comment_id, c.inquiry_board_id, c.author_id,
                       u.user_name as author_name,
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
    """
    문의자가 본인이 작성했던 기존의 글 내용이나 제목을 변경함.
    타인이 남의 글을 수정할 수 없도록 강제 필터링하며, 수정된 항목(updates 배열)만 동적으로 빌드해 UPDATE 치는 구조임.

    Args:
        inquiry_board_id (int): 수정 대상 ID.
        req (InquiryUpdateRequest): title/content 중 변경된 속성이 기재된 페이로드.
        request (Request): 소유자 검증용.

    Returns:
        InquiryResponse: 수정 적용된 최신 문의글 상태.
    """
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
    """
    작성자 본인이 문의글을 완전히 지우고자 할 때 사용됨.
    게시글을 지울 때 종속된 `comments`까지 함께 롤백/캐스캐이드 되도록 처리하여 시스템에 고아 데이터를 남기지 않음.

    Args:
        inquiry_board_id (int): 삭제할 ID.
        request (Request): 권한 체크 세션.
    """
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
