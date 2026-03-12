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
            pid_str = str(row['product_id'])[:20] if row.get('product_id') else ''
            # 'base_price' 컬럼이 없거나 비었을 때 예외처리 수정 (필요하다면)
            base_price = int(float(row['base_price'])) if row.get('base_price') else 0

            cur.execute("""
                INSERT INTO products (
                    product_id, model_code, prod_name, base_price, 
                    category_code, img_hdfs_path, brand_name, gender, origin_url, create_dt, update_dt
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (product_id) DO NOTHING
            """, (
                pid_str,
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
    print(f"✅ Imported {product_count} products from CSV for brand '{brand}'.")

def import_brand_from_json(cur, brand: str):
    """CSV 파일이 없을 때 JSON 파일들을 읽어서 PostgreSQL에 삽입"""
    import glob
    import json
    json_dir = f'data-pipeline/database/data/{brand}/postgre/json/*.json'
    files = glob.glob(json_dir)
    
    if not files:
        print(f"⚠️  No JSON files found for brand '{brand}' in {json_dir}. Skipping.")
        return

    product_count = 0
    for filepath in files:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                doc = json.load(f)
            
            pid_str = str(doc.get('product_id', ''))[:20] if doc.get('product_id') else ''
            model_code = doc.get('model_code', '')
            prod_name = doc.get('prod_name', '')
            
            # base_price 처리
            base_price = 0
            if doc.get('base_price'):
                try:
                    base_price = int(float(doc['base_price']))
                except (ValueError, TypeError):
                    base_price = 0
                    
            category_code = doc.get('category_code', '')
            img_hdfs_path = doc.get('img_hdfs_path', '')
            brand_name = doc.get('brand_name', '')
            gender = doc.get('gender', '')
            origin_url = doc.get('origine_url', '') # note the spelling
            create_dt = doc.get('create_dt', '')
            update_dt = doc.get('update_dt', '')

            cur.execute("""
                INSERT INTO products (
                    product_id, model_code, prod_name, base_price, 
                    category_code, img_hdfs_path, brand_name, gender, origin_url, create_dt, update_dt
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (product_id) DO NOTHING
            """, (
                pid_str, model_code, prod_name, base_price, category_code, img_hdfs_path, 
                brand_name, gender, origin_url, create_dt, update_dt
            ))
            product_count += 1
        except Exception as e:
            print(f"❌ Error inserting JSON doc {filepath}: {e}")
            
    print(f"✅ Imported {product_count} products from JSON metadata for brand '{brand}'.")

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
            csv_path = f'data-pipeline/database/data/{args.brand}/postgre/products.csv'
            if os.path.exists(csv_path):
                import_brand_products(cur, args.brand)
            else:
                print(f"ℹ️  CSV not found for '{args.brand}'. Falling back to JSON metadata.")
                import_brand_from_json(cur, args.brand)

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
