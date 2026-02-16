"""
상품 라우터 - 상품 CRUD + 상세 조회
PostgreSQL (products, naver_prices, product_features) + MongoDB (product_details)
"""
from fastapi import APIRouter, HTTPException, Query, status, Request
import math
import logging

from ..database import get_pg_cursor, get_mongo_db
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
    """Redis 세션에서 현재 로그인한 사용자 정보를 가져옵니다."""
    from ..database import get_redis
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
    """최근 본 상품 목록 조회"""
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
                    COALESCE(np.price, p.base_price) as lowest_price,
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
                    "product_id": r["product_id"],
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
    """좋아요 목록 조회"""
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
                    COALESCE(np.price, p.base_price) as lowest_price,
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
                    "product_id": r["product_id"],
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
    """상품 목록 조회 (페이징)"""
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
async def get_product_detail(product_id: int):
    """상품 상세 정보 (PostgreSQL + MongoDB 조합)"""
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
                SELECT nprice_id, product_id, rank, price, mall_name, mall_url, create_dt
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
    """상품 등록"""
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
async def delete_product(product_id: int):
    """상품 삭제"""
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
async def record_product_view(product_id: int, request: Request):
    """상품 조회 기록 (최근 본 상품)"""
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
async def add_like(product_id: int, request: Request):
    """좋아요 추가"""
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
async def remove_like(product_id: int, request: Request):
    """좋아요 취소"""
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
async def get_like_status(product_id: int, request: Request):
    """좋아요 상태 확인"""
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
