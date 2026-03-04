# fashion_batch_job_NV.py

import os
import requests
import psycopg2
import time
from dotenv import load_dotenv

# ---------------------------------
# 1️⃣ 네이버 API 환경변수
# ---------------------------------
# 현재 실행 위치가 ~/main-project-lookalike/data-pipeline/spark/jobs 이므로
# 상위 상위 폴더(~/main-project-lookalike)에 있는 .env를 찾아가야 합니다.
dotenv_path = os.path.join(os.path.dirname(__file__), '../../../.env')
load_dotenv(dotenv_path)
NAVER_CLIENT_ID = "P8VMs8BhXzp3_B1W5H65"
NAVER_CLIENT_SECRET = "sss0tmNxwC"

# NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
# NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
NAVER_URL = "https://openapi.naver.com/v1/search/shop.json"

if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
    raise ValueError("NAVER API 환경변수가 설정되지 않았습니다.")

# ---------------------------------
# 2️⃣ DB 연결 정보
# ---------------------------------
DB_CONFIG = {
    "host": "postgresql",
    #"host": "localhost",
    "database": "datadb",
    "user": "datauser",
    "password": "DataPass2026!"
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
        SELECT product_id, prod_name, brand_name
        FROM products
        WHERE create_dt::date = CURRENT_DATE
    """)

    products = cur.fetchall()
    print(f"📦 오늘 등록 상품 수: {len(products)}")

    for product_id, prod_name, brand_name in products:

        if not brand_name or not prod_name:
            print(f"⚠ {product_id} 검색어 부족 → 패스")
            continue

        query = f"{brand_name} {prod_name}"

        # ---------------------------------
        # 4️⃣ 네이버 API 호출
        # ---------------------------------
        params = {
            "query": query,
            "display": 20, # 공식몰 필터링을 위해 20개 조회
            "sort": "sim"  # asc 대신 sim 사용이 더 정확함
        }

        headers = {
            "X-Naver-Client-Id": NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
        }

        try:
            response = requests.get(NAVER_URL, params=params, headers=headers, timeout=5)
        except requests.exceptions.RequestException as e:
            print(f"❌ API 요청 실패 ({product_id}) → {e}")
            continue

        if response.status_code != 200:
            print(f"❌ API 오류 ({product_id}) → {response.text}")
            continue

        data = response.json()

        if "items" not in data:
            print(f"❌ API 응답 이상 ({product_id}) → {data}")
            continue

        items = data.get("items", [])

        if not items:
            print(f"🔍 {product_id} 검색 결과 없음")
            continue

        # ---------------------------------
        # 4.5️⃣ 공식몰 필터링 로직
        # ---------------------------------
        filtered_items = []
        brand_upper = brand_name.upper() if brand_name else ""
        
        for item in items:
            mall_name = item.get("mallName", "")
            mall_upper = mall_name.upper()
            is_official = False
            
            if brand_upper == "8SECONDS":
                if "에잇세컨즈" in mall_name or "8SECONDS" in mall_upper or "SSF" in mall_upper:
                    is_official = True
            elif brand_upper == "TOPTEN10":
                if "탑텐" in mall_name or "TOPTEN" in mall_upper:
                    is_official = True
                    
            if not is_official:
                filtered_items.append(item)

        if not filtered_items:
            print(f"🔍 {product_id} 유효 검색 결과 없음 (모두 공식몰)")
            continue

        # ---------------------------------
        # 5️⃣ 가격 낮은순 정렬
        # ---------------------------------
        try:
            items_sorted = sorted(
                filtered_items,
                key=lambda x: int(x.get("lprice", 0))
            )
        except Exception as e:
            print(f"❌ {product_id} 가격 정렬 실패 → {e}")
            continue

        try:
            # ---------------------------------
            # 6️⃣ 기존 데이터 삭제 (정합성 보장)
            # ---------------------------------
            cur.execute("""
                DELETE FROM naver_prices
                WHERE product_id = %s
            """, (product_id,))

            # ---------------------------------
            # 7️⃣ 1~5위 저장 (image_url 포함)
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
                """, (
                    product_id,
                    idx,
                    price,
                    mall_name,
                    mall_url,
                    image_url
                ))

            conn.commit()
            print(f"✅ {product_id} 저장 완료")

        except Exception as e:
            conn.rollback()
            print(f"❌ DB 저장 실패 ({product_id}) → {e}")
            continue

        # API 호출 제한 대비
        time.sleep(0.2)

    print("🎉 전체 작업 완료")

except Exception as e:
    print("🚨 시스템 오류 발생:", e)

finally:
    if 'cur' in locals():
        cur.close()
    if 'conn' in locals():
        conn.close()
    print("🔌 DB 연결 종료")
