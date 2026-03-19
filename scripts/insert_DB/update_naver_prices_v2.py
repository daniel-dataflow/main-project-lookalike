import os
import requests
import psycopg2
import time
import random
import datetime
import re
import difflib
import sys
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 1. 환경변수 및 세션 설정
# .env 파일 위치 (프로젝트 루트)
dotenv_path = os.path.join(os.path.dirname(__file__), '../../.env')
load_dotenv(dotenv_path)

NAVER_CLIENT_ID = os.getenv("X_NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("X_NAVER_CLIENT_SECRET")
NAVER_URL = "https://openapi.naver.com/v1/search/shop.json"

if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
    print("[WARNING] NAVER API 환경변수가 설정되지 않았습니다. .env 파일을 확인해주세요.")

session = requests.Session()
retry = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
session.mount('https://', HTTPAdapter(max_retries=retry))

# 브랜드 한글 매핑
BRAND_KO_MAP = {
    "zara": "자라",
    "8seconds": "에잇세컨즈",
    "musinsa": "무신사",
    "uniqlo": "유니클로",
    "topten": "탑텐"
}

# 2. 핵심 유틸리티 함수 (NV.py에서 이식)
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
        "query": query, "display": display, "start": 1, "sort": "sim", "exclude": "used"
    }
    
    try:
        response = session.get(NAVER_URL, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        return response.json().get("items", [])
    except Exception as e:
        print(f" 네이버 쇼핑 API 호출 실패 (query: {query}): {e}")
        return []

# 3. 메인 배치 로직
def main():
    # 처리 개수 (LIMIT) 인자 처리
    limit = 500
    if len(sys.argv) > 1:
        try:
            limit = int(sys.argv[1])
        except ValueError:
            print(f"[ERROR] 잘못된 LIMIT 인자: '{sys.argv[1]}'. 기본값 {limit}을 사용합니다.")

    # DB 연결 정보 (localhost 기본값)
    DB_CONFIG = {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "database": os.getenv("POSTGRES_DB"),
        "user": os.getenv("POSTGRES_USER"),
        "password": os.getenv("POSTGRES_PASSWORD"),
        "port": os.getenv("POSTGRES_PORT", "5432")
    }

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = False
        cur = conn.cursor()

        print(f"🚀 [V2] DB 연결 성공 (Host: {DB_CONFIG['host']})")
        print(f"🔍 가격 정보가 누락된 상품 중 최신순으로 상위 {limit}개를 처리합니다.")

        # 누락된 데이터 조회 쿼리 (V2 핵심)
        cur.execute("""
            SELECT p.product_id, p.model_code, p.prod_name, p.brand_name, p.base_price
            FROM products p
            WHERE NOT EXISTS (
                SELECT 1 FROM naver_prices np WHERE np.product_id = p.product_id
            )
            ORDER BY p.create_dt DESC
            LIMIT %s
        """, (limit,))

        products = cur.fetchall()
        print(f"📦 처리 대상 상품 수: {len(products)}")

        if not products:
            print("✅ 모든 상품에 가격 정보가 존재합니다. 작업을 종료합니다.")
            return

        batch_run_time = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        log_dir = os.path.join(os.path.dirname(__file__), '../../logs/spark/naver_api')
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, f"{batch_run_time}_backfill_v2.log")

        SIMILARITY_THRESHOLD = 0.35

        for product_id, model_code, prod_name, brand_name, base_price in products:
            brand_en = str(brand_name).lower() if brand_name else "unknown"
            brand_ko = BRAND_KO_MAP.get(brand_en, brand_en)
            prod_name = str(prod_name).strip() if prod_name else ""
            model_code = str(model_code).strip() if model_code else ""
            
            raw_price = str(base_price) if base_price else "0"
            clean_price_str = re.sub(r'[^0-9]', '', raw_price) 
            original_price = int(clean_price_str) if clean_price_str else 0

            if not brand_name or (not prod_name and not model_code):
                continue

            print(f"▶ [{brand_en.upper()}] {prod_name} ({model_code}) 검색 중...")

            # 다각도 검색 쿼리 구성
            clean_model_code = model_code.split('-')[0] if '-' in model_code else model_code
            clean_model_code = clean_model_code.split('_')[0] if '_' in clean_model_code else clean_model_code
            is_valid_code = 0 < len(clean_model_code) <= 10
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
                items = search_naver_shopping_api(query, display=100)
                if not items: continue
                
                items_sorted = sorted(items, key=lambda x: int(x.get("lprice", 0)))
                valid_count = 0
                for item in items_sorted:
                    if valid_count >= 5: break
                    
                    naver_price = int(item.get("lprice", 0))
                    clean_title = item.get("title", "").replace("<b>", "").replace("</b>", "")
                    
                    # 필터링 로직 (스팸/가격/유사도)
                    if any(kw in clean_title for kw in ["구매대행", "구제", "중고", "병행수입"]): continue
                    if original_price > 0 and (naver_price <= original_price * 0.3 or naver_price >= original_price * 2.5): continue
                            
                    t_clean = re.sub(r'[^가-힣a-zA-Z0-9]', '', prod_name).lower()
                    n_clean = re.sub(r'[^가-힣a-zA-Z0-9]', '', clean_title).lower()
                    sim_score = 1.0 if (t_clean and t_clean in n_clean) else get_similarity(prod_name, clean_title)

                    is_code_search = "code" in condition_key
                    main_code_low = clean_model_code.lower()
                    has_exact_code = main_code_low and main_code_low in clean_title.replace(" ", "").lower()

                    if is_code_search:
                        if not has_exact_code or sim_score < (SIMILARITY_THRESHOLD * 0.5): continue 
                    else:
                        if (has_exact_code and sim_score < SIMILARITY_THRESHOLD * 0.5) or (not has_exact_code and sim_score < SIMILARITY_THRESHOLD): continue

                    naver_api_pid = str(item.get("productId", ""))
                    if naver_api_pid in seen_product_ids: continue
                    seen_product_ids.add(naver_api_pid)

                    pooled_items.append({
                        "title": clean_title, "price": naver_price, "mallName": item.get("mallName", ""),
                        "link": item.get("link", ""), "image": item.get("image", ""), "similarity_score": round(sim_score, 2)
                    })
                    valid_count += 1
                time.sleep(0.1)

            # 최저가 정렬 및 몰 중복 제거
            pooled_items.sort(key=lambda x: x["price"])
            final_top_5 = []
            seen_malls = set() 
            for item in pooled_items:
                if item["mallName"] not in seen_malls:
                    seen_malls.add(item["mallName"])
                    final_top_5.append(item)
                if len(final_top_5) >= 5: break

            # 결과 저장
            if final_top_5:
                try:
                    cur.execute("DELETE FROM naver_prices WHERE product_id = %s", (product_id,))
                    for idx, item in enumerate(final_top_5, start=1):
                        cur.execute("""
                            INSERT INTO naver_prices
                            (product_id, brand, model_code, original_name, original_price, 
                             rank, naver_title, naver_price, mall_name, mall_url, image_url, similarity_score, 
                             create_dt, update_dt)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
                        """, (product_id, brand_en, model_code, prod_name, original_price, 
                              idx, item["title"], item["price"], item["mallName"], item["link"], item["image"], item["similarity_score"]))
                    conn.commit()
                    print(f"  ✅ 완료: {len(final_top_5)}건 저장 (최저가 {final_top_5[0]['price']:,}원)")
                except Exception as e:
                    conn.rollback()
                    print(f"  ❌ DB 저장 실패: {e}")
            else:
                print(f"  ⚠️ 결과 없음 (필터링됨)")
            
            time.sleep(random.uniform(1.0, 2.0))

        print("\n🎉 모든 누락 데이터 보완 작업 완료")

    except Exception as e:
        print(f"❌ 시스템 오류 발생: {e}")
    finally:
        if 'cur' in locals(): cur.close()
        if 'conn' in locals(): conn.close()

if __name__ == "__main__":
    main()
