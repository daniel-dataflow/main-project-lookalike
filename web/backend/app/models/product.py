"""
상품 관련 Pydantic 모델
"""
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List
from datetime import datetime


# ──────────────────────────────────────
# Request 모델
# ──────────────────────────────────────
class ProductCreateRequest(BaseModel):
    """상품 등록"""
    model_config = ConfigDict(protected_namespaces=())

    origine_prod_id: Optional[str] = Field(None, max_length=50)
    model_code: Optional[str] = Field(None, max_length=50)
    prod_name: Optional[str] = Field(None, max_length=50)
    base_price: Optional[int] = None
    category_code: Optional[str] = Field(None, max_length=50)
    img_hdfs_path: Optional[str] = Field(None, max_length=512)


# ──────────────────────────────────────
# Response 모델
# ──────────────────────────────────────
class ProductResponse(BaseModel):
    """상품 정보 응답"""
    model_config = ConfigDict(protected_namespaces=())

    product_id: int
    origine_prod_id: Optional[str] = None
    model_code: Optional[str] = None
    prod_name: Optional[str] = None
    base_price: Optional[int] = None
    category_code: Optional[str] = None
    img_hdfs_path: Optional[str] = None
    create_dt: Optional[datetime] = None
    update_dt: Optional[datetime] = None


class NaverPriceResponse(BaseModel):
    """네이버 가격 정보 응답"""
    nprice_id: int
    product_id: int
    rank: Optional[int] = None
    price: Optional[int] = None
    mall_name: Optional[str] = None
    mall_url: Optional[str] = None
    create_dt: Optional[datetime] = None


class ProductDetailResponse(BaseModel):
    """상품 상세 응답 (PostgreSQL + MongoDB 조합)"""
    product: ProductResponse
    detail_desc: Optional[str] = None
    detected_desc: Optional[str] = None
    naver_prices: List[NaverPriceResponse] = []


class ProductListResponse(BaseModel):
    """상품 목록 응답 (페이징)"""
    items: List[ProductResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
