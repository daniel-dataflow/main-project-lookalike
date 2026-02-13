# database/resetup_db.py

import psycopg2
from pymongo import MongoClient
from elasticsearch import Elasticsearch
from datetime import datetime

# 설정 정보
DB_USER = "datauser"
DB_PASS = "DataPass2024!"
DB_NAME = "datadb"
ES_URL = "http://localhost:8903"

# 1. PostgreSQL 초기화
def init_postgresql():
    try:
        conn = psycopg2.connect(host="localhost", database=DB_NAME, user=DB_USER, password=DB_PASS, port=5432)
        cur = conn.cursor()

        # 삭제 (의존성 역순)
        drop_query = "DROP TABLE IF EXISTS vector_search_idx, search_logs, product_features, naver_prices, comments, posts, products, brand_sequences, users CASCADE;"
        cur.execute(drop_query)

        create_queries = [
            # 1. Brand_sequences (TOPTEN 추가를 위해 다시 확인)
            "CREATE TABLE brand_sequences (brand_name VARCHAR(50) PRIMARY KEY, last_seq INTEGER DEFAULT 0);",
            
            # 2. Users / Posts / Comments (정의서 일치)
            "CREATE TABLE users (user_id VARCHAR(50) PRIMARY KEY, password VARCHAR(255) NOT NULL, name VARCHAR(50), email VARCHAR(100) UNIQUE, role VARCHAR(20), last_login TIMESTAMP, create_dt TIMESTAMP DEFAULT NOW(), update_dt TIMESTAMP DEFAULT NOW());",
            "CREATE TABLE posts (post_id BIGSERIAL PRIMARY KEY, title VARCHAR(200), content TEXT, author_id VARCHAR(50) REFERENCES users(user_id), view_count INTEGER DEFAULT 0, is_notice BOOLEAN DEFAULT FALSE, create_dt TIMESTAMP DEFAULT NOW(), update_dt TIMESTAMP DEFAULT NOW());",
            "CREATE TABLE comments (comment_id BIGSERIAL PRIMARY KEY, post_id BIGINT REFERENCES posts(post_id), author_id VARCHAR(50) REFERENCES users(user_id), comment_text TEXT, create_dt TIMESTAMP DEFAULT NOW());",
            
            # 3. Products
            "CREATE TABLE products (product_id VARCHAR(20) PRIMARY KEY, model_code VARCHAR(50), brand_name VARCHAR(50), prod_name VARCHAR(200), base_price INTEGER, category_code VARCHAR(50), img_hdfs_path VARCHAR(512), create_dt TIMESTAMP DEFAULT NOW(), update_dt TIMESTAMP DEFAULT NOW());",
            
            # 4. Naver_prices / Product_features
            "CREATE TABLE naver_prices (nprice_id BIGSERIAL PRIMARY KEY, product_id VARCHAR(20) REFERENCES products(product_id), rank SMALLINT, price INTEGER, mall_name VARCHAR(100), mall_url VARCHAR(500), create_dt TIMESTAMP DEFAULT NOW());",
            "CREATE TABLE product_features (product_id VARCHAR(20) PRIMARY KEY REFERENCES products(product_id), detected_desc VARCHAR(1000), create_dt TIMESTAMP DEFAULT NOW());",
            
            # 5. Search_logs (정의서와 다른 부분 교정)
            """
            CREATE TABLE search_logs (
                log_id BIGSERIAL PRIMARY KEY,
                user_id VARCHAR(50) REFERENCES users(user_id),
                input_img_path VARCHAR(512),
                input_text TEXT,
                applied_category VARCHAR(50),
                nprice_id BIGINT REFERENCES naver_prices(nprice_id),
                create_dt TIMESTAMP DEFAULT NOW(),
                update_dt TIMESTAMP DEFAULT NOW()
            );
            """
        ]

        for q in create_queries:
            cur.execute(q)
        
        # [수정] 브랜드 리스트에 TOPTEN(TT) 추가
        brands = ['UNIQLO', 'ZARA', 'EIGHTSECONDS', 'MUSINSA', 'TOPTEN']
        for brand in brands:
            cur.execute("INSERT INTO brand_sequences VALUES (%s, 0)", (brand,))

        conn.commit()
        print("✅ PostgreSQL: TOPTEN 포함 모든 테이블 초기화 완료")
    except Exception as e:
        print(f"❌ PostgreSQL 에러: {e}")
    finally:
        if conn: conn.close()

# 2. MongoDB 초기화 (상세 스키마 가이드 적용)
def init_mongodb():
    try:
        client = MongoClient("mongodb://datauser:DataPass2024!@localhost:27017/admin")
        db = client[DB_NAME]
        
        if "product_details" in db.list_collection_names():
            db.product_details.drop()
        
        # 컬렉션 생성
        db.create_collection("product_details")
        
        # [핵심] 정의서에 명시된 필드들에 대한 인덱스 및 관리
        db.product_details.create_index("product_id", unique=True) # PK
        db.product_details.create_index("model_code")             # 검색용
        db.product_details.create_index("brand_name")             # 필터용
        
        # 엔지니어의 팁: 아래와 같은 구조로 적재될 것임을 명시 (실제 코드에 영향X, 가이드용)
        # {
        #   "product_id": "UQ0001",
        #   "model_code": "461234",
        #   "brand_name": "UNIQLO",
        #   "img_hdfs_path": ["/path1.jpg", "/path2.jpg"],  <-- Array
        #   "detail_desc": "텍스트 내용...",
        #   "create_dt": ISODate(...)
        # }
        
        print("✅ MongoDB: product_details 정의서 규격(Array/Detail) 준비 완료")
        client.close()
    except Exception as e:
        print(f"❌ MongoDB 에러: {e}")

# 3. Elasticsearch 초기화
def init_elasticsearch():
    try:
        es = Elasticsearch(ES_URL)
        mapping = {
            "properties": {
                "product_id": {"type": "keyword"},
                "image_vector": {"type": "dense_vector", "dims": 512, "index": True, "similarity": "cosine"},
                "text_vector": {"type": "dense_vector", "dims": 512, "index": True, "similarity": "cosine"},
                "price": {"type": "integer"},
                "create_dt": {"type": "date"}
            }
        }
        
        for idx in ["vector_idx", "vector_search_idx"]:
            if es.indices.exists(index=idx):
                es.indices.delete(index=idx)
            es.indices.create(index=idx, mappings=mapping)
            
        print("✅ Elasticsearch: 벡터 인덱스 2종 생성 완료")
    except Exception as e:
        print(f"❌ Elasticsearch 에러: {e}")

if __name__ == "__main__":
    init_postgresql()
    init_mongodb()
    init_elasticsearch()
    print("\n🚀 [Success] TOPTEN 추가 및 MongoDB 정의서 규격 반영 완료!")