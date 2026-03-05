import os
import time
import random
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import datetime

# .env 환경변수 로딩
load_dotenv()

# .env에 저장된 예민한 정보 읽기
NAVER_CLIENT_ID = os.getenv("X_NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("X_NAVER_CLIENT_SECRET")

# 네이버 API 필수값 검증
if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
    print("❌ X_NAVER_CLIENT_ID 또는 X_NAVER_CLIENT_SECRET 환경변수가 .env에 없습니다.")
    exit(1)

NAVER_SHOPPING_API_URL = "https://openapi.naver.com/v1/search/shop.json"

# DB 정보 (하드코딩 제거, .env만을 참조)
DB_HOST = os.getenv("POSTGRES_HOST")
DB_PORT = os.getenv("POSTGRES_PORT")
DB_USER = os.getenv("POSTGRES_USER")
DB_PASS = os.getenv("POSTGRES_PASSWORD")
DB_NAME = os.getenv("POSTGRES_DB")

def get_db_connection():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASS,
        dbname=DB_NAME
    )

def search_naver_shopping(query: str, display: int = 5) -> list:
    """네이버 쇼핑 API를 호출하여 검색 결과를 반환합니다."""
    # 사용자 지침 "X-Naver-Client-Id을 참조해봐" 반영
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    params = {
        "query": query,
        "display": display,
        "sort": "sim"  # 유사도순 정렬
    }
    
    try:
        response = requests.get(NAVER_SHOPPING_API_URL, headers=headers, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        return data.get("items", [])
    except Exception as e:
        print(f"❌ 네이버 쇼핑 API 호출 실패 (query: {query}): {e}")
        return []

def main():
    # 배치 실행 시간 기록 (로그 파일명용)
    batch_run_time = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    log_filename = f"{batch_run_time}_missing_items.log"
    print(f"🚀 네이버 쇼핑 API -> naver_prices 업데이트 배치를 시작합니다. (상위 5개 최저가)")
    conn = None
    try:
        conn = get_db_connection()
        conn.autocommit = False
        
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 1. products 테이블에서 처리 대상 조회 (또는 전체)
            # 기존에 가격이 수집되지 않은 상품만 조회
            cur.execute("""
                SELECT p.product_id, p.prod_name, p.brand_name, p.model_code
                FROM products p
                WHERE p.product_id NOT IN (
                    SELECT DISTINCT product_id 
                    FROM naver_prices
                )
            """)
            products = cur.fetchall()
            
            print(f"총 {len(products)}개의 상품 정보를 조회했습니다.")
            
            updated_count = 0
            
            for prod in products:
                pid = prod['product_id']
                # 1차: 브랜드명 + 모델코드(상품코드)
                items = []
                search_query = ""
                
                if prod.get('model_code'):
                    search_query = f"{prod['brand_name']} {prod['model_code']}".strip()
                    items = search_naver_shopping(search_query, display=20)
                
                # 2차: 브랜드명 + 상품명 (모델코드가 없거나 1차 검색 실패 시)
                if not items:
                    search_query_fallback = f"{prod['brand_name']} {prod['prod_name']}".strip()
                    if search_query:
                        print(f"⚠️ 1차 검색 실패, 상품명으로 2차 검색 시도: {search_query_fallback}")
                        time.sleep(1.0) # 혹시 모를 레이트 리밋 방지
                    search_query = search_query_fallback
                    items = search_naver_shopping(search_query, display=20)
                    used_fallback = True
                else:
                    used_fallback = False
                
                if items:
                    # 공식몰 제외 필터 삭제 (모든 결과 허용)
                    # 가격 낮은순 정렬
                    items_sorted = sorted(items, key=lambda x: int(x.get("lprice", 0)))
                    
                    # 기존 데이터 삭제 후 새 데이터 삽입
                    cur.execute("DELETE FROM naver_prices WHERE product_id = %s", (pid,))
                    
                    # 상위 5개 삽입
                    for rank, item in enumerate(items_sorted[:5], start=1):
                        price = int(item.get("lprice", 0))
                        mall_name = item.get("mallName", "알 수 없음")
                        mall_url = item.get("link", "")
                        image_url = item.get("image", "")

                        # 2. naver_prices 테이블에 정규 INSERT 수행
                        cur.execute("""
                            INSERT INTO naver_prices (product_id, rank, price, mall_name, mall_url, image_url, create_dt, update_dt)
                            VALUES (%s, %s, %s, %s, %s, %s, now(), now())
                        """, (pid, rank, price, mall_name, mall_url, image_url))
                        
                    saved_count = min(5, len(items_sorted))
                    updated_count += saved_count
                    print(f"✅ 업데이트 완료: {search_query} -> {saved_count}개의 최저가 저장")

                    # 로그 디렉토리 준비
                    log_dir = "logs/spark/naver_api"
                    os.makedirs(log_dir, exist_ok=True)
                    log_file = os.path.join(log_dir, log_filename)
                    
                    # 1. 1차 실패 후 2차(상품명)에서 성공한 경우 로깅
                    if used_fallback:
                        with open(log_file, "a", encoding="utf-8") as f:
                            f.write(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [2차검색성공] BRAND: {prod.get('brand_name', 'N/A')} | CODE: {prod.get('model_code', 'N/A')} | NAME: {prod.get('prod_name', 'N/A')} -> {saved_count}건\n")
                    
                    # 2. 검색 결과가 5개 미만인 경우 로깅
                    if len(items_sorted) < 5:
                        with open(log_file, "a", encoding="utf-8") as f:
                            f.write(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [최저가부족({len(items_sorted)}/5)] BRAND: {prod.get('brand_name', 'N/A')} | CODE: {prod.get('model_code', 'N/A')} | NAME: {prod.get('prod_name', 'N/A')}\n")

                else:
                    print(f"⚠️ 검색 결과 없음: {search_query}")
                    # 실패 결과를 파일로 남김 (0건)
                    log_dir = "logs/spark/naver_api"
                    os.makedirs(log_dir, exist_ok=True)
                    log_file = os.path.join(log_dir, log_filename)
                    with open(log_file, "a", encoding="utf-8") as f:
                        f.write(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [검색실패(0건)] BRAND: {prod.get('brand_name', 'N/A')} | CODE: {prod.get('model_code', 'N/A')} | NAME: {prod.get('prod_name', 'N/A')}\n")
                
                # 네이버 안티 봇 회피 및 API Rate Limit 고려하여 충분한 랜덤 지연 시간 추가
                delay = random.uniform(1.5, 3.0)
                time.sleep(delay)

            # 변경사항 최종 커밋
            conn.commit()
            print(f"🎉 배치 작업 완료! 총 {updated_count}건의 가격 정보가 갱신되었습니다.")

    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ 작업 중 오류 발생 로그 모음: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    main()
