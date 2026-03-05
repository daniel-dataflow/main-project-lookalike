# fashion_batch_job_NV.py

import os
import requests
import psycopg2
import time
import random
import datetime
from dotenv import load_dotenv

# ---------------------------------
# 1️⃣ 네이버 API 환경변수
# ---------------------------------
# 현재 실행 위치가 ~/main-project-lookalike/data-pipeline/spark/jobs 이므로
# 상위 상위 폴더(~/main-project-lookalike)에 있는 .env를 찾아가야 합니다.
dotenv_path = os.path.join(os.path.dirname(__file__), '../../../.env')
load_dotenv(dotenv_path)

NAVER_CLIENT_ID = os.getenv("X_NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("X_NAVER_CLIENT_SECRET")
NAVER_URL = "https://openapi.naver.com/v1/search/shop.json"

if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
    raise ValueError("NAVER API 환경변수가 설정되지 않았습니다.")

def search_naver_shopping(query: str, display: int = 20) -> list:
    """네이버 쇼핑 API를 호출하여 검색 결과를 반환합니다."""
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
        response = requests.get(NAVER_URL, headers=headers, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        return data.get("items", [])
    except Exception as e:
        print(f"❌ 네이버 쇼핑 API 호출 실패 (query: {query}): {e}")
        return []

# ---------------------------------
# 2️⃣ DB 연결 정보
# ---------------------------------
DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST"),
    "database": os.getenv("POSTGRES_DB"),
    "user": os.getenv("POSTGRES_USER"),
    "password": os.getenv("POSTGRES_PASSWORD")
}

try:
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    cur = conn.cursor()

    print("✅ DB 연결 성공")

    # ---------------------------------
    # 3️⃣ 오늘 등록된 상품 조회
    # ---------------------------------
    cur.execute("""
        SELECT product_id, model_code, prod_name, brand_name
        FROM products
        WHERE create_dt::date = CURRENT_DATE
    """)

    products = cur.fetchall()
    print(f"📦 오늘 등록 상품 수: {len(products)}")

    # 로그 파일 경로 설정 (날짜/시간 포함)
    batch_run_time = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    log_dir = os.path.join(os.path.dirname(__file__), '../../../logs/spark/naver_api')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"{batch_run_time}_missing_items.log")

    for product_id, model_code, prod_name, brand_name in products:

        if not brand_name or (not prod_name and not model_code):
            print(f"⚠ {product_id} 검색어 부족 → 패스")
            continue

        # ---------------------------------
        # 4️⃣ 네이버 API 호출 (2-Step Fallback)
        # ---------------------------------
        items = []
        search_query = ""
        
        # 1차 검색: 브랜드명 + 모델코드
        if model_code:
            search_query = f"{brand_name} {model_code}".strip()
            items = search_naver_shopping(search_query, display=20)
        
        # 2차 검색: 브랜드명 + 상품명 (1차 실패 시)
        used_fallback = False
        if not items:
            search_query_fallback = f"{brand_name} {prod_name}".strip()
            if search_query:
                print(f"⚠️ 1차 검색 실패, 상품명으로 2차 검색 시도: {search_query_fallback}")
                time.sleep(1.0)
            search_query = search_query_fallback
            items = search_naver_shopping(search_query, display=20)
            used_fallback = True

        if not items:
            print(f"🔍 {product_id} 검색 결과 없음")
            # 실패 로깅 기록
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [검색실패(0건)] BRAND: {brand_name or 'N/A'} | CODE: {model_code or 'N/A'} | NAME: {prod_name or 'N/A'}\n")
            continue

        # ---------------------------------
        # 5️⃣ 가격 낮은순 정렬
        # ---------------------------------
        try:
            items_sorted = sorted(items, key=lambda x: int(x.get("lprice", 0)))
        except Exception as e:
            print(f"❌ {product_id} 가격 정렬 실패 → {e}")
            continue

        # 저장 건수 파악
        saved_count = min(5, len(items_sorted))

        # 로깅 기록 (2차 검색 성공 또는 최저가 부족)
        if used_fallback:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [2차검색성공] BRAND: {brand_name or 'N/A'} | CODE: {model_code or 'N/A'} | NAME: {prod_name or 'N/A'} -> {saved_count}건\n")
        
        if len(items_sorted) < 5:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [최저가부족({len(items_sorted)}/5)] BRAND: {brand_name or 'N/A'} | CODE: {model_code or 'N/A'} | NAME: {prod_name or 'N/A'}\n")

        try:
            # ---------------------------------
            # 6️⃣ 기존 데이터 삭제 (정합성 보장)
            # ---------------------------------
            cur.execute("""
                DELETE FROM naver_prices
                WHERE product_id = %s
            """, (product_id,))

            # ---------------------------------
            # 7️⃣ 상위 5개 저장 (image_url 포함)
            # ---------------------------------
            for idx, item in enumerate(items_sorted[:5], start=1):
                price = int(item.get("lprice", 0))
                mall_name = item.get("mallName", "")
                mall_url = item.get("link", "")
                image_url = item.get("image", "")

                cur.execute("""
                    INSERT INTO naver_prices
                    (product_id, rank, price, mall_name, mall_url, image_url, create_dt, update_dt)
                    VALUES (%s, %s, %s, %s, %s, %s, now(), now())
                """, (product_id, idx, price, mall_name, mall_url, image_url))

            conn.commit()
            print(f"✅ {product_id} 완료: {search_query} -> {saved_count}건 저장")

        except Exception as e:
            conn.rollback()
            print(f"❌ DB 저장 실패 ({product_id}) → {e}")
            continue

        # API 호출 제한 대비 (랜덤 지연)
        time.sleep(random.uniform(1.5, 3.0))

    print("🎉 전체 작업 완료")

except Exception as e:
    print("🚨 시스템 오류 발생:", e)

finally:
    if 'cur' in locals():
        cur.close()
    if 'conn' in locals():
        conn.close()
    print("🔌 DB 연결 종료")
