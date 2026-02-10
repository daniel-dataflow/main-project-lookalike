"""
상품 라우터 - 상품 CRUD + 상세 조회
PostgreSQL (products, naver_prices, product_features) + MongoDB (product_details)
"""
from fastapi import APIRouter, HTTPException, Query, status
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

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/products", tags=["상품"])


# ──────────────────────────────────────
# 상품 목록 (페이징)
# ──────────────────────────────────────
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
                SELECT product_id, origine_prod_id, model_code, prod_name,
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
                SELECT product_id, origine_prod_id, model_code, prod_name,
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
                INSERT INTO products (origine_prod_id, model_code, prod_name,
                                      base_price, category_code, img_hdfs_path)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING product_id, origine_prod_id, model_code, prod_name,
                          base_price, category_code, img_hdfs_path, create_dt, update_dt
                """,
                (
                    req.origine_prod_id,
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
