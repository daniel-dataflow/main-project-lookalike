import os
import json
import requests
import time
import random
import re
import difflib
from pathlib import Path
from google.colab import userdata
from google.colab import drive

# 드라이브 마운트 및 환경 설정
drive.mount('/content/drive')

try:
    NAVER_CLIENT_ID = userdata.get("X_NAVER_CLIENT_ID")
    NAVER_CLIENT_SECRET = userdata.get("X_NAVER_CLIENT_SECRET")
except:
    NAVER_CLIENT_ID = "P8VMs8BhXzp3_B1W5H65"
    NAVER_CLIENT_SECRET = "sss0tmNxwC"

NAVER_URL = "https://openapi.naver.com/v1/search/shop.json"

BRAND_KO_MAP = {
    "zara": "자라",
    "musinsa": "무신사",
    "uniqlo": "유니클로",
    "8seconds": "에잇세컨즈",
    "topten": "탑텐"
}

def get_similarity(target_name: str, naver_title: str) -> float:
    target_clean = re.sub(r'[^가-힣a-zA-Z0-9]', '', target_name)
    naver_clean = re.sub(r'[^가-힣a-zA-Z0-9]', '', naver_title)
    if not target_clean or not naver_clean:
        return 0.0
    return difflib.SequenceMatcher(None, target_clean, naver_clean).ratio()

def search_naver_shopping(query: str, display: int = 100) -> list:
    if not query.strip():
        return []
        
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0"
    }
    params = {
        "query": query,
        "display": display, 
        # 정확도(sim)로 100개
        "sort": "sim"       
    }

    try:
        response = requests.get(NAVER_URL, headers=headers, params=params, timeout=5)
        response.raise_for_status()
        return response.json().get("items", [])
    except Exception as e:
        print(f"API 호출 실패 (query: {query}): {e}")
        return []

# ---------------------------------
# 2️⃣ 데이터 읽기
# ---------------------------------
BASE_DIR = Path('/content/drive/MyDrive/# 데이터 저장 위치')
JSON_DIR = BASE_DIR / 'json'
NAVER_OUT_DIR = BASE_DIR / 'naver_prices' 

NAVER_OUT_DIR.mkdir(parents=True, exist_ok=True)

products = []

if JSON_DIR.exists():
    for json_path in JSON_DIR.glob('*.json'):
        file_stem = json_path.stem

        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            item = data[0] if isinstance(data, list) else data

        parts = file_stem.split('_')
        if len(parts) >= 4:
            brand = parts[0].lower()
            model_code = "_".join(parts[3:])
        else:
            brand = item.get("brand", "zara").lower()
            model_code = item.get("model_code", "unknown")

        prod_name = item.get("prod_name", item.get("name", ""))
        
        raw_price = str(item.get("price", item.get("original_price", "0")))
        clean_price_str = re.sub(r'[^0-9]', '', raw_price) 
        original_price = int(clean_price_str) if clean_price_str else 0

        products.append({
            "product_id": item.get("product_id", model_code),
            "brand_en": brand,
            "brand_ko": BRAND_KO_MAP.get(brand, brand),
            "model_code": model_code,
            "prod_name": prod_name,
            "original_price": original_price
        })

total_products = len(products)
print(f"📦 총 {total_products}개의 상품 데이터를 성공적으로 읽어왔습니다!\n")

final_results = []
SIMILARITY_THRESHOLD = 0.6 

# 3️⃣ 네이버 검색 및 다중 필터링
if products:
    for idx, p in enumerate(products, start=1):
        product_id = p["product_id"]
        brand_en = p["brand_en"]
        brand_ko = p["brand_ko"]
        model_code = p["model_code"]
        prod_name = p["prod_name"]
        original_price = p["original_price"]

        print(f"\n▶ [{idx}/{total_products}] 🛒 대상: {brand_en.upper()} | 코드: {model_code} | 이름: {prod_name}")

        clean_model_code = model_code.split('-')[0] if '-' in model_code else model_code
        clean_model_code = clean_model_code.split('_')[0] if '_' in clean_model_code else clean_model_code

        search_queries = {
            "ko_name": f"{brand_ko} {prod_name}".strip(),
            "ko_code": f"{brand_ko} {clean_model_code}".strip(),
            "en_name": f"{brand_en} {prod_name}".strip(),
            "en_code": f"{brand_en} {clean_model_code}".strip()
        }

        pooled_items = []
        seen_product_ids = set() 

        for condition_key, query in search_queries.items():
            if not query.replace(brand_ko, "").replace(brand_en, "").strip():
                continue
                
            print(f"  검색어({condition_key}): {query}")
            items = search_naver_shopping(query, display=100) 
            
            if items:
                # 정확도순으로 가져왔기 때문에, 바로 반복문 돌리지 않고 가져온 100개를 임시로 가격순 정렬합니다.
                items_sorted_by_price = sorted(items, key=lambda x: int(x.get("lprice", 0)))
                valid_items_count = 0
                
                for item in items_sorted_by_price:
                    if valid_items_count >= 5: 
                        break
                        
                    naver_price = int(item.get("lprice", 0))
                    clean_title = item.get("title", "").replace("<b>", "").replace("</b>", "")

                    # 방어 0: 악성 키워드 필터링
                    spam_keywords = ["구매대행", "중고", "구제", "병행수입"]
                    is_spam = any(keyword in clean_title for keyword in spam_keywords)
                    if is_spam:
                        continue

                    # 방어 1: 가격 방어
                    if original_price > 0:
                        if naver_price <= (original_price * 0.3) or naver_price >= (original_price * 2.0):
                            continue
                            
                    # 방어 2: 이름 유사도
                    t_clean = re.sub(r'[^가-힣a-zA-Z0-9]', '', prod_name).lower()
                    n_clean = re.sub(r'[^가-힣a-zA-Z0-9]', '', clean_title).lower()
                    
                    if t_clean and t_clean in n_clean:
                        sim_score = 1.0
                    else:
                        sim_score = get_similarity(prod_name, clean_title)

                    # 방어 3: 코드 매칭 검사
                    is_code_search = "code" in condition_key
                    main_code = clean_model_code.lower()
                    has_exact_code = main_code and main_code in clean_title.replace(" ", "").lower()

                    if is_code_search:
                        if not has_exact_code:
                            continue 
                        if sim_score < (SIMILARITY_THRESHOLD * 0.5): 
                            continue 
                    else:
                        if sim_score < SIMILARITY_THRESHOLD:
                            continue

                    naver_product_id = item.get("productId", "")
                    if naver_product_id and naver_product_id in seen_product_ids:
                        continue
                    if naver_product_id:
                        seen_product_ids.add(naver_product_id)

                    print(f"    ✔️ [후보등록 / 유사도 {sim_score:.2f}] {clean_title} ({naver_price:,}원)")

                    pooled_items.append({
                        "found_by": condition_key,
                        "title": clean_title,
                        "similarity_score": round(sim_score, 2),
                        "price": naver_price,
                        "mall_name": item.get("mallName", "알수없음"),
                        "mall_url": item.get("link", ""),
                        "image_url": item.get("image", ""),
                        "naver_product_id": naver_product_id
                    })
                    
                    valid_items_count += 1
            
            time.sleep(0.5)

        # 전체 모인 후보군을 가격 낮은 순으로 최종 정렬하고 중복 쇼핑몰 제거
        pooled_items.sort(key=lambda x: x["price"])
        
        final_top_5 = []
        seen_malls = set() 

        for item in pooled_items:
            mall_name = item["mall_name"]
            
            if mall_name not in seen_malls:
                seen_malls.add(mall_name)
                final_top_5.append(item)
                
            if len(final_top_5) >= 5:
                break

        if final_top_5:
            print(f"  [최종 상위 요약 - 쇼핑몰 중복 제거]")
            for rank, item in enumerate(final_top_5, start=1):
                item["final_rank"] = rank
                print(f"    {rank}위: [{item['mall_name']}] {item['title']} - {item['price']:,}원")
        else:
            print(f"  조건(가격/유사도)을 통과한 상품이 없습니다.")

        final_results.append({
            "product_id": product_id,
            "original_info": {
                "brand_en": brand_en,
                "brand_ko": brand_ko,
                "model_code": model_code,
                "prod_name": prod_name,
                "original_price": original_price
            },
            "top_5_results": final_top_5 
        })

        print("-" * 60)
        time.sleep(random.uniform(1.0, 2.0))

# 4️⃣ 최종 결과 JSON 저장
if final_results:
    output_file = NAVER_OUT_DIR / "naver_top5_integrated_results.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(final_results, f, ensure_ascii=False, indent=2)
    print(f"\n 수집 완료! 데이터가 저장되었습니다: {output_file}")
else:
    print("\n 저장할 검색 결과가 없습니다.")