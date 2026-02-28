import os
import csv
import psycopg2
import hashlib

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '..', '.env'))
except ImportError:
    pass

def hash_product_id(pid_str: str) -> int:
    """문자열 ID를 64비트 정수로 변환"""
    hex_digest = hashlib.md5(pid_str.encode('utf-8')).hexdigest()
    return int(hex_digest[:15], 16)

def main():
    DB_USER = os.environ.get("POSTGRES_USER", "datauser")
    DB_PASS = os.environ.get("POSTGRES_PASSWORD", "")
    DB_NAME = os.environ.get("POSTGRES_DB", "datadb")

    conn = psycopg2.connect(
        host="localhost",
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
        port=5432
    )
    cur = conn.cursor()

    try:
        # 기존 데이터 모두 비우기 (Zara 데이터 등)
        cur.execute("TRUNCATE TABLE search_results, search_logs, product_features, naver_prices, products CASCADE;")
        print("✅ TRUNCATED tables successfully.")
        
        # 1. Load products.csv
        products_csv_path = 'data-pipeline/elasticsearch/data/8seconds/postgres/products.csv'
        with open(products_csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            product_count = 0
            for row in reader:
                pid_int = hash_product_id(row['product_id'])
                cur.execute("""
                    INSERT INTO products (
                        product_id, model_code, prod_name, base_price, 
                        category_code, img_hdfs_path, brand_name, gender, origin_url, create_dt, update_dt
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (product_id) DO NOTHING
                """, (
                    pid_int,
                    row['model_code'],
                    row['prod_name'],
                    int(row['base_price']) if row['base_price'] else 0,
                    row['category_code'],
                    row['img_hdfs_path'],
                    row['brand_name'],
                    row['gender'],
                    row['origin_url'],
                    row['create_dt'],
                    row['update_dt']
                ))
                product_count += 1
        print(f"✅ Imported {product_count} products.")

        # 2. Load naver_prices.csv
        prices_csv_path = 'data-pipeline/elasticsearch/data/8seconds/postgres/naver_prices.csv'
        with open(prices_csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            price_count = 0
            for row in reader:
                pid_int = hash_product_id(row['product_id'])
                cur.execute("""
                    INSERT INTO naver_prices (
                        product_id, rank, price, mall_name, mall_url, create_dt
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    pid_int,
                    int(row['rank']) if row['rank'] else 1,
                    int(row['price']) if row['price'] else 0,
                    row['mall_name'],
                    row['mall_url'],
                    row['create_dt']
                ))
                price_count += 1
        print(f"✅ Imported {price_count} price entries.")

        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"❌ Error during import: {e}")
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    main()
