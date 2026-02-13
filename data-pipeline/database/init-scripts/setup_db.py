# database/setup_db.py

import psycopg2
from pymongo import MongoClient
from elasticsearch import Elasticsearch
from datetime import datetime


# 설정 정보
DB_USER = "datauser"
DB_PASS = "DataPass2024!"
DB_NAME = "datadb"
ES_URL = "http://localhost:8903"

def init_postgresql():
    try:
        conn = psycopg2.connect(host="localhost", database=DB_NAME, user=DB_USER, password=DB_PASS, port=5432)
        cur = conn.cursor()
        
        queries = [
            # 1. Users (PK는 자동 인덱스 생성됨)
            "CREATE TABLE IF NOT EXISTS users (user_id VARCHAR(50) PRIMARY KEY, password VARCHAR(255) NOT NULL, name VARCHAR(50), email VARCHAR(100) UNIQUE, role VARCHAR(20) DEFAULT 'USER', last_login TIMESTAMP DEFAULT NOW(), create_dt TIMESTAMP DEFAULT NOW(), update_dt TIMESTAMP DEFAULT NOW());",
            
            # 2. Posts
            "CREATE TABLE IF NOT EXISTS posts (post_id BIGSERIAL PRIMARY KEY, title VARCHAR(200) NOT NULL, content TEXT, author_id VARCHAR(50) REFERENCES users(user_id), view_count INTEGER DEFAULT 0, is_notice BOOLEAN DEFAULT FALSE, create_dt TIMESTAMP DEFAULT NOW(), update_dt TIMESTAMP DEFAULT NOW());",
            
            # 3. Comments + INDEX (게시글별 댓글 조회 성능 강화)
            "CREATE TABLE IF NOT EXISTS comments (comment_id BIGSERIAL PRIMARY KEY, post_id BIGINT REFERENCES posts(post_id), author_id VARCHAR(50) REFERENCES users(user_id), comment_text TEXT, create_dt TIMESTAMP DEFAULT NOW());",
            "CREATE INDEX IF NOT EXISTS idx_comments_post_id ON comments(post_id);", # 인덱스 추가!
            
            # 4. Products
            "CREATE TABLE IF NOT EXISTS products (product_id BIGSERIAL PRIMARY KEY, origine_prod_id VARCHAR(50), brand_name VARCHAR(50), prod_name VARCHAR(50), base_price INTEGER, category_code VARCHAR(50), img_hdfs_path VARCHAR(512), create_dt TIMESTAMP DEFAULT NOW(), update_dt TIMESTAMP DEFAULT NOW());",
            
            # 5. Naver_prices + INDEX (상품별 최저가 조회 성능 강화)
            "CREATE TABLE IF NOT EXISTS naver_prices (nprice_id BIGSERIAL PRIMARY KEY, product_id BIGINT REFERENCES products(product_id), rank SMALLINT, price INTEGER, mall_name VARCHAR(100), mall_url VARCHAR(500), create_dt TIMESTAMP DEFAULT NOW());",
            "CREATE INDEX IF NOT EXISTS idx_naver_prices_product_id ON naver_prices(product_id);", # 인덱스 추가!
            
            # 6. Product_features
            "CREATE TABLE IF NOT EXISTS product_features (product_id BIGINT PRIMARY KEY REFERENCES products(product_id), detected_desc VARCHAR(1000), create_dt TIMESTAMP DEFAULT NOW());",
            
            # 7. Search_logs + INDEX (회원별 검색 로그/최저가 연동 조회 성능 강화)
            "CREATE TABLE IF NOT EXISTS search_logs (log_id BIGSERIAL PRIMARY KEY, user_id VARCHAR(50) REFERENCES users(user_id), input_img_path VARCHAR(512), input_text TEXT, applied_category VARCHAR(50), nprice_id BIGINT REFERENCES naver_prices(nprice_id), create_dt TIMESTAMP DEFAULT NOW(), update_dt TIMESTAMP DEFAULT NOW());",
            "CREATE INDEX IF NOT EXISTS idx_search_logs_user_id ON search_logs(user_id);",   # 인덱스 추가!
            "CREATE INDEX IF NOT EXISTS idx_search_logs_nprice_id ON search_logs(nprice_id);" # 인덱스 추가!
        ]
        
        for q in queries:
            cur.execute(q)
        
        conn.commit()
        cur.close()
        conn.close()
        print("✅ PostgreSQL Tables & All Indices Created!")
    except Exception as e:
        print(f"❌ PostgreSQL 에러: {e}")


def init_mongodb():
    try:
        client = MongoClient(
            host="localhost",
            port=27017,
            username="datauser",      # 실제 계정 맞게
            password="DataPass2024!", # 실제 계정 맞게
            authSource="admin"
        )

        db = client[DB_NAME]

        # 컬렉션 이름: product_details
        if "product_details" not in db.list_collection_names():
            db.product_details.insert_one({
                "product_id": -1,
                "name": "INIT_DUMMY",
                "brand": "SYSTEM",
                "created_at": datetime.utcnow(),
                "is_dummy": True
            })
            print("✅ MongoDB collection created: product_details")
        else:
            print("ℹ️ MongoDB collection already exists: product_details")

        client.close()

    except Exception as e:
        print(f"❌ MongoDB 에러: {e}")


# Elasticsearch 인덱스 생성 (이미 인덱스 자체가 검색용 구조입니다)

def init_elasticsearch():
    es = Elasticsearch("http://localhost:8903")

    mappings = {
        "properties": {
            "product_id": {"type": "long"},
            "log_id": {"type": "long"},
            "image_vector": {
                "type": "dense_vector",
                "dims": 512,
                "index": True,
                "similarity": "cosine"
            },
            "text_vector": {
                "type": "dense_vector",
                "dims": 512,
                "index": True,
                "similarity": "cosine"
            },
            "price": {"type": "integer"},
            "create_dt": {"type": "date"}
        }
    }

    for index_name in ["vector_idx", "vector_search_idx"]:
        if not es.indices.exists(index=index_name):
            es.indices.create(
                index=index_name,
                mappings=mappings
            )
            print(f"✅ Elasticsearch index created: {index_name}")
        else:
            print(f"ℹ️ Elasticsearch index already exists: {index_name}")


if __name__ == "__main__":
    # 데이터베이스 생성 함수는 이전과 동일하게 사용하세요
    init_postgresql()
    init_elasticsearch()
    init_mongodb()
    print("\n🚀 Index 강화 세팅 완료!")