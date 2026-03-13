"""
상품 검색 관련 외부 API 통신 및 DB 기록을 담당하는 라우터 모듈.
- 클라이언트(프론트엔드)의 복합 조건(이미지, 텍스트, 카테고리 등) 검색 요청 처리를 위해 구성.
- 의존성 서비스들을 조합하여 실제 비즈니스 흐름(검색 -> DB 로깅 -> 응답)을 완성함.
"""
from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form, Request
from fastapi.responses import Response
from typing import Optional
import logging
import json
import os
import httpx

from ..database import get_pg_cursor, get_redis
from ..models.search import (
    SearchByTextRequest,
    SearchResultResponse,
    SimilarProductResponse,
    SearchLogResponse,
    ImageSearchResponse,
    ProductResult,
    SearchHistoryItem,
    SearchHistoryListResponse,
)
from ..services.image_service import image_service
from ..services.search_service import search_service

from ..config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/search", tags=["검색"])


# ──────────────────────────────────────
# 헬퍼: Redis 세션에서 user_id 추출
# ──────────────────────────────────────
def _get_user_from_session(request: Request) -> Optional[dict]:
    """
    HTTP Request 쿠키에 저장된 세션 토큰으로 Redis 캐시에서 사용자 정보를 복원함.
    비로그인 사용자도 검색 기능을 이용할 수 있으므로, 세션 유무만 판별하여 반환.

    Args:
        request (Request): FastAPI HTTP Request 객체.

    Returns:
        Optional[dict]: Redis에서 가져온 사용자 데이터. 세션이 만료되거나 없으면 None 반환.
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
        logger.warning(f"세션 조회 실패: {e}")
    return None


# ──────────────────────────────────────
# 이미지 기반 유사 상품 검색 (메인)
# ──────────────────────────────────────
@router.post("/by-image", response_model=ImageSearchResponse)
async def search_by_image(
    request: Request,
    image: Optional[UploadFile] = File(None, description="검색할 이미지 (선택)"),
    search_text: Optional[str] = Form(None, description="추가 검색어"),
    gender: Optional[str] = Form(None, description="성별 필터"),
    category: Optional[str] = Form(None, description="의류 카테고리 필터"),
):
    """
    클라이언트의 다중 인자로 상품 유사도 검색을 수행하고 조회 로그를 남김.
    - AI 모델 파이프라인(ML Engine)과 DB(PostgreSQL/HDFS) 전략을 결합하기 위해 사용.
    - 검색 처리 속도 최적화를 위해 이미지 업로드와 머신러닝 벡터 추론 작업을 비동기로 처리.

    Args:
        request (Request): 세션 정보에 접근하기 위한 Request 객체.
        image (Optional[UploadFile]): 사용자가 업로드한 기준 이미지.
        search_text (Optional[str]): 텍스트로 된 추가 검색 조건(예: '하객룩').
        gender (Optional[str]): 노출 상품 성별 제한(men, women).
        category (Optional[str]): 노출 상품 카테고리 제한(top, bottom 등).

    Returns:
        ImageSearchResponse: 병합된 최종 검색 결과와 추후 이력을 조회할 수 있는 로그 ID.

    Raises:
        HTTPException: 검색어와 이미지 보두 누락된 경우(400) 또는 내부 서비스(ML, DB) 에러 발생 시(500).
    """
    session = _get_user_from_session(request)
    user_id = session.get("user_id") if session else None
    
    # 로그인 체크 제거 - 비로그인 사용자도 검색 가능
    # user_id는 검색 로그 저장 시 선택적으로 사용

    # 검색 조건 검증
    if not image and not search_text:
        raise HTTPException(status_code=400, detail="이미지 또는 검색어를 입력해주세요")

    try:
        # Initialize variables for image processing results
        image_info = {
            "image_id": None,
            "hdfs_thumb_path": None,
            "file_size": None,
            "width": None,
            "height": None,
            "hdfs_uploaded": False,
        }
        
        # 이미지가 있을 경우에만 처리
        if image:
            # 1. 파일 검증
            image_service.validate_image_file(image)

            # 2. 메모리에서 썸네일 생성 + HDFS 업로드
            result = await image_service.process_and_upload_thumbnail(image, str(user_id))
            image_info.update(result)
            logger.info(
                f"이미지 처리 완료: user={user_id}, id={image_info['image_id']}, "
                f"hdfs={'✅' if image_info['hdfs_uploaded'] else '❌'}"
            )

        # 3. ML 엔진 호출: 벡터 검색 Top-K(product_id -> score) 확보
        ml_scores = None
        try:
            data = {}
            files = {}

            if search_text:
                data["text"] = search_text
            if gender:
                data["gender"] = gender
            if category:
                data["category"] = category

            if image:
                # 업로드 파일은 앞단 처리에서 이미 한 번 읽혔을 수 있으므로 포인터를 되감는다.
                await image.seek(0)
                files["image"] = (
                    image.filename or "query.jpg",
                    await image.read(),
                    image.content_type or "application/octet-stream",
                )

            # ML 경로는 임베딩 + ES 검색까지 포함되므로 read timeout을 넉넉히 둔다.
            settings = get_settings()
            ML_ENGINE_URL = settings.ML_ENGINE_URL
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout=60.0, connect=5.0, read=60.0)
            ) as client:
                ml_resp = await client.post(ML_ENGINE_URL, data=data, files=files)
                ml_resp.raise_for_status()
                ml_data = ml_resp.json()
                ml_scores = ml_data.get("ml_product_scores")
        except Exception as ml_err:
            # ML 경로 실패 시 DB fallback으로 자동 전환되도록 None 유지
            logger.warning(
                "ML 엔진 호출 실패, DB fallback 사용: %s: %r",
                type(ml_err).__name__,
                ml_err,
            )

        # 4. 검색 서비스 (전략 1: ML 검색 결과, 전략 2: 텍스트 검색, 전략 3: DB fallback)
        ml_results = await search_service.search_products(
            ml_product_scores=ml_scores,
            category=category,
            gender=gender,
            limit=6,
        )
        result_count = len(ml_results)

        # 5. DB에 검색 로그 기록
        # 기존 "남자_상의" 형식의 임시 호환 처리 및 분리 처리
        gender_filter = gender
        category_filter = category
        if category and "_" in category and not gender:
            parts = category.split("_", 1)
            gender_filter, category_filter = parts[0], parts[1]

        log_id = None
        try:
            with get_pg_cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO search_logs (
                        user_id, input_img_path, thumbnail_path,
                        input_text, applied_category, gender,
                        image_size, image_width, image_height,
                        search_status, result_count
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'completed', %s)
                    RETURNING log_id
                    """,
                    (
                        user_id,
                        None,  # 원본은 저장하지 않음
                        image_info["hdfs_thumb_path"],
                        search_text,
                        category_filter,
                        gender_filter,
                        image_info["file_size"],
                        image_info["width"],
                        image_info["height"],
                        result_count,
                    ),
                )
                row = cur.fetchone()
                log_id = row["log_id"]
        except Exception as db_err:
            logger.error(f"검색 로그 DB 저장 실패: {db_err}")
            raise HTTPException(status_code=500, detail="검색 로그 저장 실패")


        # 6. 검색 결과 DB 저장
        try:
            with get_pg_cursor() as cur:
                for rank, item in enumerate(ml_results, 1):
                    cur.execute(
                        """
                        INSERT INTO search_results (
                            log_id, product_name, brand, price,
                            image_url, mall_name, mall_url, rank
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            log_id,
                            item["product_name"],
                            item["brand"],
                            item["price"],
                            item["image_url"],
                            item["mall_name"],
                            item["mall_url"],
                            rank,
                        ),
                    )
        except Exception as res_err:
            logger.warning(f"검색 결과 저장 실패 (검색은 계속): {res_err}")

        # 7. 응답
        # search_source: 실제 사용된 검색 전략 (프론트엔드 디버깅, 향후 UI에서 활용 가능)
        used_source = ml_results[0]["search_source"] if ml_results else "db"
        return ImageSearchResponse(
            success=True,
            log_id=log_id,
            thumbnail_url=f"/api/search/thumbnail/{log_id}",
            results=[
                ProductResult(
                    product_id=str(r["product_id"]),
                    product_name=r["product_name"],
                    brand=r["brand"],
                    price=r["price"],
                    image_url=r["image_url"],
                    mall_name=r["mall_name"],
                    mall_url=r["mall_url"],
                    similarity_score=r.get("similarity_score"),
                    search_source=r.get("search_source", "db"),
                )
                for r in ml_results
            ],
            result_count=result_count,
            search_source=used_source,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"이미지 검색 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="서버 오류가 발생했습니다")


# ──────────────────────────────────────
# YOLO 의류 객체 탐지 프록시 (UI 선택용)
# ──────────────────────────────────────
@router.post("/detect")
async def detect_apparel(
    request: Request,
    image: UploadFile = File(..., description="의류를 탐지할 원본 이미지")
):
    """
    ML 엔진의 YOLO 객체 탐지 API로 이미지를 단순히 전달(프록시)하고
    바운딩 박스 목록(좌표)만 반환합니다.
    """
    # 설정: ML Engine의 새로운 YOLO 독립 라우터
    YOLO_ENGINE_URL = os.getenv("YOLO_ENGINE_URL", "http://ml-engine:8914/yolo/detect")
    
    try:
        data = await image.read()
        files = {"image": (image.filename or "detect.jpg", data, image.content_type or "image/jpeg")}
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(YOLO_ENGINE_URL, files=files)
            resp.raise_for_status()
            return resp.json()
            
    except Exception as e:
        logger.error(f"YOLO 탐지 프록시 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="객체 탐지 서버 통신 오류가 발생했습니다.")


# ──────────────────────────────────────
# 썸네일 이미지 조회 (HDFS에서 직접 읽기)
# ──────────────────────────────────────
@router.get("/thumbnail/{log_id}")
async def get_thumbnail(log_id: int, request: Request):
    """HDFS에서 썸네일을 읽어서 반환"""
    session = _get_user_from_session(request)
    user_id = session.get("user_id") if session else None

    if not user_id:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")

    try:
        with get_pg_cursor() as cur:
            cur.execute(
                "SELECT thumbnail_path, user_id FROM search_logs WHERE log_id = %s",
                (log_id,),
            )
            row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="이미지를 찾을 수 없습니다")

        if row["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="접근 권한이 없습니다")

        thumb_path = row["thumbnail_path"]
        if not thumb_path:
            raise HTTPException(status_code=404, detail="썸네일이 없습니다")

        # HDFS에서 직접 읽기
        image_data = await image_service.read_thumbnail_from_hdfs(thumb_path)
        if image_data:
            return Response(
                content=image_data,
                media_type="image/jpeg",
                headers={"Cache-Control": "public, max-age=86400"},
            )

        raise HTTPException(status_code=404, detail="이미지 파일을 찾을 수 없습니다")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"썸네일 조회 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# 검색 히스토리 조회 (마이페이지용)
# ──────────────────────────────────────
@router.get("/history", response_model=SearchHistoryListResponse)
async def get_search_history(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """사용자의 검색 히스토리 조회 (최신순, 페이지네이션)"""
    session = _get_user_from_session(request)
    user_id = session.get("user_id") if session else None

    if not user_id:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")

    try:
        with get_pg_cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) as cnt FROM search_logs WHERE user_id = %s",
                (user_id,),
            )
            total = cur.fetchone()["cnt"]

            cur.execute(
                """
                SELECT log_id, thumbnail_path, input_text,
                       applied_category, gender, create_dt, result_count
                FROM search_logs
                WHERE user_id = %s
                ORDER BY create_dt DESC
                LIMIT %s OFFSET %s
                """,
                (user_id, limit, offset),
            )
            rows = cur.fetchall()

        history = [
            SearchHistoryItem(
                log_id=row["log_id"],
                thumbnail_url=f"/api/search/thumbnail/{row['log_id']}" if row["thumbnail_path"] else None,
                search_text=row["input_text"],
                category=row["applied_category"],
                gender=row["gender"],
                create_dt=row["create_dt"],
                result_count=row["result_count"] or 0,
            )
            for row in rows
        ]

        return SearchHistoryListResponse(
            success=True,
            total=total,
            page=offset // limit + 1,
            limit=limit,
            history=history,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"검색 히스토리 조회 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# 검색 히스토리 상세 조회
# ──────────────────────────────────────
@router.get("/history/{log_id}")
async def get_search_history_detail(log_id: int, request: Request):
    """특정 검색의 상세 결과 조회"""
    session = _get_user_from_session(request)
    user_id = session.get("user_id") if session else None

    if not user_id:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")

    try:
        with get_pg_cursor() as cur:
            cur.execute(
                """
                SELECT log_id, thumbnail_path, input_text,
                       applied_category, gender, create_dt, result_count, user_id
                FROM search_logs WHERE log_id = %s
                """,
                (log_id,),
            )
            log_row = cur.fetchone()

            if not log_row:
                raise HTTPException(status_code=404, detail="검색 기록을 찾을 수 없습니다")
            if log_row["user_id"] != user_id:
                raise HTTPException(status_code=403, detail="접근 권한이 없습니다")

            cur.execute(
                """
                SELECT 
                    product_name,
                    brand,
                    price,
                    image_url,
                    mall_name,
                    mall_url,
                    rank
                FROM search_results
                WHERE log_id = %s 
                ORDER BY rank ASC
                """,
                (log_id,),
            )
            result_rows = cur.fetchall()

        return {
            "success": True,
            "log_id": log_row["log_id"],
            "thumbnail_url": f"/api/search/thumbnail/{log_row['log_id']}" if log_row["thumbnail_path"] else None,
            "search_text": log_row["input_text"],
            "category": log_row["applied_category"],
            "gender": log_row["gender"],
            "create_dt": log_row["create_dt"].isoformat() if log_row["create_dt"] else None,
            "results": [
                {
                    "product_name": r["product_name"],
                    "brand": r["brand"],
                    "price": r["price"],
                    "image_url": r["image_url"],
                    "mall_name": r["mall_name"],
                    "mall_url": r["mall_url"],
                }
                for r in result_rows
            ],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"검색 상세 조회 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# 검색 히스토리 전체 삭제
# ──────────────────────────────────────
@router.delete("/history")
async def delete_all_search_history(request: Request):
    """사용자의 검색 히스토리 전체 삭제"""
    session = _get_user_from_session(request)
    user_id = session.get("user_id") if session else None

    if not user_id:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")

    try:
        with get_pg_cursor() as cur:
            cur.execute("DELETE FROM search_logs WHERE user_id = %s", (user_id,))
            deleted_count = cur.rowcount

        return {
            "success": True,
            "message": f"검색 히스토리 {deleted_count}건이 삭제되었습니다",
            "deleted_count": deleted_count,
        }

    except Exception as e:
        logger.error(f"검색 히스토리 삭제 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# 텍스트 기반 유사 상품 검색 (기존 유지)
# ──────────────────────────────────────
@router.post("/by-text", response_model=SearchResultResponse)
async def search_by_text(
    req: SearchByTextRequest,
    user_id: Optional[str] = Query(None, description="사용자 ID (검색 로그용)"),
):
    """텍스트 키워드 검색 (ML 연동 전 DB 검색으로 대체)"""
    try:
        with get_pg_cursor() as cur:
            # 기본 검색어 필터
            conditions = ["prod_name ILIKE %s"]
            params = [f"%{req.query}%"]

            # 성별 필터 추가
            if req.gender:
                conditions.append("gender = %s")
                params.append(req.gender.lower())

            # 카테고리 필터 추가 (SearchByTextRequest에 존재하므로 지원 가능)
            if req.category:
                cat_vals = search_service._category_filter_values(req.category)
                if cat_vals:
                    placeholders = ",".join(["%s"] * len(cat_vals))
                    conditions.append(f"LOWER(category_code) IN ({placeholders})")
                    params.extend(cat_vals)

            params.append(req.top_k)
            query = f"""
                SELECT product_id, prod_name, base_price, img_hdfs_path
                FROM products
                WHERE {" AND ".join(conditions)}
                ORDER BY product_id DESC LIMIT %s
            """
            cur.execute(query, tuple(params))
            rows = cur.fetchall()

        results = [
            SimilarProductResponse(
                product_id=str(r["product_id"]),
                prod_name=r["prod_name"],
                base_price=r["base_price"],
                img_hdfs_path=r["img_hdfs_path"],
            )
            for r in rows
        ]

        if user_id:
            try:
                with get_pg_cursor() as cur:
                    cur.execute(
                        "INSERT INTO search_logs (user_id, input_text, applied_category, gender) VALUES (%s, %s, %s, %s)",
                        (user_id, req.query, req.category, req.gender),
                    )
            except Exception as log_err:
                logger.warning(f"검색 로그 저장 실패: {log_err}")

        return SearchResultResponse(results=results, total=len(results), query_type="text")

    except Exception as e:
        logger.error(f"텍스트 검색 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")


# ──────────────────────────────────────
# 검색 로그 조회 (레거시 호환)
# ──────────────────────────────────────
@router.get("/logs/{user_id}", response_model=list[SearchLogResponse])
async def get_search_logs(
    user_id: str,
    limit: int = Query(20, ge=1, le=100),
):
    """사용자의 최근 검색 기록 조회 (레거시 API)"""
    try:
        with get_pg_cursor() as cur:
            cur.execute(
                """
                SELECT log_id, user_id, input_img_path, input_text,
                       applied_category, gender, create_dt
                FROM search_logs WHERE user_id = %s
                ORDER BY create_dt DESC LIMIT %s
                """,
                (user_id, limit),
            )
            rows = cur.fetchall()

        return [SearchLogResponse(**r) for r in rows]

    except Exception as e:
        logger.error(f"검색 로그 조회 실패: {e}")
        raise HTTPException(status_code=500, detail="서버 오류")
