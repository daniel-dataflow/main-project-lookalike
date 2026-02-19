# 데이터베이스 스키마 정의서

## 📊 데이터베이스 개요

- **PostgreSQL (datadb)**: 상품, 가격, 검색 로그 등 핵심 데이터
- **MongoDB**: 크롤링 원본 데이터 (비정형)
- **Redis**: 세션, 캐시

---

## 🛍️ 상품 관련 테이블

### 1. products (상품 기본 정보)

| 컬럼명 | 타입 | 제약조건 | 설명 |
|--------|------|----------|------|
| product_id | BIGINT | PK, AUTO_INCREMENT | 상품 고유 ID |
| model_code | VARCHAR(50) | | 모델 코드 |
| prod_name | VARCHAR(50) | | 상품명 |
| base_price | INTEGER | | 기준 가격 (정가) |
| category_code | VARCHAR(50) | | 카테고리 코드 (상의, 하의, 아우터 등) |
| img_hdfs_path | VARCHAR(512) | | 이미지 HDFS 경로 |
| brand_name | VARCHAR(100) | | 브랜드명 |
| create_dt | TIMESTAMP | DEFAULT now() | 생성일시 |
| update_dt | TIMESTAMP | DEFAULT now() | 수정일시 |

**인덱스:**
- PRIMARY KEY: product_id

**외래키 참조:**
- naver_prices.product_id → products.product_id
- product_features.product_id → products.product_id

---

### 2. naver_prices (네이버 쇼핑 최저가 정보)

| 컬럼명 | 타입 | 제약조건 | 설명 |
|--------|------|----------|------|
| nprice_id | BIGINT | PK, AUTO_INCREMENT | 가격 정보 ID |
| product_id | BIGINT | FK | 상품 ID (products 참조) |
| rank | INTEGER | | 가격 순위 (1=최저가) |
| price | INTEGER | | 판매 가격 |
| mall_name | VARCHAR(100) | | 쇼핑몰명 |
| mall_url | VARCHAR(512) | | 쇼핑몰 URL |
| create_dt | TIMESTAMP | DEFAULT now() | 생성일시 |
| update_dt | TIMESTAMP | DEFAULT now() | 수정일시 |

**인덱스:**
- PRIMARY KEY: nprice_id
- FOREIGN KEY: product_id → products.product_id

---

### 3. product_features (상품 특징/설명)

| 컬럼명 | 타입 | 제약조건 | 설명 |
|--------|------|----------|------|
| feature_id | BIGINT | PK, AUTO_INCREMENT | 특징 ID |
| product_id | BIGINT | FK, UNIQUE | 상품 ID (products 참조) |
| detected_desc | TEXT | | 상품 설명 (텍스트 검색용) |
| create_dt | TIMESTAMP | DEFAULT now() | 생성일시 |
| update_dt | TIMESTAMP | DEFAULT now() | 수정일시 |

**인덱스:**
- PRIMARY KEY: feature_id
- UNIQUE: product_id
- FOREIGN KEY: product_id → products.product_id

---

## 🔍 검색 관련 테이블

### 4. search_logs (검색 로그)

| 컬럼명 | 타입 | 제약조건 | 설명 |
|--------|------|----------|------|
| log_id | BIGINT | PK, AUTO_INCREMENT | 검색 로그 ID |
| user_id | VARCHAR(50) | FK | 사용자 ID (users 참조) |
| input_img_path | VARCHAR(512) | | 업로드 이미지 경로 (원본, 현재 미사용) |
| thumbnail_path | VARCHAR(512) | | 썸네일 이미지 HDFS 경로 |
| input_text | TEXT | | 검색어 (텍스트 검색) |
| applied_category | VARCHAR(50) | | 적용된 카테고리 필터 |
| image_size | INTEGER | | 이미지 파일 크기 (bytes) |
| image_width | INTEGER | | 이미지 너비 (px) |
| image_height | INTEGER | | 이미지 높이 (px) |
| search_status | VARCHAR(20) | DEFAULT 'completed' | 검색 상태 (completed / failed) |
| result_count | INTEGER | DEFAULT 0 | 검색 결과 개수 |
| nprice_id | BIGINT | FK | 네이버 가격 ID (naver_prices 참조) |
| create_dt | TIMESTAMP | DEFAULT now() | 생성일시 |
| update_dt | TIMESTAMP | DEFAULT now() | 수정일시 |

**인덱스:**
- PRIMARY KEY: log_id
- INDEX: user_id
- INDEX: create_dt DESC
- INDEX: search_status
- INDEX: nprice_id
- FOREIGN KEY: user_id → users.user_id
- FOREIGN KEY: nprice_id → naver_prices.nprice_id

---

### 5. search_results (검색 결과 상세)

> **설계 방침 (비정규화):** 검색 당시 상품 정보를 직접 저장합니다.
> product_id FK 조인 방식 대신 스냅샷으로 보존하여, 추후 상품 정보가 변경되더라도 검색 이력이 오염되지 않습니다.
> ML/ES 검색 결과의 외부 소스 상품도 동일 구조로 저장 가능합니다.

| 컬럼명 | 타입 | 제약조건 | 설명 |
|--------|------|----------|------|
| result_id | BIGINT | PK, AUTO_INCREMENT | 결과 ID |
| log_id | BIGINT | FK | 검색 로그 ID (search_logs 참조) |
| product_name | VARCHAR(200) | | 상품명 (검색 당시 스냅샷) |
| brand | VARCHAR(100) | | 브랜드명 (검색 당시 스냅샷) |
| price | INTEGER | | 판매 가격 (검색 당시 스냅샷) |
| image_url | VARCHAR(512) | | 상품 이미지 URL |
| mall_name | VARCHAR(100) | | 쇼핑몰명 |
| mall_url | VARCHAR(500) | | 쇼핑몰 URL |
| rank | SMALLINT | | 검색 결과 순위 (1부터 시작) |
| create_dt | TIMESTAMP | DEFAULT now() | 생성일시 |

**인덱스:**
- PRIMARY KEY: result_id
- INDEX: log_id
- FOREIGN KEY: log_id → search_logs.log_id (ON DELETE CASCADE)

---

## 👤 사용자 관련 테이블

### 6. users (사용자 정보)

| 컬럼명 | 타입 | 제약조건 | 설명 |
|--------|------|----------|------|
| user_id | VARCHAR(50) | PK | 사용자 ID |
| email | VARCHAR(100) | UNIQUE | 이메일 |
| password_hash | VARCHAR(255) | | 비밀번호 해시 |
| username | VARCHAR(50) | | 사용자명 |
| **provider** | **VARCHAR(20)** | | **소셜 로그인 제공자 (2024-02-10 추가)** |
| **provider_id** | **VARCHAR(100)** | | **소셜 로그인 ID (2024-02-10 추가)** |
| **profile_image** | **VARCHAR(512)** | | **프로필 이미지 URL (2024-02-10 추가)** |
| create_dt | TIMESTAMP | DEFAULT now() | 생성일시 |
| update_dt | TIMESTAMP | DEFAULT now() | 수정일시 |

**인덱스:**
- PRIMARY KEY: user_id
- UNIQUE: email

---

## 💬 게시판 관련 테이블

### 7. inquiry_board (문의 게시판)

| 컬럼명 | 타입 | 제약조건 | 설명 |
|--------|------|----------|------|
| inquiry_board_id | BIGINT | PK, AUTO_INCREMENT | 게시글 ID |
| user_id | VARCHAR(50) | FK | 작성자 ID |
| title | VARCHAR(200) | NOT NULL | 제목 |
| content | TEXT | NOT NULL | 내용 |
| status | VARCHAR(20) | DEFAULT 'pending' | 상태 (pending/answered) |
| create_dt | TIMESTAMP | DEFAULT now() | 생성일시 |
| update_dt | TIMESTAMP | DEFAULT now() | 수정일시 |

**인덱스:**
- PRIMARY KEY: inquiry_board_id
- FOREIGN KEY: user_id → users.user_id

---

### 8. comments (댓글)

| 컬럼명 | 타입 | 제약조건 | 설명 |
|--------|------|----------|------|
| comment_id | BIGINT | PK, AUTO_INCREMENT | 댓글 ID |
| inquiry_board_id | BIGINT | FK | 게시글 ID |
| user_id | VARCHAR(50) | FK | 작성자 ID |
| content | TEXT | NOT NULL | 댓글 내용 |
| create_dt | TIMESTAMP | DEFAULT now() | 생성일시 |
| update_dt | TIMESTAMP | DEFAULT now() | 수정일시 |

**인덱스:**
- PRIMARY KEY: comment_id
- FOREIGN KEY: inquiry_board_id → inquiry_board.inquiry_board_id
- FOREIGN KEY: user_id → users.user_id

---

## 📝 주요 변경 이력

### 2026-02-19 (검색 서비스 리팩토링 - Phase 1)
1. **search_results 테이블 구조 변경**
   - `product_id` (FK) + `similarity_score` 방식 → **비정규화 스냅샷 방식**으로 전환
   - 추가: `product_name`, `brand`, `price`, `image_url`, `mall_name`, `mall_url`
   - 삭제: `product_id`, `similarity_score`
   - 이유: ML/ES 검색 결과(외부 소스)를 products 테이블 없이도 저장 가능, 이력 불변성 보장

2. **search_service.py 신규 생성** (코드 변경, DB 무관)
   - 전략 패턴: ES kNN → ES 텍스트 → DB fallback 자동 선택

### 2024-02-15
1. **products 테이블**
   - `brand_name` 컬럼 추가 (VARCHAR(100))
   - `origine_prod_id` 컬럼 삭제 (미사용)

2. **search_logs 테이블 확장**
   - `thumbnail_path`: 썸네일 이미지 HDFS 경로
   - `image_size`, `image_width`, `image_height`: 이미지 메타데이터
   - `search_status`: 검색 상태 (DEFAULT 'completed')
   - `result_count`: 검색 결과 개수

3. **search_results 테이블 신규 생성**
   - 검색 결과를 별도 테이블로 관리 (search_logs와 1:N)

4. **recent_views, likes 테이블 신규 생성**

### 2024-02-11
1. **inquiry_board 테이블**
   - `post_id` → `inquiry_board_id`로 컬럼명 변경

### 2024-02-10
1. **users 테이블**
   - 소셜 로그인 지원: `provider`, `provider_id`, `profile_image` 추가
   - `social_id` → `provider_id` 컬럼명 변경

---

## 🔗 테이블 관계도

```
users (1) ──< (N) search_logs (1) ──< (N) search_results
                      │
                      └──> (1) naver_prices

products (1) ──< (N) naver_prices
         (1) ──< (1) product_features

users (1) ──< (N) inquiry_board (1) ──< (N) comments
```

---

## 📊 테스트 데이터 현황

### insert_test_data.sh 실행 결과
- **products**: 10개 (의류 상품)
- **naver_prices**: 50개 (각 상품당 5개 쇼핑몰)
- **product_features**: 10개 (상품 설명)

### 테스트 데이터 예시
```sql
-- 상품 예시
product_id: 2
model_code: UT-2024-WH
brand_name: UNIQLO
prod_name: 프리미엄 코튼 반팔 티셔츠 - 화이트
base_price: 19900
category_code: 상의

-- 가격 예시 (product_id=2)
rank=1: 17900원 (네이버쇼핑)
rank=2: 18500원 (11번가)
rank=3: 19000원 (쿠팡)
```

---

## 🚀 마이그레이션 스크립트

### apply_db_changes.sh 적용 항목 (순서대로)

| 단계 | 내용 |
|------|------|
| 1 | Airflow DB (airflowdb) 분리 |
| 2 | users 소셜 로그인 컬럼 (provider, provider_id, profile_image) |
| 3 | inquiry_board 테이블 생성/마이그레이션 (posts → inquiry_board, post_id → inquiry_board_id) |
| 3 | comments 테이블 생성 |
| 4 | search_logs 확장 (thumbnail_path, image_size/width/height, search_status, result_count) |
| 4 | search_results 테이블 생성 (비정규화 구조) |
| 5 | products.brand_name 컬럼 추가 |
| 6 | products.origine_prod_id 컬럼 삭제 |
| 7 | users.social_id → provider_id 컬럼명 변경 |
| 8 | recent_views, likes 테이블 생성 |

### insert_test_data.sh
- 의류 상품 10개 이상 삽입
- 각 상품당 최저가 정보 (naver_prices)
- 상품 설명/특징 데이터 (product_features)

---

## 📌 참고사항

1. **이미지 저장 정책**
   - 원본 이미지: 저장하지 않음 (메모리 절약)
   - 썸네일만 HDFS에 저장 (`/images/thumb/`)

2. **search_results 비정규화 설계**
   - `product_id` FK 방식을 사용하지 않고 검색 당시 상품 정보를 직접 저장
   - ML/ES 외부 검색 결과도 동일 구조로 저장 가능
   - 상품 정보 변경과 무관하게 검색 이력 불변성 보장

3. **검색 결과 저장 구조**
   - `search_logs`: 검색 메타데이터 (검색어, 이미지 정보, 검색 상태)
   - `search_results`: 실제 반환된 상품 목록 (log_id와 1:N)
