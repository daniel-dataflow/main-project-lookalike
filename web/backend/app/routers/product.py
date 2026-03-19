"""
PostgreSQL(products 등 핵심 스키마) 및 MongoDB(비정형 텍스트) 혼합 저장소 기반의 상품 정보 라우터 모듈.
- 클라이언트 쇼핑몰 뷰 렌더링을 위해 기본 메타데이터와 상품 상세 설명, 관련 가격 정보를 애그리게이션(Aggregation)하여 제공함.
- 사용자 경험 향상을 위한 '최근 본 상품', '좋아요' 등의 개별 유저 행동 기반 API도 함께 호스팅함.
"""
from fastapi import APIRouter, HTTPException, Query, status, Request
import math
import logging

from ..database import get_pg_cursor, get_mongo_db, get_redis
from ..models.product import (
    ProductCreateRequest,
    ProductResponse,
    ProductDetailResponse,
    ProductListResponse,
    NaverPriceResponse,
)

from typing import Optional
import json

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/products", tags=["상품"])


# ──────────────────────────────────────
# 세션에서 사용자 정보 가져오기
# ──────────────────────────────────────
def _get_user_from_session(request: Request) -> Optional[dict]:
    """
    현재 HTTP 요청의 세션 토큰을 검사해 Redis에서 유저 정보를 꺼내옴.
    특정 상품 조작이나 조회 리스트 로딩 전 사용 권한/식별자를 부여받기 위해 라우트마다 주입/활용됨.

    Args:
        request (Request): 미들웨어를 거쳐 도달한 FastAPI HTTP Request 객체.

    Returns:
        Optional[dict]: Redis에서 꺼내온 세션 사전 객체 혹은 미가입 시 None.
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
        logger.error(f"세션 조회 실패: {e}")
    return None




# ──────────────────────────────────────
# 상품 목록 (페이징)
# ──────────────────────────────────────
@router.get("/recent-views")
async def get_recent_views(request: Request, limit: int = 20):
    """
    현재 로그인한 유저가 과거 조회했던 상품 기록들을 `recent_views` 테이블에서 꺼내 시간형 역순 제공.
    고객 체류시간을 높이기 위해 프론트엔드 개인화 추천 위젯에 바인딩됨.

    Args:
        request (Request): 로그인 사용자 세션을 파싱할 HTTP 리퀘스트.
        limit (int, optional): 가져올 상품 이력의 개수 상한 (기본 20).

    Returns:
        dict: 파싱 성공 시 success 플래그와 파싱된 상품 정보 리스트(products).
    """
    session = _get_user_from_session(request)
    user_id = session.get("user_id") if session else None

    if not user_id:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")

    try:
        with get_pg_cursor() as cur:
            cur.execute(
                """
                SELECT 
                    p.product_id,
                    p.prod_name,
                    p.brand_name,
                    p.base_price,
                    p.img_hdfs_path,
                    p.category_code,
                    COALESCE(np.naver_price, p.base_price) as lowest_price,
                    np.mall_name,
                    rv.view_dt
                FROM recent_views rv
                JOIN products p ON rv.product_id = p.product_id
                LEFT JOIN naver_prices np ON p.product_id = np.product_id AND np.rank = 1
                WHERE rv.user_id = %s
                ORDER BY rv.view_dt DESC
                LIMIT %s
                """,
                (user_id, limit),
            )
            rows = cur.fetchall()

        return {
            "success": True,
            "products": [
                {
                    "product_id": str(r["product_id"]),
                    "prod_name": r["prod_name"],
                    "brand_name": r["brand_name"],
                    "base_price": r["base_price"],
                    "lowest_price": r["lowest_price"],
                    "img_hdfs_path": r["img_hdfs_path"],
                    "category_code": r["category_code"],
                    "mall_name": r["mall_name"],
                    "view_dt": r["view_dt"].isoformat() if r["view_dt"] else None,
                }
                for r in rows
            ],
        }
    except Exception as e:
        logger.error(f"최근 본 상품 조회 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# 좋아요 추가
@router.get("/likes")
async def get_likes(request: Request, limit: int = 20):
    """
    사용자가 '찜하기' 버튼을 누른(조인된) 대상 상품 목록을 조회함.
    개인화된 쇼핑 포트폴리오(마이페이지) 구성을 위해 호출됨.

    Args:
        request (Request): 접속 세션 파악을 위한 HTTP 리퀘스트 객체.
        limit (int, optional): 가져올 최대 찜 목록 사이즈.

    Returns:
        dict: 찜한 상품들의 세부 딕셔너리 배열.
    """
    session = _get_user_from_session(request)
    user_id = session.get("user_id") if session else None

    if not user_id:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")

    try:
        with get_pg_cursor() as cur:
            cur.execute(
                """
                SELECT 
                    p.product_id,
                    p.prod_name,
                    p.brand_name,
                    p.base_price,
                    p.img_hdfs_path,
                    p.category_code,
                    COALESCE(np.naver_price, p.base_price) as lowest_price,
                    np.mall_name,
                    l.create_dt
                FROM likes l
                JOIN products p ON l.product_id = p.product_id
                LEFT JOIN naver_prices np ON p.product_id = np.product_id AND np.rank = 1
                WHERE l.user_id = %s
                ORDER BY l.create_dt DESC
                LIMIT %s
                """,
                (user_id, limit),
            )
            rows = cur.fetchall()

        return {
            "success": True,
            "products": [
                {
                    "product_id": str(r["product_id"]),
                    "prod_name": r["prod_name"],
                    "brand_name": r["brand_name"],
                    "base_price": r["base_price"],
                    "lowest_price": r["lowest_price"],
                    "img_hdfs_path": r["img_hdfs_path"],
                    "category_code": r["category_code"],
                    "mall_name": r["mall_name"],
                    "create_dt": r["create_dt"].isoformat() if r["create_dt"] else None,
                }
                for r in rows
            ],
        }
    except Exception as e:
        logger.error(f"좋아요 목록 조회 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")
@router.get("", response_model=ProductListResponse)
async def list_products(
    page: int = Query(1, ge=1, description="페이지 번호"),
    page_size: int = Query(20, ge=1, le=100, description="페이지 크기"),
    category: str = Query(None, description="카테고리 필터"),
    keyword: str = Query(None, description="상품명 검색"),
):
    """
    검색어 및 대분류 카테고리 속성에 맞춰서 PostgreSQL의 상품 테이블을 페이지네이션(Offset 지정) 방식으로 불러옴.
    어드민 대시보드의 상품 관리 메뉴 보드 혹은 전체 리스트 페이지 렌더링에 사용됨.

    Args:
        page (int, optional): 보여줄 페이지 뎁스 숫자 (1부터 시작).
        page_size (int, optional): 1페이지당 포함할 최대 로우 스토어 데이터량.
        category (str, optional): 포함될 카테고리 필터 (선택적).
        keyword (str, optional): 상품 이름의 일부를 나타내는 검색어 (선택적).

    Returns:
        ProductListResponse: 메타데이터(총 페이지 등)와 아이템 목록을 들고 있는 Pagination 패키지 객체.
    """
    try:
        offset = (page - 1) * page_size
        conditions = []
        params = []

        if category:
            conditions.append("category_code = %s")
            params.append(category)
        if keyword:
            conditions.append("prod_name ILIKE %s")
            params.append(f"%{keyword}%")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with get_pg_cursor() as cur:
            # 전체 개수
            cur.execute(f"SELECT COUNT(*) as cnt FROM products {where_clause}", params)
            total = cur.fetchone()["cnt"]

            # 데이터 조회
            cur.execute(
                f"""
                SELECT product_id, model_code, prod_name,
                       base_price, category_code, img_hdfs_path, create_dt, update_dt
                FROM products
                {where_clause}
                ORDER BY product_id DESC
                LIMIT %s OFFSET %s
                """,
                params + [page_size, offset],
            )
            rows = cur.fetchall()

        return ProductListResponse(
            items=[ProductResponse(**r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
            total_pages=math.ceil(total / page_size) if total > 0 else 0,
        )

    except Exception as e:
        logger.error(f"상품 목록 조회 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# 상품 상세 조회 (PG + MongoDB 조합)
# ──────────────────────────────────────
@router.get("/{product_id}", response_model=ProductDetailResponse)
async def get_product_detail(product_id: str):
    """
    단건 상품에 대해 RDBMS의 정형 데이터(기본가, 최저가 채널, 특징)와 NoSQL의 비정형 긴 텍스트(HTML Detail)를 모두 모아 조합함.
    상품 상세 페이지(PDP) 노출 시 요구되는 모든 디테일 조각들을 한 번에 서빙하여 클라이언트 I/O를 줄임.

    Args:
        product_id (str): 조합 애그리게이션 대상이 되는 기본키 UUID 문자열.

    Returns:
        ProductDetailResponse: PostgreSQL 정보와 MongoDB 텍스트가 중첩된 최종 병합 Pydantic 객체.
    """
    try:
        # 1. PostgreSQL: 상품 기본 정보
        with get_pg_cursor() as cur:
            cur.execute(
                """
                SELECT product_id, model_code, prod_name,
                       base_price, category_code, img_hdfs_path, create_dt, update_dt
                FROM products WHERE product_id = %s
                """,
                (product_id,),
            )
            product_row = cur.fetchone()

            if not product_row:
                raise HTTPException(status_code=404, detail="상품을 찾을 수 없습니다")

            # 2. PostgreSQL: 네이버 가격 정보
            cur.execute(
                """
                SELECT nprice_id, product_id, rank, naver_price, mall_name, mall_url, create_dt
                FROM naver_prices
                WHERE product_id = %s
                ORDER BY rank ASC
                """,
                (product_id,),
            )
            price_rows = cur.fetchall()

            # 3. PostgreSQL: 상품 특징 (detected_desc)
            cur.execute(
                "SELECT detected_desc FROM product_features WHERE product_id = %s",
                (product_id,),
            )
            feature_row = cur.fetchone()

        # 4. MongoDB: 상세 설명
        detail_desc = None
        try:
            db = get_mongo_db()
            mongo_doc = db.product_details.find_one(
                {"product_id": product_id},
                {"_id": 0, "detail_desc": 1},
            )
            if mongo_doc:
                detail_desc = mongo_doc.get("detail_desc")
        except Exception as mongo_err:
            logger.warning(f"MongoDB 조회 실패 (무시): {mongo_err}")

        return ProductDetailResponse(
            product=ProductResponse(**product_row),
            detail_desc=detail_desc,
            detected_desc=feature_row["detected_desc"] if feature_row else None,
            naver_prices=[NaverPriceResponse(**r) for r in price_rows],
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"상품 상세 조회 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# 상품 등록
# ──────────────────────────────────────
@router.post("", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(req: ProductCreateRequest):
    """
    관리자용 백오피스 또는 연동 파이프라인에서 신규 상품의 메타데이터를 Postgresql에 밀어넣음.

    Args:
        req (ProductCreateRequest): 필수 식별자와 가격/이미지 필드들을 지닌 페이로드.

    Returns:
        ProductResponse: 생성된 상품 ROW의 전체 덤프 정보.
    """
    try:
        with get_pg_cursor() as cur:
            cur.execute(
                """
                INSERT INTO products (model_code, prod_name,
                                      base_price, category_code, img_hdfs_path)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING product_id, model_code, prod_name,
                          base_price, category_code, img_hdfs_path, create_dt, update_dt
                """,
                (
                    req.model_code,
                    req.prod_name,
                    req.base_price,
                    req.category_code,
                    req.img_hdfs_path,
                ),
            )
            row = cur.fetchone()

        return ProductResponse(**row)

    except Exception as e:
        logger.error(f"상품 등록 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# 상품 삭제
# ──────────────────────────────────────
@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(product_id: str):
    """
    판매 중단/어드민 삭제 시, 외래키 조건으로 결합된 가격, 히스토리, NoSQL 등을 롤백 캐스캐이드 삭제.
    고립된 고아(Orphan) 데이터를 남기지 않기 위해 연계된 테이블 삭제를 트랜잭션 단위로 함께 처리함.

    Args:
        product_id (str): 지우고자 하는 아이템 식별자.
    """
    try:
        with get_pg_cursor() as cur:
            # 관련 데이터 먼저 삭제 (FK 제약 때문)
            cur.execute("DELETE FROM search_logs WHERE nprice_id IN (SELECT nprice_id FROM naver_prices WHERE product_id = %s)", (product_id,))
            cur.execute("DELETE FROM naver_prices WHERE product_id = %s", (product_id,))
            cur.execute("DELETE FROM product_features WHERE product_id = %s", (product_id,))
            cur.execute("DELETE FROM products WHERE product_id = %s RETURNING product_id", (product_id,))
            row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="상품을 찾을 수 없습니다")

        # MongoDB에서도 삭제
        try:
            db = get_mongo_db()
            db.product_details.delete_one({"product_id": product_id})
        except Exception:
            pass

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"상품 삭제 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# 최근 본 상품 기록
# ──────────────────────────────────────
@router.post("/{product_id}/view")
async def record_product_view(product_id: str, request: Request):
    """
    클라이언트가 상품 상세 페이지에 진입할 때마다 호출되어, 해당 유저의 최근 본 상품 내역을 갱신함.
    검색이나 개인화 추천 풀(Pool)의 품질을 높이기 위한 로그성 팩트 테이블(recent_views)에 적재.

    Args:
        product_id (str): 누적될 대상 상품 식별자.
        request (Request): 유저 세션을 확인하기 위한 리퀘스트.

    Returns:
        dict: 정상 반영을 뜻하는 success 플래그.
    """
    session = _get_user_from_session(request)
    user_id = session.get("user_id") if session else None

    if not user_id:
        return {"success": False, "message": "로그인이 필요합니다"}

    try:
        with get_pg_cursor() as cur:
            # 기존 기록이 있으면 시간만 업데이트, 없으면 새로 추가
            cur.execute(
                """
                INSERT INTO recent_views (user_id, product_id, view_dt)
                VALUES (%s, %s, NOW())
                ON CONFLICT (user_id, product_id)
                DO UPDATE SET view_dt = NOW()
                """,
                (user_id, product_id),
            )
        return {"success": True}
    except Exception as e:
        logger.error(f"상품 조회 기록 실패: {e}")
        return {"success": False, "message": "기록 실패"}


# ──────────────────────────────────────
# 최근 본 상품 목록 조회
# ──────────────────────────────────────
# ──────────────────────────────────────
@router.post("/{product_id}/like")
async def add_like(product_id: str, request: Request):
    """
    관심 상품으로 표시하기 위한 사용자의 좋아요(Like/찜) 상태를 DB에 Insert.
    선호도(Preference) 기반의 Collaborative Filtering 모델이나 개인 맞춤형 기획전 노출에 사용하기 위함.

    Args:
        product_id (str): 좋아요 대상 상품.
        request (Request): 식별 세션.

    Returns:
        dict: 좋아요 버튼 UI 활성화를 위한 상태 플래그.
    """
    session = _get_user_from_session(request)
    user_id = session.get("user_id") if session else None

    if not user_id:
        return {"success": False, "message": "로그인이 필요합니다"}

    try:
        with get_pg_cursor() as cur:
            cur.execute(
                """
                INSERT INTO likes (user_id, product_id)
                VALUES (%s, %s)
                ON CONFLICT (user_id, product_id) DO NOTHING
                """,
                (user_id, product_id),
            )
        return {"success": True, "liked": True}
    except Exception as e:
        logger.error(f"좋아요 추가 실패: {e}")
        return {"success": False, "message": "좋아요 실패"}


# ──────────────────────────────────────
# 좋아요 취소
# ──────────────────────────────────────
@router.delete("/{product_id}/like")
async def remove_like(product_id: str, request: Request):
    """
    기존에 활성화했던 좋아요 상태를 해제 처리함.
    단순 클릭 실수 혹은 유저 선호도 변경에 대응해 즉시 `likes` 테이블에서 물리 레코드를 제거.

    Args:
        product_id (str): 대상 상품 UUID.
        request (Request): HTTP 요청.

    Returns:
        dict: UI의 좋아요 상태 해제를 지시하는 플래그.
    """
    session = _get_user_from_session(request)
    user_id = session.get("user_id") if session else None

    if not user_id:
        return {"success": False, "message": "로그인이 필요합니다"}

    try:
        with get_pg_cursor() as cur:
            cur.execute(
                """
                DELETE FROM likes
                WHERE user_id = %s AND product_id = %s
                """,
                (user_id, product_id),
            )
        return {"success": True, "liked": False}
    except Exception as e:
        logger.error(f"좋아요 취소 실패: {e}")
        return {"success": False, "message": "좋아요 취소 실패"}


# ──────────────────────────────────────
# 좋아요 상태 확인
# ──────────────────────────────────────
@router.get("/{product_id}/like-status")
async def get_like_status(product_id: str, request: Request):
    """
    상세페이지 등에서 현 진입 유저가 해당 상품에 좋아요를 눌렀는지 여부를 사전 검증해 버튼의 초기 색상을 결정함.
    쇼핑몰 렌더링 시 UX 향상을 목적으로 사용.

    Args:
        product_id (str): 검증 대상.
        request (Request): 로그인 사용자 판별 객체.

    Returns:
        dict: 해당 상품의 찜(Liked) 상태(True/False).
    """
    session = _get_user_from_session(request)
    user_id = session.get("user_id") if session else None

    if not user_id:
        return {"success": True, "liked": False}

    try:
        with get_pg_cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM likes
                WHERE user_id = %s AND product_id = %s
                """,
                (user_id, product_id),
            )
            exists = cur.fetchone() is not None

        return {"success": True, "liked": exists}
    except Exception as e:
        logger.error(f"좋아요 상태 확인 실패: {e}")
        return {"success": False, "liked": False}


# ──────────────────────────────────────
# 좋아요 목록 조회
# ──────────────────────────────────────
