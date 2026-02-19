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
| origine_prod_id | VARCHAR(50) | | 원본 상품 ID (크롤링 소스) |
| model_code | VARCHAR(50) | | 모델 코드 |
| prod_name | VARCHAR(50) | | 상품명 |
| base_price | INTEGER | | 기준 가격 (정가) |
| category_code | VARCHAR(50) | | 카테고리 코드 (상의, 하의, 아우터 등) |
| img_hdfs_path | VARCHAR(512) | | 이미지 HDFS 경로 |
| **brand_name** | **VARCHAR(100)** | | **브랜드명 (2024-02-15 추가)** |
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
| **thumbnail_path** | **VARCHAR(512)** | | **썸네일 이미지 경로 (2024-02-15 추가)** |
| input_text | TEXT | | 검색어 (텍스트 검색) |
| applied_category | VARCHAR(50) | | 적용된 카테고리 필터 |
| **image_size** | **INTEGER** | | **이미지 파일 크기 (bytes) (2024-02-15 추가)** |
| **image_width** | **INTEGER** | | **이미지 너비 (px) (2024-02-15 추가)** |
| **image_height** | **INTEGER** | | **이미지 높이 (px) (2024-02-15 추가)** |
| **search_status** | **VARCHAR(20)** | **DEFAULT 'pending'** | **검색 상태 (2024-02-15 추가)** |
| **search_result** | **JSON** | | **검색 결과 (JSON) (2024-02-15 추가)** |
| **result_count** | **INTEGER** | **DEFAULT 0** | **검색 결과 개수 (2024-02-15 추가)** |
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

**⚠️ 2024-02-15 신규 생성**

| 컬럼명 | 타입 | 제약조건 | 설명 |
|--------|------|----------|------|
| result_id | INTEGER | PK, AUTO_INCREMENT | 결과 ID |
| log_id | INTEGER | FK | 검색 로그 ID (search_logs 참조) |
| product_id | VARCHAR(50) | | 상품 ID |
| similarity_score | DOUBLE PRECISION | | 유사도 점수 (0.0 ~ 1.0) |
| rank | INTEGER | | 검색 결과 순위 |
| create_dt | TIMESTAMP | DEFAULT now() | 생성일시 |

**인덱스:**
- PRIMARY KEY: result_id
- FOREIGN KEY: log_id → search_logs.log_id (ON DELETE CASCADE)

**참고:**
- 검색 결과의 상세 정보(상품명, 가격 등)는 product_id로 products 테이블 조인하여 조회

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

### 2024-02-15
1. **products 테이블**
   - `brand_name` 컬럼 추가 (VARCHAR(100))
   - 브랜드 정보를 별도 관리하여 검색 및 필터링 개선

2. **search_logs 테이블 확장**
   - `thumbnail_path`: 썸네일 이미지 경로 (원본 대신 썸네일만 저장)
   - `image_size`, `image_width`, `image_height`: 이미지 메타데이터
   - `search_status`: 검색 상태 추적
   - `search_result`: 검색 결과 JSON 저장
   - `result_count`: 검색 결과 개수

3. **search_results 테이블 신규 생성**
   - 검색 결과를 별도 테이블로 관리
   - log_id와 1:N 관계
   - 검색 이력 분석 및 추천 시스템 기반 데이터

### 2024-02-10
1. **users 테이블**
   - 소셜 로그인 지원: `provider`, `provider_id`, `profile_image` 추가

### 2024-02-11
1. **inquiry_board 테이블**
   - `post_id` → `inquiry_board_id`로 컬럼명 변경

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

### insert_test_data.sql 실행 결과
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

### apply_db_changes.sh
- Airflow DB 분리
- users 소셜 로그인 컬럼 추가
- inquiry_board 테이블 생성 및 마이그레이션
- comments 테이블 생성
- search_logs 확장 (썸네일, 메타데이터)
- search_results 테이블 생성
- **products.brand_name 컬럼 추가**

### insert_test_data.sql
- 의류 상품 10개 삽입
- 각 상품당 5개 쇼핑몰 최저가 정보
- 상품 설명/특징 데이터

---

## 📌 참고사항

1. **이미지 저장 정책**
   - 원본 이미지: 저장하지 않음 (메모리 절약)
   - 썸네일만 HDFS에 저장 (`/images/thumb/`)

2. **product_id 타입**
   - products 테이블: BIGINT (AUTO_INCREMENT)
   - search_results 테이블: VARCHAR(50) (호환성 유지)

3. **검색 결과 저장**
   - search_logs: 검색 메타데이터
   - search_results: 개별 검색 결과 (product_id, similarity_score, rank)
