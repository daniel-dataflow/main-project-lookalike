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
from ..database import get_pg_cursor

logger = logging.getLogger(__name__)


class SearchService:
    def _category_filter_values(self, category: str) -> list[str]:
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

    async def search_products(
        self,
        query_text: Optional[str] = None,
        ml_product_scores: Optional[dict] = None,
        category: Optional[str] = None,
        gender: Optional[str] = None,
        limit: int = 6,
    ) -> list:
        """
        유사 상품 검색 (전략 자동 선택)
        """
        if ml_product_scores is not None and len(ml_product_scores) > 0:
            try:
                hydrated = self._hydrate_from_db(
                    ml_product_scores,
                    source="ml_api",
                    category=category,
                    gender=gender,
                    limit=limit,
                )
                if hydrated:
                    return hydrated[:limit]
                logger.warning("ML 결과 Hydration 0건, fallback to DB")
            except Exception as e:
                logger.warning(f"ML 결과 Hydration 실패, fallback to DB: {e}")

        return self._search_by_db(category=category, gender=gender, limit=limit)

    def _hydrate_from_db(
        self,
        product_scores: dict,
        source: str,
        category: Optional[str] = None,
        gender: Optional[str] = None,
        limit: int = 6,
    ) -> list:
        if not product_scores:
            return []

        product_ids = list(product_scores.keys())
        logger.info(
            "Hydration start: ml_scores=%d, gender=%s, category=%s, limit=%d",
            len(product_ids),
            gender,
            category,
            limit,
        )

        try:
            with get_pg_cursor() as cur:
                placeholders = ",".join(["%s"] * len(product_ids))
                conditions = [f"p.product_id::text IN ({placeholders})"]
                params: list = list(product_ids)

                if gender:
                    conditions.append("p.gender = %s")
                    params.append(gender.lower())
                if category:
                    category_values = self._category_filter_values(category)
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

            logger.info("Hydration db rows: %d", len(rows))

            sorted_rows = sorted(
                rows,
                key=lambda r: (
                    r["lowest_price"] if r["lowest_price"] is not None else float("inf"),
                    str(r["product_id"]),
                ),
            )

            products = []
            seen_product_ids = set()
            for row in sorted_rows:
                pid = str(row["product_id"])

                if pid not in product_scores:
                    continue

                if pid in seen_product_ids:
                    continue
                seen_product_ids.add(pid)

                products.append({
                    "product_id": pid,
                    "product_name": row["prod_name"],
                    "brand": row["brand_name"],
                    "price": row["lowest_price"],
                    "image_url": (f"/{row['img_hdfs_path']}" if row["img_hdfs_path"] and not row["img_hdfs_path"].startswith('/') else row["img_hdfs_path"]) or "https://placehold.co/300x300?text=No+Image",
                    "mall_name": row["mall_name"] or "공식몰",
                    "mall_url": row["mall_url"] or "#",
                    "similarity_score": round(product_scores.get(pid, 0.0), 4),
                    "search_source": source,
                })

            products.sort(
                key=lambda x: (x["similarity_score"] or 0.0, str(x["product_id"])),
                reverse=True,
            )
            logger.info("Hydration final products: %d", len(products))
            return products[:limit]

        except Exception as e:
            logger.error(f"DB Hydration 실패: {e}", exc_info=True)
            return []

    def _search_by_db(self, category: Optional[str], gender: Optional[str], limit: int) -> list:
        category_code = category

        try:
            with get_pg_cursor() as cur:
                conditions = []
                params: list = []

                if gender:
                    conditions.append("p.gender = %s")
                    params.append(gender.lower())
                if category_code:
                    category_values = self._category_filter_values(category_code)
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
                    "similarity_score": None,
                    "search_source": "db",
                }
                for row in rows
            ]

        except Exception as e:
            logger.error(f"DB fallback 검색 실패: {e}", exc_info=True)
            return []

search_service = SearchService()

