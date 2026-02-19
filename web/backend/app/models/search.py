"""
검색 관련 Pydantic 모델
"""
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


# ──────────────────────────────────────
# Request 모델
# ──────────────────────────────────────
class SearchByImageRequest(BaseModel):
    """이미지 기반 유사 상품 검색"""
    category: Optional[str] = Field(None, description="카테고리 필터")
    top_k: int = Field(10, ge=1, le=50, description="반환할 결과 수")


class SearchByTextRequest(BaseModel):
    """텍스트 기반 유사 상품 검색"""
    query: str = Field(..., min_length=1, description="검색 텍스트")
    category: Optional[str] = Field(None, description="카테고리 필터")
    top_k: int = Field(10, ge=1, le=50, description="반환할 결과 수")


# ──────────────────────────────────────
# Response 모델
# ──────────────────────────────────────
class SimilarProductResponse(BaseModel):
    """유사 상품 결과"""
    product_id: Optional[int] = None
    prod_name: Optional[str] = None
    base_price: Optional[int] = None
    img_hdfs_path: Optional[str] = None


class SearchResultResponse(BaseModel):
    """검색 결과 응답"""
    results: List[SimilarProductResponse]
    total: int
    query_type: str  # "image" or "text"


class SearchLogResponse(BaseModel):
    """검색 로그"""
    log_id: int
    user_id: Optional[str] = None
    input_img_path: Optional[str] = None
    input_text: Optional[str] = None
    applied_category: Optional[str] = None
    create_dt: Optional[datetime] = None


# ──────────────────────────────────────
# 이미지 검색 관련 모델
# ──────────────────────────────────────
class ProductResult(BaseModel):
    """상품 검색 결과
    - similarity_score: ES 검색 시 유사도 점수 (0.0~1.0), DB fallback 시 None
    - search_source: 검색 전략 ("elasticsearch_knn" | "elasticsearch_text" | "db")
    """
    product_id: int
    product_name: str
    brand: str
    price: int
    image_url: str
    mall_name: str
    mall_url: str
    similarity_score: Optional[float] = None   # ML 유사도 점수
    search_source: str = "db"                  # 검색 전략 식별자


# 하위 호환성 (import하는 코드가 있다면 수정 전에 동작)
MockProductResult = ProductResult


class ImageSearchResponse(BaseModel):
    """이미지 검색 응답"""
    success: bool = True
    log_id: int
    thumbnail_url: str
    results: List[ProductResult]
    result_count: int
    search_source: str = "db"  # 전체 검색에 적용된 전략


class SearchHistoryItem(BaseModel):
    """검색 히스토리 아이템"""
    log_id: int
    thumbnail_url: Optional[str] = None
    search_text: Optional[str] = None
    category: Optional[str] = None
    create_dt: Optional[datetime] = None
    result_count: int = 0


class SearchHistoryListResponse(BaseModel):
    """검색 히스토리 목록 응답"""
    success: bool = True
    total: int
    page: int
    limit: int
    history: List[SearchHistoryItem]
