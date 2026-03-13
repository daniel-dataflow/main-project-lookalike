"""
상품 검색 관련 비즈니스 로직 처리 모듈.
- ML 엔진의 벡터 검색 방식과 DB 기반의 텍스트 검색(fallback) 전략 통합 관리.
- 호출부(Router)에서 검색 전략의 내부 로직을 알 필요가 없도록 추상화함.
"""
import logging
from typing import Optional
from ..database import get_pg_cursor

logger = logging.getLogger(__name__)


class SearchService:
    """
    유사 상품 검색 비즈니스 로직 처리 엔티티.
    성능 최적화 및 코드 응집도 증가를 위해 객체 상태로 관리함.
    """
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
        ml_product_scores: Optional[dict] = None,
        category: Optional[str] = None,
        gender: Optional[str] = None,
        limit: int = 6,
    ) -> list:
        """
        사용자 요청에 따른 상품 검색 통합 진입점.

        Args:
            ml_product_scores (Optional[dict]): ML 모델에서 도출된 {product_id: score} 맵 자료구조. AI 엔진의 결과를 우선적으로 참조하기 위함.
            category (Optional[str]): 대분류 필터 제한.
            gender (Optional[str]): 성별 필터 제한.
            limit (int, optional): 노출할 최종 상품 리스트 개수. UI 구성 상 기본 6개로 지정.

        Returns:
            list: 유사도가 높거나 무작위 추천된(fallback 시) 상품 정보 딕셔너리의 배열. (product_id, base_price, mall_url 등 포함)
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

