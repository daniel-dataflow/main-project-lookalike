"""
실제 DB 기반 상품 검색 서비스
- Mock ML 대신 products 테이블에서 실제 데이터 조회
- 카테고리 필터링 지원
- 최저가 정보 포함
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def search_similar_products(category: Optional[str] = None, limit: int = 4) -> list:
    """
    실제 DB에서 의류 상품 조회 (카테고리 필터링 지원)
    
    Args:
        category: 카테고리 필터 (None or '전체'면 전체 조회)
        limit: 반환할 상품 개수
    
    Returns:
        list: 상품 정보 리스트 (product_id, product_name, brand, price, image_url 등)
    """
    from ..database import get_pg_cursor
    
    try:
        with get_pg_cursor() as cur:
            # 카테고리 필터링
            if category and category != '전체':
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
                        np.mall_url
                    FROM products p
                    LEFT JOIN naver_prices np ON p.product_id = np.product_id AND np.rank = 1
                    WHERE p.category_code = %s
                    ORDER BY RANDOM()
                    LIMIT %s
                    """,
                    (category, limit),
                )
            else:
                # 전체 조회
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
                        np.mall_url
                    FROM products p
                    LEFT JOIN naver_prices np ON p.product_id = np.product_id AND np.rank = 1
                    ORDER BY RANDOM()
                    LIMIT %s
                    """,
                    (limit,),
                )
            
            rows = cur.fetchall()
        
        # 결과 포맷팅
        results = []
        for i, row in enumerate(rows):
            
            results.append({
                "product_id": row["product_id"],
                "product_name": row["prod_name"],
                "brand": row["brand_name"],
                "price": row["lowest_price"],
                "image_url": row["img_hdfs_path"] or "https://placehold.co/300x300?text=No+Image",
                "mall_name": row["mall_name"] or "공식몰",
                "mall_url": row["mall_url"] or "#",
            })
        
        logger.info(f"DB 검색 완료: {len(results)}개 상품 (카테고리: {category or '전체'})")
        return results
    
    except Exception as e:
        logger.error(f"DB 검색 실패: {e}", exc_info=True)
        # 실패 시 빈 리스트 반환
        return []
