# 기존 테이블 삭제 후 새로 만듦

import os

import psycopg2

from pymongo import MongoClient

from elasticsearch import Elasticsearch

from datetime import datetime



try:

    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '..', '.env'))

except ImportError:

    pass  # python-dotenv 없으면 시스템 환경변수만 사용





# =====================

# 설정 정보 (.env에서 로드, 도커 컨테이너 이름 기준)

# =====================

DB_USER = os.environ.get("POSTGRES_USER", "datauser")

DB_PASS = os.environ.get("POSTGRES_PASSWORD", "DataPass2026!")

DB_NAME = os.environ.get("POSTGRES_DB", "datadb")

# 로컬이 아닌 도커 네트워크 컨테이너명 사용

DB_HOST = os.environ.get("POSTGRES_HOST", "postgresql") 

ES_URL = os.environ.get("ELASTICSEARCH_URL", "http://elasticsearch-main:8903")

MONGO_HOST = os.environ.get("MONGODB_HOST", "mongo-main")

MONGO_PORT = int(os.environ.get("MONGODB_PORT", 27017))

MONGO_USER = os.environ.get("MONGODB_USER", "datauser")

MONGO_PASS = os.environ.get("MONGODB_PASSWORD", "DataPass2026!")



# =====================

# PostgreSQL

# =====================

def init_postgresql():

    try:

        conn = psycopg2.connect(

            host=DB_HOST,

            database=DB_NAME,

            user=DB_USER,

            password=DB_PASS,

            port=5432

        )

        cur = conn.cursor()



        # 0️⃣ 기존 테이블 전체 삭제 (의존성 고려하여 CASCADE 사용)

        drop_query = """

        DROP TABLE IF EXISTS

            brand_sequences,

            search_results,

            search_logs,

            product_features,

            naver_prices,

            comments,

            inquiry_board,

            products,

            users

        CASCADE;

        """

        cur.execute(drop_query)



        # 1️⃣ 테이블 재생성

        create_queries = [

            # Brand Sequences (스파크 잡을 위한 시퀀스 관리)

            """

            CREATE TABLE brand_sequences (

                brand_name VARCHAR(50) PRIMARY KEY,

                last_seq INTEGER DEFAULT 0

            );

            """,



            # Users (소셜 로그인 지원)

            """

            CREATE TABLE users (

                user_id VARCHAR(50) PRIMARY KEY,

                password VARCHAR(255),

                name VARCHAR(50),

                email VARCHAR(100) UNIQUE,

                role VARCHAR(20) DEFAULT 'USER',

                provider VARCHAR(20),

                provider_id VARCHAR(100),

                profile_image VARCHAR(512),

                last_login TIMESTAMP DEFAULT NOW(),

                create_dt TIMESTAMP DEFAULT NOW(),

                update_dt TIMESTAMP DEFAULT NOW()

            );

            """,



            # Inquiry Board (게시판)

            """

            CREATE TABLE inquiry_board (

                inquiry_board_id BIGSERIAL PRIMARY KEY,

                title VARCHAR(200) NOT NULL,

                content TEXT,

                author_id VARCHAR(50) REFERENCES users(user_id),

                view_count INTEGER DEFAULT 0,

                is_notice BOOLEAN DEFAULT FALSE,

                create_dt TIMESTAMP DEFAULT NOW(),

                update_dt TIMESTAMP DEFAULT NOW()

            );

            """,



            # Comments

            """

            CREATE TABLE comments (

                comment_id BIGSERIAL PRIMARY KEY,

                inquiry_board_id BIGINT REFERENCES inquiry_board(inquiry_board_id) ON DELETE CASCADE,

                author_id VARCHAR(50) REFERENCES users(user_id),

                comment_text TEXT,

                create_dt TIMESTAMP DEFAULT NOW()

            );

            """,

            "CREATE INDEX idx_comments_inquiry_board_id ON comments(inquiry_board_id);",



            # Products (product_id를 VARCHAR(50)으로 변경)

            """

            CREATE TABLE products (

                product_id VARCHAR(50) PRIMARY KEY,

                model_code VARCHAR(50),

                prod_name VARCHAR(255),

                base_price INTEGER,

                category_code VARCHAR(50),

                img_hdfs_path VARCHAR(512),

                brand_name VARCHAR(100),
                
                image_filename VARCHAR(255),
                
                product_url TEXT,

                create_dt TIMESTAMP DEFAULT NOW(),

                update_dt TIMESTAMP DEFAULT NOW()

            );

            """,



            # Naver Prices (product_id VARCHAR(50) 통일, image_url, update_dt 추가)

            """

            CREATE TABLE naver_prices (

                nprice_id BIGSERIAL PRIMARY KEY,

                product_id VARCHAR(50) REFERENCES products(product_id),

                rank SMALLINT,

                price INTEGER,

                mall_name VARCHAR(100),

                mall_url TEXT,

                image_url TEXT,

                create_dt TIMESTAMP DEFAULT NOW(),

                update_dt TIMESTAMP DEFAULT NOW()

            );

            """,

            "CREATE INDEX idx_naver_prices_product_id ON naver_prices(product_id);",



            # Product Features (product_id VARCHAR(50) 통일)

            """

            CREATE TABLE product_features (

                product_id VARCHAR(50) PRIMARY KEY REFERENCES products(product_id),

                detected_desc TEXT,

                create_dt TIMESTAMP DEFAULT NOW()

            );

            """,



            # Search Logs

            """

            CREATE TABLE search_logs (

                log_id BIGSERIAL PRIMARY KEY,

                user_id VARCHAR(50) REFERENCES users(user_id),

                input_img_path VARCHAR(512),

                thumbnail_path VARCHAR(512),

                input_text TEXT,

                applied_category VARCHAR(50),

                image_size INTEGER,

                image_width INTEGER,

                image_height INTEGER,

                search_status VARCHAR(20) DEFAULT 'pending',

                search_result JSON,

                result_count INTEGER DEFAULT 0,

                nprice_id BIGINT REFERENCES naver_prices(nprice_id),

                create_dt TIMESTAMP DEFAULT NOW(),

                update_dt TIMESTAMP DEFAULT NOW()

            );

            """,

            "CREATE INDEX idx_search_logs_user_id ON search_logs(user_id);",

            "CREATE INDEX idx_search_logs_nprice_id ON search_logs(nprice_id);",

            "CREATE INDEX idx_search_logs_create_dt ON search_logs(create_dt DESC);",

            "CREATE INDEX idx_search_logs_status ON search_logs(search_status);",



            # Search Results (product_id VARCHAR(50) 통일)

            """

            CREATE TABLE search_results (

                result_id SERIAL PRIMARY KEY,

                log_id INTEGER REFERENCES search_logs(log_id) ON DELETE CASCADE,

                product_id VARCHAR(50),

                rank INTEGER,

                create_dt TIMESTAMP DEFAULT NOW()

            );

            """

        ]



        for q in create_queries:

            cur.execute(q)



        conn.commit()

        cur.close()

        conn.close()



        print("✅ PostgreSQL: ALL TABLES DROPPED & RECREATED")



    except Exception as e:

        print(f"❌ PostgreSQL 에러: {e}")





# =====================

# MongoDB

# =====================

def init_mongodb():

    try:

        client = MongoClient(

            host=MONGO_HOST,

            port=MONGO_PORT,

            username=MONGO_USER,

            password=MONGO_PASS,

            authSource="admin"

        )



        db = client[DB_NAME]



        # 1. 기존 컬렉션 삭제

        if "product_details" in db.list_collection_names():

            db.product_details.drop()

            print("🗑️ MongoDB collection dropped: product_details")



        # 2. 최신 설계 반영 (product_id 문자열로 매칭)

        db.product_details.insert_one({

            "product_id": "DUMMY_ID",        # PostgreSQL의 product_id와 매칭용

            "detail_desc": "INITIAL DUMMY: 원문 상세 설명이 여기에 통째로 들어갑니다.", 

            "create_dt": datetime.utcnow(),  # 데이터 수집 시점

            "is_dummy": True

        })



        # 3. 조회 성능 향상을 위한 인덱스 생성

        db.product_details.create_index("product_id", unique=True)



        print("✅ MongoDB collection recreated: product_details (Ready for Raw Data)")

        client.close()



    except Exception as e:

        print(f"❌ MongoDB 에러: {e}")





# =====================

# Elasticsearch

# =====================

def init_elasticsearch():

    try:

        es = Elasticsearch(ES_URL)



        # product_id를 문자열(keyword)로 변경하여 통일

        mappings = {

            "properties": {

                "product_id": {"type": "keyword"},

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

            # 기존 인덱스 삭제

            es.indices.delete(index=index_name, ignore=[400, 404])

            print(f"🗑 Elasticsearch index deleted: {index_name}")



            # 재생성

            es.indices.create(

                index=index_name,

                mappings=mappings

            )

            print(f"✅ Elasticsearch index recreated: {index_name}")

            

    except Exception as e:

         print(f"❌ Elasticsearch 에러: {e}")





# =====================

# MAIN

# =====================

if __name__ == "__main__":

    init_postgresql()

    init_elasticsearch()

    init_mongodb()

    print("\n🚀 ALL DATABASES RESET & INITIALIZED!")
