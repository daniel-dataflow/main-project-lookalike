import os
import csv
import psycopg2
import hashlib
import glob
from dotenv import load_dotenv

# .env 환경변수 로딩
load_dotenv()

DB_HOST = os.getenv("POSTGRES_HOST", "127.0.0.1" # 호스트 환경 고정
DB_PORT = os.getenv("POSTGRES_PORT", "5432")
DB_USER = os.getenv("POSTGRES_USER", "datauser")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "DataPass2026!")
DB_NAME = os.getenv("POSTGRES_DB", "datadb")

CSV_PATH = "data-pipeline/elasticsearch/data/products.csv"
IMAGE_DIR = "data-pipeline/elasticsearch/data/image"

def hash_product_id(pid_str: str) -> int:
    """문자열 ID를 64비트 정수로 변환하여 Elasticsearch와 PostgreSQL ID를 일치시킵니다."""
    hex_digest = hashlib.md5(pid_str.encode('utf-8')).hexdigest()
    return int(hex_digest[:15], 16)

def get_db_connection():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASS,
        dbname=DB_NAME
    )

def main():
    print("🚀 PostgreSQL products 테이블 초기화 및 CSV 임포트를 시작합니다.")
    
    # HDFS 구성을 위한 로컬 파일 맵 생성
    local_imgs = glob.glob(f"{IMAGE_DIR}/*.jpg")
    img_map = {}
    for path in local_imgs:
        filename = os.path.basename(path)
        parts = filename.replace(".jpg", "").split("_")
        if parts:
            model_code = parts[-1]
            brand = parts[0]
            img_map[model_code.upper()] = (brand, filename)

    conn = get_db_connection()
    conn.autocommit = False
    
    try:
        with conn.cursor() as cur:
            print("🧹 기존 products 및 연관 데이터를 삭제합니다...")
            cur.execute("TRUNCATE TABLE products CASCADE;")
            
            if not os.path.exists(CSV_PATH):
                raise FileNotFoundError(f"CSV 파일을 찾을 수 없습니다: {CSV_PATH}")
                
            print(f"📖 {CSV_PATH} 읽는 중...")
            with open(CSV_PATH, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                
                insert_count = 0
                for row in reader:
                    model_code = row['model_code'].strip().upper()
                    
                    # 1. 실제 HDFS 경로 동적 생성
                    if model_code in img_map:
                        brand, filename = img_map[model_code]
                        hdfs_path = f"/raw/{brand}/image/{filename}"
                    else:
                        hdfs_path = row['local_image_path'] # 매칭 실패시 폴백
                        
                    # 2. ID 생성
                    pid_int = hash_product_id(row['product_id'])
                    
                    # 3. CSV 정보에서 직접 읽어옴
                    category_code = row['category']
                    gender = row['gender']
                        
                    # 4. 가격 정리
                    price_str = row['price']
                    price = int(price_str) if price_str and price_str.isdigit() else 0
                    
                    cur.execute("""
                        INSERT INTO products (
                            product_id, model_code, prod_name, base_price, 
                            category_code, img_hdfs_path, brand_name, gender
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        pid_int,
                        row['model_code'],
                        row['product_name'],
                        price,
                        category_code,
                        hdfs_path,
                        row['brand'],
                        gender
                    ))
                    insert_count += 1
            
            conn.commit()
            print(f"✅ 총 {insert_count}건의 상품이 products 테이블에 저장되었습니다.")
            
    except Exception as e:
        conn.rollback()
        print(f"❌ 오류 발생: {e}")
        exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
