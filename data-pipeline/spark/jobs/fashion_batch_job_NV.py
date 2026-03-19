import os
import requests
import psycopg2
import time
import random
import datetime
import re
import difflib
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 네이버 API 환경변수 및 세션 설정
dotenv_path = os.path.join(os.path.dirname(__file__), '../../../.env')
load_dotenv(dotenv_path)

NAVER_CLIENT_ID = os.getenv("X_NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("X_NAVER_CLIENT_SECRET")
NAVER_URL = "https://openapi.naver.com/v1/search/shop.json"

if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
    raise ValueError("NAVER API 환경변수가 설정되지 않았습니다.")

session = requests.Session()
retry = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
session.mount('https://', HTTPAdapter(max_retries=retry))

BRAND_KO_MAP = {
    "zara": "자라",
    "8seconds": "에잇세컨즈",
    "musinsa": "무신사",
    "uniqlo": "유니클로",
    "topten": "탑텐"
}

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
        "exclude": "used"  # 중고 원천 차단
    }
    
    try:
        response = session.get(NAVER_URL, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        return response.json().get("items", [])
    except Exception as e:
        print(f" 네이버 쇼핑 API 호출 실패 (query: {query}): {e}")
        return []

# DB 연결 및 배치 작업 시작
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

    print(" DB 연결 성공")

    # 테이블 스키마 자동 생성 및 업데이트 로직
    print(" 테이블 구조 자동 점검 및 업데이트 중...")
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS naver_price (
            product_id VARCHAR(50),
            brand VARCHAR(100),
            model_code VARCHAR(100),
            original_name VARCHAR(255),
            original_price INTEGER,
            rank INTEGER,
            naver_title VARCHAR(255),
            naver_price INTEGER,
            mall_name VARCHAR(100),
            mall_url TEXT,
            image_url TEXT,
            similarity_score NUMERIC(5, 2),
            create_dt TIMESTAMP,
            update_dt TIMESTAMP
        );
    """)
    conn.commit()

    try:
        cur.execute("ALTER TABLE naver_prices RENAME COLUMN price TO naver_prices;")
        conn.commit()
    except psycopg2.Error:
        conn.rollback()

    alter_queries = [
        "ALTER TABLE naver_prices ADD COLUMN IF NOT EXISTS brand VARCHAR(100);",
        "ALTER TABLE naver_prices ADD COLUMN IF NOT EXISTS model_code VARCHAR(100);",
        "ALTER TABLE naver_prices ADD COLUMN IF NOT EXISTS original_name VARCHAR(255);",
        "ALTER TABLE naver_prices ADD COLUMN IF NOT EXISTS original_price INTEGER;",
        "ALTER TABLE naver_prices ADD COLUMN IF NOT EXISTS naver_title VARCHAR(255);",
        "ALTER TABLE naver_prices ADD COLUMN IF NOT EXISTS similarity_score NUMERIC(5, 2);"
    ]
    
    for q in alter_queries:
        try:
            cur.execute(q)
            conn.commit()
        except psycopg2.Error:
            conn.rollback()

    print("테이블 구조 세팅 완료!")
    
    cur.execute("""
        SELECT product_id, model_code, prod_name, brand_name, base_price
        FROM products
        WHERE create_dt::date = CURRENT_DATE
    """)

    products = cur.fetchall()
    print(f" 오늘 등록 상품 수: {len(products)}")

    # 로그 파일 경로 설정
    batch_run_time = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    log_dir = os.path.join(os.path.dirname(__file__), '../../../logs/spark/naver_api')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"{batch_run_time}_missing_items.log")

    SIMILARITY_THRESHOLD = 0.35

    for product_id, model_code, prod_name, brand_name, base_price in products:
        # 데이터 전처리
        brand_en = str(brand_name).lower() if brand_name else "unknown"
        brand_ko = BRAND_KO_MAP.get(brand_en, brand_en)
        prod_name = str(prod_name).strip() if prod_name else ""
        model_code = str(model_code).strip() if model_code else ""
        
        raw_price = str(base_price) if base_price else "0"
        clean_price_str = re.sub(r'[^0-9]', '', raw_price) 
        original_price = int(clean_price_str) if clean_price_str else 0

        if not brand_name or (not prod_name and not model_code):
            print(f"⚠ {product_id} 검색어 부족 → 패스")
            continue

        print(f"\n▶ 대상: {brand_en.upper()} | 코드: {model_code} | 이름: {prod_name}")

        # 다각도 스마트 검색 풀링
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
                
            items = search_naver_shopping_api(query, display=100)
            
            if items:
                items_sorted_by_price = sorted(items, key=lambda x: int(x.get("lprice", 0)))
                valid_items_count = 0
                
                for item in items_sorted_by_price:
                    if valid_items_count >= 5: break
                        
                    naver_price = int(item.get("lprice", 0))
                    clean_title = item.get("title", "").replace("<b>", "").replace("</b>", "")

                    # 스팸 키워드 제거
                    spam_keywords = ["구매대행", "구제", "중고", "병행수입"]
                    if any(keyword in clean_title for keyword in spam_keywords): continue

                    # 가격 방어
                    if original_price > 0:
                        if naver_price <= (original_price * 0.3) or naver_price >= (original_price * 2.5):
                            continue
                            
                    # 유사도 체크
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

                    # similarity_score 포함
                    pooled_items.append({
                        "title": clean_title,
                        "price": naver_price,
                        "mallName": item.get("mallName", ""),
                        "link": item.get("link", ""),
                        "image": item.get("image", ""),
                        "similarity_score": round(sim_score, 2)
                    })
                    valid_items_count += 1
            
            time.sleep(0.2) 

        # 최저가 정렬 및 쇼핑몰 도배 방지
        pooled_items.sort(key=lambda x: x["price"])
        
        final_top_5 = []
        seen_malls = set() 

        for item in pooled_items:
            if item["mallName"] not in seen_malls:
                seen_malls.add(item["mallName"])
                final_top_5.append(item)
            if len(final_top_5) >= 5:
                break

        # 로깅 기록
        if not final_top_5:
            print(f" 검색 결과 없음 (또는 필터 탈락)")
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [검색실패(0건)] BRAND: {brand_name or 'N/A'} | CODE: {model_code or 'N/A'} | NAME: {prod_name or 'N/A'}\n")
            continue

        saved_count = len(final_top_5)
        if saved_count < 5:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [최저가부족({saved_count}/5)] BRAND: {brand_name or 'N/A'} | CODE: {model_code or 'N/A'} | NAME: {prod_name or 'N/A'}\n")

        try:
            # 기존 데이터 삭제
            cur.execute("""
                DELETE FROM naver_prices
                WHERE product_id = %s
            """, (product_id,))

            # 상위 데이터 DB 저장 (모든 메타데이터 포함)
            for idx, item in enumerate(final_top_5, start=1):
                cur.execute("""
                    INSERT INTO naver_prices
                    (product_id, brand, model_code, original_name, original_price, 
                     rank, naver_title, naver_price, mall_name, mall_url, image_url, similarity_score, 
                     create_dt, update_dt)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
                """, (
                    product_id,
                    brand_en,                 # 브랜드
                    model_code,               # 모델 코드
                    prod_name,                # 오리지널 상품명
                    original_price,           # 오리지널 가격
                    idx,                      # 순위
                    item["title"],            # 네이버 상품명
                    item["price"],            # 네이버 가격
                    item["mallName"],         # 쇼핑몰명
                    item["link"],             # 쇼핑몰 URL
                    item["image"],            # 이미지 URL
                    item["similarity_score"]  # 유사도 점수
                ))

            conn.commit()
            print(f" 완료: {saved_count}건 저장 / 최저가({final_top_5[0]['price']:,}원)")

        except Exception as e:
            conn.rollback()
            print(f" DB 저장 실패 ({product_id}) → {e}")
            continue

        time.sleep(random.uniform(1.5, 3.0))

    print("\n 전체 배치 작업 완료")

except Exception as e:
    print(" 시스템 오류 발생:", e)

finally:
    if 'cur' in locals():
        cur.close()
    if 'conn' in locals():
        conn.close()
    print(" DB 연결 종료")