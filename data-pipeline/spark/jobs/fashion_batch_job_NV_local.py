import os
import json
import csv
import requests
import time
import re
import difflib
from pathlib import Path
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 기본 설정 (본인 json 파일 위치와 파일 저장위치에 따라 변경할것!!!)
JSON_DIR = Path(r"########################")
OUT_DIR = Path(r"################################")

OUT_DIR.mkdir(parents=True, exist_ok=True)

# 네이버 오픈 API 키 넣을것!
NAVER_CLIENT_ID = "####################"
NAVER_CLIENT_SECRET = "#################"
NAVER_URL = "https://openapi.naver.com/v1/search/shop.json"

BRAND_KO_MAP = {
    "zara": "자라",
    "8seconds": "에잇세컨즈",
    "musinsa": "무신사",
    "uniqlo": "유니클로",
    "topten": "탑텐"
}

session = requests.Session()
retry = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
session.mount('https://', HTTPAdapter(max_retries=retry))

# 핵심 유틸리티 함수
def get_similarity(target_name: str, naver_title: str) -> float:
    if not target_name or not naver_title: 
        return 0.0
    target_clean = re.sub(r'[^가-힣a-zA-Z0-9]', '', target_name).lower()
    naver_title_clean = re.sub(r'[^가-힣a-zA-Z0-9]', '', naver_title).lower()
    return difflib.SequenceMatcher(None, target_clean, naver_title_clean).ratio()

def extract_core_keywords(prod_name: str, max_keywords: int = 3) -> str:
    if not prod_name: return ""
    clean_name = re.sub(r'[^가-힣a-zA-Z0-9\s]', ' ', prod_name)
    stopwords = {'zara', '자라', '8seconds', '에잇세컨즈', '여성', '남성', '여자', '남자', '공용', '남녀공용', '신상', '정품'}
    
    words = clean_name.split()
    keywords = [w for w in words if w.lower() not in stopwords and not w.isdigit()]
            
    return " ".join(keywords[:max_keywords]) if keywords else prod_name

def search_naver_shopping_api(query: str, display: int = 100) -> list:
    if not query.strip():
        return []
        
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
    }
    params = {
        "query": query,
        "display": display,
        "start": 1,
        "sort": "sim",
        "exclude": "used"
    }

    try:
        response = session.get(NAVER_URL, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        return response.json().get("items", [])
    except Exception as e:
        print(f" API 호출 실패 (query: {query}): {e}")
        return []

# 메인 실행 로직
def main():
    print("==================================================")
    print(" 네이버 쇼핑 API 가격비교 시작")
    print(f" 읽어올 폴더: {JSON_DIR}")
    print(f" 저장될 폴더: {OUT_DIR}")
    print("==================================================\n")

    products = []
    
    # JSON 파일 읽기
    if JSON_DIR.exists():
        for json_path in JSON_DIR.glob('*.json'):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    item = data[0] if isinstance(data, list) and len(data) > 0 else data
                
                # brand_name 우선 탐색
                brand = item.get("brand_name", item.get("brand", "unknown")).lower()
                model_code = item.get("model_code", item.get("code", "unknown"))

                # prod_name 우선 탐색
                prod_name = str(item.get("prod_name") or item.get("name") or item.get("productName") or item.get("title") or "").strip()
                
                # base_price 우선 탐색
                raw_price = str(item.get("base_price", item.get("price", item.get("original_price", "0"))))
                clean_price_str = re.sub(r'[^0-9]', '', raw_price) 
                original_price = int(clean_price_str) if clean_price_str else 0

                products.append({
                    "product_id": item.get("product_id", model_code),
                    "brand_en": brand,
                    "brand_ko": BRAND_KO_MAP.get(brand, brand),
                    "model_code": str(model_code),
                    "prod_name": prod_name,
                    "original_price": original_price
                })
            except Exception as e:
                print(f"⚠️ 파일 읽기 에러 ({json_path.name}): {e}")
                continue

    total_products = len(products)
    print(f" 총 {total_products}개의 상품 데이터를 성공적으로 읽어왔습니다!\n")

    final_results = []
    SIMILARITY_THRESHOLD = 0.35 

    if not products:
        print(" 처리할 상품 데이터가 없습니다. 경로를 확인해주세요.")
        return

    for idx, p_data in enumerate(products, start=1):
        product_id = p_data["product_id"]
        brand_en = p_data["brand_en"]
        brand_ko = p_data["brand_ko"]
        model_code = p_data["model_code"]
        prod_name = p_data["prod_name"]
        original_price = p_data["original_price"]

        print(f"\n▶ [{idx}/{TEST_LIMIT}] 🛒 대상: {brand_en.upper()} | 코드: {model_code} | 이름: {prod_name} | 가격: {original_price:,}원")

        if not prod_name and model_code == "unknown":
            print("  ⚠️ 검색에 필요한 이름과 코드가 모두 없어 건너뜁니다.")
            continue

        clean_model_code = model_code.split('-')[0] if '-' in model_code else model_code
        clean_model_code = clean_model_code.split('_')[0] if '_' in clean_model_code else clean_model_code
        is_valid_code = len(clean_model_code) > 0 and len(clean_model_code) <= 10
        
        core_prod_name = extract_core_keywords(prod_name, max_keywords=3)

        search_queries = {}
        if core_prod_name:
            search_queries["ko_name"] = f"{brand_ko} {core_prod_name}".strip()
            search_queries["en_name"] = f"{brand_en} {core_prod_name}".strip()
        if is_valid_code:
            search_queries["ko_code"] = f"{brand_ko} {clean_model_code}".strip()
            search_queries["en_code"] = f"{brand_en} {clean_model_code}".strip()

        pooled_items = []
        seen_product_ids = set() 

        for condition_key, query in search_queries.items():
            if not query.replace(brand_ko, "").replace(brand_en, "").strip():
                continue
                
            print(f" API 검색 중({condition_key}): {query}")
            items = search_naver_shopping_api(query, display=100) 
            
            if items:
                items_sorted_by_price = sorted(items, key=lambda x: int(x.get("lprice", 0)))
                valid_items_count = 0
                
                for item in items_sorted_by_price:
                    if valid_items_count >= 5: break
                        
                    naver_price = int(item.get("lprice", 0))
                    clean_title = item.get("title", "").replace("<b>", "").replace("</b>", "")

                    if original_price > 0:
                        if naver_price <= (original_price * 0.3) or naver_price >= (original_price * 2.5):
                            continue
                            
                    t_clean = re.sub(r'[^가-힣a-zA-Z0-9]', '', prod_name).lower()
                    n_clean = re.sub(r'[^가-힣a-zA-Z0-9]', '', clean_title).lower()
                    sim_score = 1.0 if (t_clean and t_clean in n_clean) else get_similarity(prod_name, clean_title)

                    is_code_search = "code" in condition_key
                    main_code = clean_model_code.lower()
                    has_exact_code = main_code and main_code in clean_title.replace(" ", "").lower()

                    if is_code_search:
                        if not has_exact_code: continue 
                        if sim_score < (SIMILARITY_THRESHOLD * 0.5): continue 
                    else:
                        if has_exact_code and main_code:
                            if sim_score < (SIMILARITY_THRESHOLD * 0.5): continue
                        else:
                            if sim_score < SIMILARITY_THRESHOLD: continue

                    naver_product_id = str(item.get("productId", ""))
                    if naver_product_id and naver_product_id in seen_product_ids: continue
                    if naver_product_id: seen_product_ids.add(naver_product_id)

                    print(f" [수집 성공 / 유사도 {sim_score:.2f}] {clean_title} ({naver_price:,}원)")
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
            
            time.sleep(0.2)

        pooled_items.sort(key=lambda x: x["price"])
        
        final_top_5 = []
        seen_malls = set() 

        for item in pooled_items:
            if item["mall_name"] not in seen_malls:
                seen_malls.add(item["mall_name"])
                final_top_5.append(item)
            if len(final_top_5) >= 5:
                break

        if final_top_5:
            print(f" [최종 최저가 요약]")
            for rank, item in enumerate(final_top_5, start=1):
                item["final_rank"] = rank
                print(f"    {rank}위: [{item['mall_name']}] {item['title']} - {item['price']:,}원")
        else:
            print(f" 조건(가격/유사도)을 통과한 상품이 없습니다.")

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

    # 최종 결과 CSV 저장
    if final_results:
        output_file = OUT_DIR / "naver_api_optimized_results.csv"
        
        with open(output_file, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            
            writer.writerow([
                'Product ID', 'Brand', 'Model Code', 'Original Name', 'Original Price', 
                'Rank', 'Naver Title', 'Naver Price', 'Mall Name', 'Mall URL', 'Similarity Score'
            ])

            for res in final_results:
                orig = res["original_info"]
                top5 = res["top_5_results"]

                if not top5:
                    writer.writerow([
                        res["product_id"], orig["brand_en"], orig["model_code"], 
                        orig["prod_name"], orig["original_price"], 
                        '-', '결과 없음', '-', '-', '-', '-'
                    ])
                else:
                    for item in top5:
                        writer.writerow([
                            res["product_id"],
                            orig["brand_en"],
                            orig["model_code"],
                            orig["prod_name"],
                            orig["original_price"],
                            item["final_rank"],
                            item["title"],
                            item["price"],
                            item["mall_name"],
                            item["mall_url"],
                            item["similarity_score"]
                        ])
                        
        print(f"\n 수집 완료! CSV 파일이 성공적으로 저장되었습니다: {output_file}")

if __name__ == "__main__":
    main()