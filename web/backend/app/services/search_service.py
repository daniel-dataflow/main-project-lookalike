"""
검색 서비스 추상화 레이어 (전략 패턴)
--------------------------------------
ML/Elasticsearch 연동을 고려한 검색 전략 선택:

  1. ML 서버 검색        - ML 서버에서 받아온 {product_id: score} 목록이 있을 때 (ES kNN 포함)
  2. ES 텍스트 검색      - ES 가용 + query_text만 있을 시 (향후 전환)
  3. DB fallback         - 상기 조건 미충족 시 (현재 기본 동작)

호출부(search.py)는 이 모듈만 바라보면 되며,
ML 파이프라인 완성 후 ml_product_scores 딕셔너리를 넘기는 것으로 
해당 ID들에 대한 실시간 DB 데이터(최저가 등) 조인을 자동 수행합니다.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _category_filter_values(category: str) -> list[str]:
    """입력 카테고리를 DB category_code 비교용 값 목록으로 확장한다."""
    key = (category or "").strip().lower()
    if not key:
        return []
    category_map = {
        "top": ["top", "상의"],
        "bottom": ["bottom", "하의"],
        "outer": ["outer", "아우터"],
    }
    return category_map.get(key, [key])


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────

async def search_products(
    query_text: Optional[str] = None,
    ml_product_scores: Optional[dict] = None,
    category: Optional[str] = None,
    gender: Optional[str] = None,
    limit: int = 6,
) -> list:
    """
    유사 상품 검색 (전략 자동 선택)

    Args:
        query_text:        사용자 검색어 (텍스트 검색용)
        ml_product_scores: ML 모델이 ES를 조회하여 반환한 {product_id: score} 딕셔너리
        category:          카테고리 필터 (None 또는 '전체' = 전체 조회)
        limit:             반환할 결과 수

    Returns:
        list of dict: [
            {
                product_id, product_name, brand, price,
                image_url, mall_name, mall_url,
                similarity_score,    # float (0.0~1.0) or None (DB fallback)
                search_source,       # "ml_api" | "db"
            },
            ...
        ]
    """
    # 전략 1: ML API에서 받은 ID 목록을 기반으로 DB 데이터 수화 (Hydration)
    if ml_product_scores is not None and len(ml_product_scores) > 0:
        try:
            hydrated = _hydrate_from_db(
                ml_product_scores,
                source="ml_api",
                category=category,
                gender=gender,
                limit=limit,
            )
            if hydrated:
                return hydrated
            # ML 결과는 왔지만 DB 매핑이 0건인 경우 빈 배열을 반환하지 않고 fallback 사용.
            logger.warning("ML 결과 Hydration 0건, fallback to DB")
        except Exception as e:
            logger.warning(f"ML 결과 Hydration 실패, fallback to DB: {e}")

    # 전략 2: DB fallback (현재 기본 동작 - ML 입력이 없거나 실패할 때 항상 작동)
    return _search_by_db(category=category, gender=gender, limit=limit)


# ──────────────────────────────────────────────────────────────────────
# DB 수화(Hydration) 헬퍼
# ──────────────────────────────────────────────────────────────────────



def _hydrate_from_db(
    product_scores: dict,
    source: str,
    category: Optional[str] = None,
    gender: Optional[str] = None,
    limit: int = 6,
) -> list:
    """
    ES에서 반환된 {product_code: score} 목록을 사용하여
    PostgreSQL에서 표시용 부가 데이터(최저가, 쇼핑몰 이름, URL 등)를 채워 넣고,
    원래의 ES 스코어(유사도) 순서를 유지한 채 반환합니다.
    """
    if not product_scores:
        return []

    from ..database import get_pg_cursor
    product_ids = list(product_scores.keys())

    try:
        with get_pg_cursor() as cur:
            # IN clause용 파라미터 생성
            placeholders = ",".join(["%s"] * len(product_ids))
            conditions = [
                f"(p.model_code IN ({placeholders}) OR p.product_id::text IN ({placeholders}))"
            ]
            params: list = list(product_ids) + list(product_ids)

            # ML 경로에서도 사용자가 선택한 성별/카테고리 조건을 강제 적용한다.
            if gender:
                conditions.append("p.gender = %s")
                params.append(gender.lower())
            if category:
                category_values = _category_filter_values(category)
                if category_values:
                    placeholders = ",".join(["%s"] * len(category_values))
                    conditions.append(f"LOWER(p.category_code) IN ({placeholders})")
                    params.extend(category_values)

            where_clause = " AND ".join(conditions)
            cur.execute(
                f"""
                SELECT
                    p.product_id, p.prod_name, p.brand_name,
                    p.base_price, p.img_hdfs_path, p.category_code,
                    p.model_code,
                    COALESCE(np.price, p.base_price) AS lowest_price,
                    np.mall_name, np.mall_url
                FROM products p
                LEFT JOIN naver_prices np
                    ON p.product_id = np.product_id AND np.rank = 1
                WHERE {where_clause}
                """,
                tuple(params),
            )
            rows = cur.fetchall()

        # 결과를 list of dict로 변환하고 유사도 점수 삽입
        # 동일 model_code가 여러 product_id로 들어온 경우가 있어
        # 가격/ID 기준으로 안정적인 대표 1건만 선택한다.
        sorted_rows = sorted(
            rows,
            key=lambda r: (
                r["lowest_price"] if r["lowest_price"] is not None else float("inf"),
                str(r["product_id"]),
            ),
        )

        products = []
        seen_score_keys = set()
        for row in sorted_rows:
            model_code = row["model_code"]
            pid = str(row["product_id"])
            
            score_key = model_code if model_code in product_scores else pid
            if score_key not in product_scores:
                continue
                
            # ML 키(=model_code 또는 product_id) 기준으로 1건만 유지
            if score_key in seen_score_keys:
                continue
            seen_score_keys.add(score_key)

            products.append({
                "product_id": pid,
                "product_name": row["prod_name"],
                "brand": row["brand_name"],
                "price": row["lowest_price"],
                # HDFS 기본 패스 형식 보정 (DB에 '/raw/...' 나 'topten/image/...' 형태로 섞여있을 수 있음)
                "image_url": (f"/{row['img_hdfs_path']}" if row["img_hdfs_path"] and not row["img_hdfs_path"].startswith('/') else row["img_hdfs_path"]) or "https://placehold.co/300x300?text=No+Image",
                "mall_name": row["mall_name"] or "공식몰",
                "mall_url": row["mall_url"] or "#",
                "similarity_score": round(product_scores.get(score_key, 0.0), 4),
                "search_source": source,
            })

        # 원래 ES에서 반환된 스코어 순서대로 내림차순 정렬 (Hydration 후 순서 유지)
        # 점수가 같을 때 product_id를 2차 키로 사용해 순서 흔들림 최소화
        products.sort(
            key=lambda x: (x["similarity_score"] or 0.0, str(x["product_id"])),
            reverse=True,
        )

        # 동일 상품명이 반복 노출되지 않도록 2차 dedupe
        # (동일명 다중 상품코드가 존재하는 현재 데이터셋 보정)
        deduped: list = []
        seen_names = set()
        for item in products:
            name_key = (item["brand"], item["product_name"])
            if name_key in seen_names:
                continue
            seen_names.add(name_key)
            deduped.append(item)

        return deduped[:limit]

    except Exception as e:
        logger.error(f"DB Hydration 실패: {e}", exc_info=True)
        return []


# ──────────────────────────────────────────────────────────────────────
# 전략 3: DB fallback (현재 기본 동작, 항상 성공 보장)
# ──────────────────────────────────────────────────────────────────────

def _search_by_db(category: Optional[str], gender: Optional[str], limit: int) -> list:
    """
    PostgreSQL에서 상품을 조회합니다 (성별+카테고리 필터, RANDOM 정렬).
    ES가 불가하거나 초기 구동 상태일 때 항상 동작하는 안전망입니다.
    """
    from ..database import get_pg_cursor

    category_code = category

    try:
        with get_pg_cursor() as cur:
            conditions = []
            params: list = []

            if gender:
                conditions.append("p.gender = %s")
                params.append(gender.lower())
            if category_code:
                category_values = _category_filter_values(category_code)
                if category_values:
                    placeholders = ",".join(["%s"] * len(category_values))
                    conditions.append(f"LOWER(p.category_code) IN ({placeholders})")
                    params.extend(category_values)

            where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            params.append(limit)

            cur.execute(
                f"""
                SELECT
                    p.product_id, p.prod_name, p.brand_name,
                    p.base_price, p.img_hdfs_path, p.category_code,
                    COALESCE(np.price, p.base_price) AS lowest_price,
                    np.mall_name, np.mall_url
                FROM products p
                LEFT JOIN naver_prices np
                    ON p.product_id = np.product_id AND np.rank = 1
                {where_clause}
                ORDER BY RANDOM()
                LIMIT %s
                """,
                params,
            )
            rows = cur.fetchall()

        filter_desc = f"gender={gender}, category={category_code}" if (gender or category_code) else "전체"
        logger.info(f"DB fallback 검색 완료: {len(rows)}개 ({filter_desc})")
        return [
            {
                "product_id": row["product_id"],
                "product_name": row["prod_name"],
                "brand": row["brand_name"],
                "price": row["lowest_price"],
                "image_url": (f"/{row['img_hdfs_path']}" if row["img_hdfs_path"] and not row["img_hdfs_path"].startswith('/') else row["img_hdfs_path"]) or "https://placehold.co/300x300?text=No+Image",
                "mall_name": row["mall_name"] or "공식몰",
                "mall_url": row["mall_url"] or "#",
                "similarity_score": None,        # DB fallback은 유사도 점수 없음
                "search_source": "db",
            }
            for row in rows
        ]

    except Exception as e:
        logger.error(f"DB fallback 검색 실패: {e}", exc_info=True)
        return []
