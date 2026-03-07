import time
import requests
import argparse
from datetime import datetime
from pymongo import MongoClient

# ==========================================
# 1. 설정 (본인의 네이버 API 키를 입력하세요)
# ==========================================
NAVER_CLIENT_ID = "YOUR_CLIENT_ID"       
NAVER_CLIENT_SECRET = "YOUR_CLIENT_SECRET" 

import os

# MongoDB 접속 정보 (텍스트 임베더와 동일)
MONGO_USER = os.environ.get("MONGODB_USER", "datauser")
MONGO_PASS = os.environ.get("MONGODB_PASSWORD", "")
MONGO_URI = os.environ.get("MONGO_URI", f"mongodb://{MONGO_USER}:{MONGO_PASS}@mongo-main:27017/?authSource=admin")

# ==========================================
# 2. 네이버 API 호출 함수 (기존과 동일)
# ==========================================
def fetch_naver_prices(query):
    url = "https://openapi.naver.com/v1/search/shop.json"
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
    }
    params = {"query": query, "display": 5, "sort": "asc"}

    try:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code == 200:
            return response.json().get('items', [])
        return []
    except Exception as e:
        print(f"  [연결 오류] {e}")
        return []

# ==========================================
# 3. 몽고DB 연동 메인 로직 (파일 대신 DB 사용!)
# ==========================================
def run_db_process(brand_name):
    print(f"🚀 [{brand_name.upper()}] 몽고DB에서 데이터를 읽어 네이버 최저가 검색을 시작합니다...")
    
    # 1. 몽고DB 연결
    client = MongoClient(MONGO_URI)
    db = client["datadb"]
    collection = db["fashion_metadata"]

    # 2. 해당 브랜드의 데이터 중, 'naver_price_list'가 아직 없는 문서만 타겟팅!
    query = {
        "brand_name": brand_name,
        "naver_price_list": {"$exists": False} # 중복 검색 방지
    }
    
    docs = list(collection.find(query))
    if not docs:
        print(f"✅ [{brand_name.upper()}] 검색할 새 상품이 없습니다 (모두 업데이트 완료).")
        return

    print(f"🔍 총 {len(docs)}건의 상품에 대해 최저가를 검색합니다.\n")

    for doc in docs:
        product_name = doc.get('product_name')
        if not product_name: continue

        search_query = f"{brand_name} {product_name}"
        items = fetch_naver_prices(search_query)
        
        price_results = []
        if items:
            for idx, item in enumerate(items, 1):
                clean_title = item['title'].replace("<b>", "").replace("</b>", "")
                price_results.append({
                    "rank": idx,
                    "price": int(item['lprice']),
                    "mall_name": item['mallName'],
                    "url": item['link']
                })
        
        # 3. 몽고DB에 해당 상품의 최저가 리스트를 업데이트 ($set)
        collection.update_one(
            {"_id": doc["_id"]},
            {"$set": {
                "naver_price_list": price_results,
                "price_updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }}
        )
        print(f"  -> DB 업데이트 완료: {product_name} (최저가 {len(price_results)}건)")
        
        time.sleep(0.1) # API 과부하 방지

    print(f"🎉 [{brand_name.upper()}] 네이버 최저가 DB 업데이트 완료!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="네이버 쇼핑 최저가 검색기 (DB 연동)")
    parser.add_argument("--brand_name", type=str, required=True, help="처리할 브랜드 이름")
    
    args = parser.parse_args()
    run_db_process(brand_name=args.brand_name)