"""
검색 서비스 추상화 레이어 (전략 패턴)
--------------------------------------
ML/Elasticsearch 연동을 고려한 검색 전략 선택:

  1. ES kNN 벡터 검색   - ES 가용 + query_embedding 제공 시 (ML 연동 후)
  2. ES 텍스트 검색     - ES 가용 + query_text만 있을 시
  3. DB fallback        - ES 연결 불가 또는 위 조건 미충족 시 (현재 기본 동작)

호출부(search.py)는 이 모듈만 바라보면 되며,
ML 파이프라인 완성 후 query_embedding을 넘기는 것만으로 자동으로 kNN 검색으로 전환됩니다.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────

async def search_products(
    query_text: Optional[str] = None,
    query_embedding: Optional[list] = None,
    category: Optional[str] = None,
    limit: int = 4,
) -> list:
    """
    유사 상품 검색 (전략 자동 선택)

    Args:
        query_text:      사용자 검색어 (텍스트 검색용)
        query_embedding: ML 모델이 추출한 쿼리 임베딩 벡터 (향후 ML 서버에서 전달)
        category:        카테고리 필터 (None 또는 '전체' = 전체 조회)
        limit:           반환할 결과 수

    Returns:
        list of dict: [
            {
                product_id, product_name, brand, price,
                image_url, mall_name, mall_url,
                similarity_score,    # float (0.0~1.0) or None (DB fallback)
                search_source,       # "elasticsearch_knn" | "db"
            },
            ...
        ]
    """
    # 전략 1: ES kNN 벡터 검색 (ML 파이프라인 연동 후 활성화)
    if query_embedding is not None:
        try:
            return await _search_by_knn(query_embedding, category, limit)
        except Exception as e:
            logger.warning(f"ES kNN 검색 실패, fallback to DB: {e}")

    # 전략 2: DB fallback (현재 기본 동작 - ES 벡터 입력이 없거나 실패할 때 항상 작동)
    return _search_by_db(category, limit)


# ──────────────────────────────────────────────────────────────────────
# 전략 1: ES kNN 벡터 검색 (ML 임베딩 연동 후 활성화됨)
# ──────────────────────────────────────────────────────────────────────

async def _search_by_knn(
    query_vector: list,
    category: Optional[str],
    limit: int,
) -> list:
    """
    Elasticsearch kNN API를 이용한 벡터 유사도 검색.
    ML 파이프라인이 임베딩을 ES products 인덱스에 저장한 후 활성화됩니다.
    """
    from ..core.elasticsearch_setup import get_es_client

    es = get_es_client()
    
    # query_vector 차원에 따라 조회 필드를 동적으로 선택 (image: 512차원, text: 384차원)
    vec_len = len(query_vector)
    if vec_len == 512:
        target_field = "image_vector"
    elif vec_len == 384:
        target_field = "text_vector"
    else:
        target_field = "embedding"
        
    knn_query = {
        "knn": {
            "field": target_field,
            "query_vector": query_vector,
            "k": limit,
            "num_candidates": limit * 10,
        }
    }

    # 카테고리 필터 적용
    if category and category != "전체":
        knn_query["knn"]["filter"] = {"term": {"category": category}}

    response = es.search(index="products", body=knn_query, size=limit)
    hits = response["hits"]["hits"]

    if not hits:
        raise ValueError("ES kNN 검색 결과 없음 - fallback 필요")

    logger.info(f"ES kNN 검색 완료: {len(hits)}개 (카테고리: {category or '전체'})")

    # ES 결과에서 product_code 또는 product_id와 매칭 점수 추출
    product_scores = {}
    for hit in hits:
        src = hit["_source"]
        key = src.get("product_code")
        if not key:
            key = str(src.get("product_id"))
        product_scores[key] = hit.get("_score", 0.0)
    
    # DB에서 최신 데이터(가격, 쇼핑몰 정보 등) 덧씌우기
    return _hydrate_from_db(product_scores, source="elasticsearch_knn")


# ──────────────────────────────────────────────────────────────────────
# DB 수화(Hydration) 헬퍼
# ──────────────────────────────────────────────────────────────────────

def _hydrate_from_db(product_scores: dict, source: str) -> list:
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
                WHERE p.model_code IN ({placeholders}) OR p.product_id::text IN ({placeholders})
                """,
                tuple(product_ids) + tuple(product_ids),
            )
            rows = cur.fetchall()

        # 결과를 list of dict로 변환하고 유사도 점수 삽입
        products = []
        seen = set()
        for row in rows:
            model_code = row["model_code"]
            pid = str(row["product_id"])
            
            score_key = model_code if model_code in product_scores else pid
            if score_key not in product_scores:
                continue
                
            if score_key in seen:
                continue
            seen.add(score_key)

            products.append({
                "product_id": pid,
                "product_name": row["prod_name"],
                "brand": row["brand_name"],
                "price": row["lowest_price"],
                "image_url": row["img_hdfs_path"] or "https://placehold.co/300x300?text=No+Image",
                "mall_name": row["mall_name"] or "공식몰",
                "mall_url": row["mall_url"] or "#",
                "similarity_score": round(product_scores.get(score_key, 0.0), 4),
                "search_source": source,
            })

        # 원래 ES에서 반환된 스코어 순서대로 내림차순 정렬 (Hydration 후 순서 유지)
        products.sort(key=lambda x: x["similarity_score"] or 0.0, reverse=True)
        return products

    except Exception as e:
        logger.error(f"DB Hydration 실패: {e}", exc_info=True)
        return []


# ──────────────────────────────────────────────────────────────────────
# 전략 3: DB fallback (현재 기본 동작, 항상 성공 보장)
# ──────────────────────────────────────────────────────────────────────

def _search_by_db(category: Optional[str], limit: int) -> list:
    """
    PostgreSQL에서 상품을 조회합니다 (성별+카테고리 필터, RANDOM 정렬).
    ES가 불가하거나 초기 구동 상태일 때 항상 동작하는 안전망입니다.

    category 형식:
      - "남자_상의"  → gender='남자' AND category_code='상의'
      - "여자_아우터" → gender='여자' AND category_code='아우터'
      - None / ""   → 전체 조회
    """
    from ..database import get_pg_cursor

    # category 파싱: "성별_카테고리" 형식 분리
    gender = None
    category_code = None
    if category:
        parts = category.split("_", 1)
        if len(parts) == 2:
            gender, category_code = parts[0], parts[1]
        else:
            category_code = parts[0]

    try:
        with get_pg_cursor() as cur:
            conditions = []
            params: list = []

            if gender:
                conditions.append("p.gender = %s")
                params.append(gender)
            if category_code:
                conditions.append("p.category_code = %s")
                params.append(category_code)

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
                "image_url": row["img_hdfs_path"] or "https://placehold.co/300x300?text=No+Image",
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


