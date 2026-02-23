"""
검색 라우터 - 유사 상품 검색 (Elasticsearch 벡터 검색) + 검색 로그
"""
from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from typing import Optional
import logging

from ..database import get_pg_cursor, get_redis
from ..models.search import (
    SearchByTextRequest,
    SearchResultResponse,
    SimilarProductResponse,
    SearchLogResponse,
)
from ..config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/search", tags=["검색"])


# ──────────────────────────────────────
# 이미지 기반 유사 상품 검색
# ──────────────────────────────────────
@router.post("/by-image", response_model=SearchResultResponse)
async def search_by_image(
    image: UploadFile = File(..., description="검색할 이미지"),
    category: Optional[str] = Form(None, description="카테고리 필터"),
    top_k: int = Form(10, ge=1, le=50, description="반환 결과 수"),
    user_id: Optional[str] = Form(None, description="사용자 ID (검색 로그용)"),
):
    """
    이미지를 업로드하면 ML 모델로 벡터 추출 후
    Elasticsearch에서 유사 상품을 검색합니다.
    
    ⚠️ 현재는 ML 모델 연동 전이므로 더미 응답을 반환합니다.
    실제 구현 시: 이미지 → 벡터 추출 → ES knn 검색 → 결과 반환
    """
    try:
        # TODO: ML 모델 연동 후 실제 벡터 검색 구현
        # 1. 이미지 읽기
        image_data = await image.read()
        logger.info(f"이미지 검색 요청: {image.filename}, size={len(image_data)} bytes")

        # 2. (TODO) ML 모델로 이미지 → 벡터 변환
        # image_vector = ml_model.extract_features(image_data)

        # 3. (TODO) Elasticsearch knn 검색
        # es_results = es_client.search(index="vector_idx", knn={...})

        # 4. 검색 로그 저장
        if user_id:
            try:
                with get_pg_cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO search_logs (user_id, input_img_path, applied_category)
                        VALUES (%s, %s, %s)
                        """,
                        (user_id, f"upload:{image.filename}", category),
                    )
            except Exception as log_err:
                logger.warning(f"검색 로그 저장 실패: {log_err}")

        # 현재는 빈 결과 반환 (ML 연동 전)
        return SearchResultResponse(
            results=[],
            total=0,
            query_type="image",
        )

    except Exception as e:
        logger.error(f"이미지 검색 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# 텍스트 기반 유사 상품 검색
# ──────────────────────────────────────
@router.post("/by-text", response_model=SearchResultResponse)
async def search_by_text(
    req: SearchByTextRequest,
    user_id: Optional[str] = Query(None, description="사용자 ID (검색 로그용)"),
):
    """
    텍스트를 입력하면 ML 모델로 벡터 추출 후
    Elasticsearch에서 유사 상품을 검색합니다.

    ⚠️ 현재는 ML 모델 연동 전이므로 DB 키워드 검색으로 대체합니다.
    실제 구현 시: 텍스트 → 벡터 추출 → ES knn 검색 → 결과 반환
    """
    try:
        # ML 모델 연동 전: PostgreSQL 키워드 검색으로 대체
        results = []
        with get_pg_cursor() as cur:
            cur.execute(
                """
                SELECT product_id, prod_name, base_price, img_hdfs_path
                FROM products
                WHERE prod_name ILIKE %s
                ORDER BY product_id DESC
                LIMIT %s
                """,
                (f"%{req.query}%", req.top_k),
            )
            rows = cur.fetchall()

            results = [
                SimilarProductResponse(
                    product_id=r["product_id"],
                    prod_name=r["prod_name"],
                    base_price=r["base_price"],
                    img_hdfs_path=r["img_hdfs_path"],
                    similarity_score=0.0,  # 키워드 검색이므로 점수 없음
                )
                for r in rows
            ]

        # 검색 로그 저장
        if user_id:
            try:
                with get_pg_cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO search_logs (user_id, input_text, applied_category)
                        VALUES (%s, %s, %s)
                        """,
                        (user_id, req.query, req.category),
                    )
            except Exception as log_err:
                logger.warning(f"검색 로그 저장 실패: {log_err}")

        return SearchResultResponse(
            results=results,
            total=len(results),
            query_type="text",
        )

    except Exception as e:
        logger.error(f"텍스트 검색 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# 검색 로그 조회 (마이페이지용)
# ──────────────────────────────────────
@router.get("/logs/{user_id}", response_model=list[SearchLogResponse])
async def get_search_logs(
    user_id: str,
    limit: int = Query(20, ge=1, le=100),
):
    """사용자의 최근 검색 기록 조회"""
    try:
        with get_pg_cursor() as cur:
            cur.execute(
                """
                SELECT log_id, user_id, input_img_path, input_text,
                       applied_category, create_dt
                FROM search_logs
                WHERE user_id = %s
                ORDER BY create_dt DESC
                LIMIT %s
                """,
                (user_id, limit),
            )
            rows = cur.fetchall()

        return [SearchLogResponse(**r) for r in rows]

    except Exception as e:
        logger.error(f"검색 로그 조회 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")
