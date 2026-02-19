# ML/Elasticsearch 검색 통합 계획서

> **목적**: 홈페이지 검색 시스템을 ML 임베딩 + VLM 기반 유사도 검색으로 전환하기 위한 아키텍처 준비  
> **최초 작성**: 2026-02-19  
> **담당**: 백엔드팀

---

## 배경 및 목표

### 현재 상태
- 홈페이지 검색(`/api/search/by-image`)은 `mock_ml.py`에서 **DB `ORDER BY RANDOM()`** 로 결과를 반환 (임시 구현)
- Elasticsearch는 현재 **로그/메트릭 모니터링 전용** (`container-logs`, `container-metrics` 인덱스만 존재)
- `search.py` L101에 `# TODO: 실제 ML 검색 로직 구현` 주석이 이미 존재

### 최종 목표
ML 파이프라인이 완성되면 아래 데이터가 Elasticsearch에 저장됨:
- **임베딩 벡터**: 이미지/텍스트 기반 ML 모델이 추출한 dense vector (512차원 예정)
- **VLM 설명**: 옷의 색상, 소재, 스타일 등을 VLM이 자동 추출한 텍스트 (`product_features.detected_desc`)

사용자가 이미지 또는 검색어를 입력하면 → Elasticsearch kNN 벡터 유사도 검색으로 유사 상품 반환

### 핵심 원칙
> **프론트엔드 API 계약 변경 없음**  
> 백엔드 내부 로직만 교체 가능한 구조(전략 패턴)로 개편하며, 프론트엔드는 수정하지 않음

---

## 현재 구조의 문제점

| 항목 | 현재 상태 | 문제 |
|------|-----------|------|
| `mock_ml.py` | `ORDER BY RANDOM()` DB 쿼리 | ML 교체 시 파일 전체 재작성 필요 |
| `search.py` L101 | `TODO` 주석만 존재 | ES 연동 진입점 없음 |
| ES 인덱스 | `container-logs`, `container-metrics`만 존재 | 상품용 인덱스(`products`) 없음 |
| `MockProductResult` 모델 | `similarity_score` 필드 없음 | ML 결과 유사도 점수 표시 불가 |

---

## 아키텍처 설계

```
사용자 요청 (이미지 or 텍스트)
        ↓
/api/search/by-image  (search.py - 변경 없음)
        ↓
search_service.search_products()  ← NEW (전략 패턴)
        ├── ES 가용 + embedding 있음  →  ES kNN 벡터 검색       (ML 연동 후)
        ├── ES 가용 + text만 있음     →  ES multi_match 검색    (텍스트 검색)
        └── ES 없음 / 연결 실패       →  DB RANDOM() fallback   (현재 동작 유지)
```

---

## 구현 계획

### Phase 1 - 구조 준비 (현재 단계) `[x]` ✅ 완료 (2026-02-19)

#### 1-1. Elasticsearch `products` 인덱스 생성
**파일**: `web/backend/app/core/elasticsearch_setup.py`  
`init_product_index()` 함수 추가:

```python
def init_product_index():
    """상품 검색용 ES 인덱스 (ML 임베딩 + VLM 설명 저장)"""
    index_body = {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "refresh_interval": "30s"
        },
        "mappings": {
            "properties": {
                "product_id":    {"type": "long"},
                "prod_name":     {"type": "text", "analyzer": "standard"},
                "brand_name":    {"type": "keyword"},
                "category":      {"type": "keyword"},
                "detected_desc": {"type": "text", "analyzer": "standard"},  # VLM 설명
                "embedding":     {                                            # ML 임베딩
                    "type": "dense_vector",
                    "dims": 512,
                    "index": True,
                    "similarity": "cosine"
                },
                "base_price":   {"type": "integer"},
                "lowest_price": {"type": "integer"},
                "mall_name":    {"type": "keyword"},
                "mall_url":     {"type": "keyword"},
                "image_url":    {"type": "keyword"},
                "indexed_at":   {"type": "date"}
            }
        }
    }
```

> `embedding` 필드는 ML 팀이 실제 값을 넣기 전까지 비워둠. 인덱스 구조만 미리 생성.

#### 1-2. 검색 서비스 추상화 레이어 신규 생성
**파일**: `web/backend/app/services/search_service.py` [NEW]  

`mock_ml.py`를 대체하는 전략 패턴 기반 서비스. 호출부(`search.py`)는 `search_service.py`만 바라봄:

```python
async def search_products(
    query_text: Optional[str],
    query_embedding: Optional[list],  # ML 서버로부터 전달받을 벡터
    category: Optional[str],
    limit: int = 4,
) -> list:
    """
    ES 가용 시 ES 검색, 불가 시 DB fallback
    각 결과에 similarity_score 포함 (DB fallback은 None)
    """
```

#### 1-3. 응답 모델 업데이트
**파일**: `web/backend/app/models/search.py`  

`MockProductResult` → `ProductResult`로 리네이밍 + 필드 추가:

```python
class ProductResult(BaseModel):
    product_id: int
    product_name: str
    brand: str
    price: int
    image_url: str
    mall_name: str
    mall_url: str
    similarity_score: Optional[float] = None  # ML 유사도 점수 (0.0~1.0)
    search_source: str = "db"                 # "elasticsearch" | "db"
```

#### 1-4. 라우터 연결 (`search.py` L101 TODO 해소)
```python
# Before
from ..services.mock_ml import search_similar_products
ml_results = search_similar_products(category)

# After
from ..services.search_service import search_products
ml_results = await search_products(
    query_text=search_text,
    query_embedding=None,   # 향후 ML 서버가 넘겨줄 벡터
    category=category,
    limit=4,
)
```

#### 1-5. 앱 시작 시 인덱스 자동 생성
**파일**: `web/backend/app/main.py`  
`startup` 이벤트에 `init_product_index()` 추가

---

### Phase 2 - ML 파이프라인 연동 (ML팀 작업 완료 후) `[ ]`

- ML 서버(FastAPI 등)에서 쿼리 이미지/텍스트의 임베딩 벡터 추출 → `search_service.py`에 전달
- `search_service.py`의 ES kNN 경로 활성화
- 상품 등록/수정 시 ES에 임베딩 upsert하는 Airflow DAG 연동
- `search_results` 테이블에 `similarity_score` 컬럼 추가 (히스토리 상세 조회용)

---

### Phase 3 - 고도화 `[ ]`

- 이미지 + 텍스트 복합 검색 (멀티모달 가중 결합)
- 카테고리별 임베딩 모델 분리 적용 가능성 검토
- 검색 결과 피드백 루프(클릭, 구매) → 랭킹 재학습

---

## 진행 상황 로그

### 2026-02-19 - 계획 수립 ✅
- 현재 코드 구조 분석 완료
- 아키텍처 설계 완료 (전략 패턴 + ES 인덱스 설계)

### 2026-02-19 - Phase 1 구현 완료 ✅

**구현 내용:**
- `elasticsearch_setup.py` - `init_product_index()` 추가 (512차원 dense_vector + VLM detected_desc 필드)
- `services/search_service.py` 신규 생성 - ES kNN → ES text → DB fallback 전략 패턴
- `models/search.py` - `MockProductResult` → `ProductResult` 리네이밍, `similarity_score` / `search_source` 필드 추가
- `routers/search.py` - `mock_ml` → `search_service` 교체, `TODO` 해소
- `main.py` - startup 이벤트에 `init_product_index()` 추가

**검증 결과:**
```json
{
  "success": true,
  "log_id": 4,
  "results": [{"search_source": "db", "similarity_score": null, ...}],
  "search_source": "db"
}
```
- ES `products` 인덱스 정상 생성 확인 (`ℹ️ already exists` 로그)
- API 응답에 `search_source: "db"`, `similarity_score: null` 포함 (DB fallback 정상 동작)
- 기존 프론트엔드 동작 변경 없음 (하위 호환성 유지)

**다음 단계 (Phase 2 진행 조건):**
- ML 팀이 이미지/텍스트 임베딩 벡터 추출 완성 시
- `search_service.py`의 `query_embedding` 인자에 벡터 전달 → 자동으로 ES kNN 검색 전환
- 상품 등록 시 ES indexing Airflow DAG 연동 필요

---

## 관련 파일 목록

| 파일 | 역할 | Phase |
|------|------|-------|
| `web/backend/app/core/elasticsearch_setup.py` | ES 인덱스 초기화 | Phase 1 |
| `web/backend/app/services/search_service.py` | 검색 전략 추상화 (NEW) | Phase 1 |
| `web/backend/app/services/mock_ml.py` | DB fallback 함수로 리팩터링 | Phase 1 |
| `web/backend/app/models/search.py` | 응답 모델 업데이트 | Phase 1 |
| `web/backend/app/routers/search.py` | 라우터 연결 교체 | Phase 1 |
| `web/backend/app/main.py` | startup 이벤트에 인덱스 생성 추가 | Phase 1 |

---

## 참고

- [DB 스키마 정의서](./DB_SCHEMA.md) - `products`, `product_features`, `search_logs` 테이블 구조
- Elasticsearch 현재 인덱스: `container-logs` (로그), `container-metrics` (메트릭)
- 상품용 신규 인덱스명: `products`
