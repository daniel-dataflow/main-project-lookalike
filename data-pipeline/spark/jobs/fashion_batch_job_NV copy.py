# fashion_batch_job_NV.py

import os
import requests
import psycopg2
from psycopg2 import sql
import time

# ---------------------------------
# 1️⃣ 네이버 API 환경변수
# ---------------------------------
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
NAVER_URL = "https://openapi.naver.com/v1/search/shop.json"

if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
    raise ValueError("NAVER API 환경변수가 설정되지 않았습니다.")

# ---------------------------------
# 2️⃣ DB 연결 정보 (환경변수 권장)
# ---------------------------------
DB_CONFIG = {
    "host": "postgresql",
    "database": "datadb",
    "user": "datauser",
    "password": "DataPass2026!"
}

try:
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    print("DB 연결 성공")

    # ---------------------------------
    # 3️⃣ 오늘 등록된 상품 조회
    # ---------------------------------
    cur.execute("""
        SELECT product_id, prod_name, brand_name
        FROM products
        WWHERE create_dt::date = CURRENT_DATE
    """)

    products = cur.fetchall()
    print(f"오늘 등록 상품 수: {len(products)}")

    for product_id, prod_name, brand_name in products:

        # model_code 또는 brand_name 없으면 skip
        if not brand_name or not prod_name:
            print(f"{product_id} 검색어 부족 → 패스")
            continue

        # ---------------------------------
        # 4️⃣ naver_prices 존재 여부 확인
        # ---------------------------------
        cur.execute("""
            SELECT 1
            FROM naver_prices
            WHERE product_id = %s
            LIMIT 1
        """, (product_id,))

        if cur.fetchone():
            print(f"{product_id} 이미 존재 → 패스")
            continue

        # ---------------------------------
        # 5️⃣ 네이버 API 호출
        # ---------------------------------
        query = f"{brand_name} {prod_name}"

        params = {
            "query": query,
            "display": 5,
            "sort": "asc"
        }

        headers = {
            "X-Naver-Client-Id": NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
        }

        response = requests.get(NAVER_URL, params=params, headers=headers)

        data = response.json()

        if "items" not in data:
            print(f"{product_id} API 응답 이상: {data}")
            continue

        data = response.json()
        items = data.get("items", [])

        if not items:
            print(f"{product_id} 검색 결과 없음")
            continue

        # ---------------------------------
        # 6️⃣ 가격 낮은순 정렬 (안전 정렬)
        # ---------------------------------
        try:
            items_sorted = sorted(
                items,
                key=lambda x: int(x.get("lprice", 0))
            )
        except Exception as e:
            print(f"{product_id} 가격 정렬 실패: {e}")
            continue

        # ---------------------------------
        # 7️⃣ 1~5위 저장
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
        print(f"{product_id} 저장 완료")

        # API 호출 제한 대비
        time.sleep(0.2)

    print("전체 작업 완료")

except Exception as e:
    print("오류 발생:", e)
    if 'conn' in locals():
        conn.rollback()

finally:
    if 'cur' in locals():
        cur.close()
    if 'conn' in locals():
        conn.close()
    print("DB 연결 종료")
