import os
import psycopg2
from dotenv import load_dotenv

# .env 로드
dotenv_path = os.path.join(os.path.dirname(__file__), '../../.env')
load_dotenv(dotenv_path)

DB_CONFIG = {
    "host": "localhost", # Docker 밖이므로 localhost
    "database": os.getenv("POSTGRES_DB"),
    "user": os.getenv("POSTGRES_USER"),
    "password": os.getenv("POSTGRES_PASSWORD")
}

try:
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    
    print("--- 최근 5일간 등록된 상품 수 ---")
    cur.execute("""
        SELECT create_dt::date, COUNT(*) 
        FROM products 
        GROUP BY create_dt::date 
        ORDER BY create_dt::date DESC 
        LIMIT 5
    """)
    for row in cur.fetchall():
        print(f"날짜: {row[0]} | 개수: {row[1]}")
        
    print("\n--- DB 현재 시각 (CURRENT_DATE) ---")
    cur.execute("SELECT CURRENT_DATE, NOW()")
    row = cur.fetchone()
    print(f"CURRENT_DATE: {row[0]} | NOW(): {row[1]}")

    cur.close()
    conn.close()
except Exception as e:
    print(f"오류 발생: {e}")
