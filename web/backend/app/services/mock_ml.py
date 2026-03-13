"""
개발 및 테스트 환경에서 활용하기 위한 가짜(Mock) ML 기반 상품 검색 서비스.
- ML 엔진 API나 Elasticsearch 서버 연동 구성이 완료되지 않은 환경에서도 개발 파이프라인(Router 등)의 정상 작동을 돕기 위해 구성함.
"""
import logging
from typing import Optional
from ..database import get_pg_cursor

logger = logging.getLogger(__name__)


class MockMLService:
    """
    ML 검색 결과를 흉내 내어 RDBMS(PostgreSQL)의 데이터로 임의 반환을 수행하는 모의 비즈니스 클래스.
    나머지 서비스 클래스들과의 형태적 일관성(추상화/의존성)을 갖추기 위해 객체로 관리됨.
    """
    def search_similar_products(self, category: Optional[str] = None, limit: int = 4) -> list:
        """
        랜덤한 기준(PostgreSQL의 RANDOM())으로 상품을 무작위 추출하여 AI 엔진이 추론한 듯한 결과 배열을 모방함.
        
        Args:
            category (Optional[str]): 카테고리 필터 문자열. 미입력 혹은 '전체'일 경우 스킵됨.
            limit (int, optional): 반환할 최대 상품 개수 한도.

        Returns:
            list: 클라이언트에게 반환될 형식(product_id, price_url 등)과 동일한 포맷의 딕셔너리 리스트. 에러 혹은 타임아웃 발생 시 빈 리스트 반환.
        """
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
mock_ml_service = MockMLService()
