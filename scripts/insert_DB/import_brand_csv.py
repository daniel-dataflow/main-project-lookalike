import os
import sys
import csv
import psycopg2
import hashlib
import argparse

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))
except ImportError:
    pass

def hash_product_id(pid_str: str) -> int:
    """문자열 ID를 64비트 정수로 변환"""
    hex_digest = hashlib.md5(pid_str.encode('utf-8')).hexdigest()
    return int(hex_digest[:15], 16)

def truncate_tables(cur):
    """PostgreSQL 관련 테이블 모두 비우기"""
    try:
        cur.execute("TRUNCATE TABLE search_results, search_logs, product_features, naver_prices, products CASCADE;")
        print("✅ TRUNCATED tables successfully.")
    except Exception as e:
        print(f"❌ Failed to truncate tables: {e}")
        raise

def import_brand_products(cur, brand: str):
    """특정 브랜드의 products.csv 데이터를 삽입"""
    products_csv_path = f'data-pipeline/database/data/{brand}/postgre/products.csv'
    if not os.path.exists(products_csv_path):
        print(f"⚠️  No products.csv found for brand '{brand}' at {products_csv_path}. Skipping.")
        return

    with open(products_csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        product_count = 0
        for row in reader:
            pid_int = hash_product_id(row['product_id'])
            # 'base_price' 컬럼이 없거나 비었을 때 예외처리 수정 (필요하다면)
            base_price = int(float(row['base_price'])) if row.get('base_price') else 0

            cur.execute("""
                INSERT INTO products (
                    product_id, model_code, prod_name, base_price, 
                    category_code, img_hdfs_path, brand_name, gender, origin_url, create_dt, update_dt
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (product_id) DO NOTHING
            """, (
                pid_int,
                row.get('model_code', ''),
                row.get('prod_name', ''),
                base_price,
                row.get('category_code', ''),
                row.get('img_hdfs_path', ''),
                row.get('brand_name', ''),
                row.get('gender', ''),
                row.get('origin_url', ''),
                row.get('create_dt', ''),
                row.get('update_dt', '')
            ))
            product_count += 1
    print(f"✅ Imported {product_count} products for brand '{brand}'.")

def main():
    parser = argparse.ArgumentParser(description="Import brand CSV data into PostgreSQL.")
    parser.add_argument("--init", action="store_true", help="Truncate tables before importing.")
    parser.add_argument("brand", nargs="?", help="Brand name to import data for.")
    args = parser.parse_args()

    if not args.init and not args.brand:
        parser.print_help()
        sys.exit(1)

    DB_USER = os.environ.get("POSTGRES_USER", "datauser")
    DB_PASS = os.environ.get("POSTGRES_PASSWORD", "datauser")
    DB_NAME = os.environ.get("POSTGRES_DB", "datadb")
    DB_HOST = os.environ.get("POSTGRES_HOST", "localhost")
    DB_PORT = os.environ.get("POSTGRES_PORT", "5432")

    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            port=DB_PORT
        )
        cur = conn.cursor()

        if args.init:
            truncate_tables(cur)
        
        if args.brand:
            import_brand_products(cur, args.brand)

        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ Error during import: {e}")
        sys.exit(1)
    finally:
        if conn:
            cur.close()
            conn.close()

if __name__ == "__main__":
    main()
