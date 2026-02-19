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
                search_source,       # "elasticsearch_knn" | "elasticsearch_text" | "db"
            },
            ...
        ]
    """
    # 전략 1: ES kNN 벡터 검색 (ML 파이프라인 연동 후 활성화)
    if query_embedding is not None:
        try:
            return await _search_by_knn(query_embedding, category, limit)
        except Exception as e:
            logger.warning(f"ES kNN 검색 실패, fallback to text/DB: {e}")

    # 전략 2: ES 텍스트 검색 (query_text가 있을 때)
    if query_text:
        try:
            return await _search_by_text_es(query_text, category, limit)
        except Exception as e:
            logger.warning(f"ES 텍스트 검색 실패, fallback to DB: {e}")

    # 전략 3: DB fallback (현재 기본 동작 - 항상 성공 보장)
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
    knn_query = {
        "knn": {
            "field": "embedding",
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
    return [_hit_to_product(hit, source="elasticsearch_knn") for hit in hits]


# ──────────────────────────────────────────────────────────────────────
# 전략 2: ES 텍스트 검색 (prod_name + detected_desc 대상)
# ──────────────────────────────────────────────────────────────────────

async def _search_by_text_es(
    query_text: str,
    category: Optional[str],
    limit: int,
) -> list:
    """
    Elasticsearch multi_match를 이용한 텍스트 검색.
    products 인덱스의 prod_name, detected_desc(VLM 설명) 필드를 검색합니다.
    """
    from ..core.elasticsearch_setup import get_es_client

    es = get_es_client()

    must_clause = {
        "multi_match": {
            "query": query_text,
            "fields": ["prod_name^2", "detected_desc", "brand_name"],
            "type": "best_fields",
            "fuzziness": "AUTO",
        }
    }

    filter_clause = []
    if category and category != "전체":
        filter_clause.append({"term": {"category": category}})

    body = {
        "query": {
            "bool": {
                "must": must_clause,
                "filter": filter_clause,
            }
        },
        "size": limit,
    }

    response = es.search(index="products", body=body)
    hits = response["hits"]["hits"]

    if not hits:
        raise ValueError("ES 텍스트 검색 결과 없음 - fallback 필요")

    logger.info(f"ES 텍스트 검색 완료: {len(hits)}개 (쿼리: {query_text!r})")
    return [_hit_to_product(hit, source="elasticsearch_text") for hit in hits]


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


# ──────────────────────────────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────────────────────────────

def _hit_to_product(hit: dict, source: str) -> dict:
    """ES hit → 통일된 상품 dict로 변환"""
    src = hit["_source"]
    score = hit.get("_score")  # kNN: cosine 유사도 / text: BM25 점수
    # cosine 유사도는 이미 0~1 범위, BM25는 정규화 없이 그대로 전달
    return {
        "product_id": src["product_id"],
        "product_name": src["prod_name"],
        "brand": src.get("brand_name", ""),
        "price": src.get("lowest_price") or src.get("base_price", 0),
        "image_url": src.get("image_url") or "https://placehold.co/300x300?text=No+Image",
        "mall_name": src.get("mall_name", "공식몰"),
        "mall_url": src.get("mall_url", "#"),
        "similarity_score": round(score, 4) if score is not None else None,
        "search_source": source,
    }
