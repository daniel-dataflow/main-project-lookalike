import os
import time
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

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
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
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
    print("🚀 네이버 쇼핑 API -> naver_prices 업데이트 배치를 시작합니다. (상위 5개 최저가)")
    conn = None
    try:
        conn = get_db_connection()
        conn.autocommit = False
        
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 1. products 테이블에서 처리 대상 조회 (또는 전체)
            cur.execute("""
                SELECT product_id, prod_name, brand_name
                FROM products
            """)
            products = cur.fetchall()
            
            print(f"총 {len(products)}개의 상품 정보를 조회했습니다.")
            
            updated_count = 0
            
            for prod in products:
                pid = prod['product_id']
                # 브랜드명과 상품명을 조합하여 검색 정확도 상향
                search_query = f"{prod['brand_name']} {prod['prod_name']}".strip()
                
                # 네이버 API로 넉넉하게 검색 (공식몰 필터링을 위해 20개 요청)
                items = search_naver_shopping(search_query, display=20)
                
                if items:
                    filtered_items = []
                    for item in items:
                        mall_name = item.get("mallName", "알 수 없음")
                        
                        # 공식몰 제외 필터링
                        is_official = False
                        brand_upper = prod['brand_name'].upper() if prod['brand_name'] else ""
                        mall_upper = mall_name.upper()
                        
                        if brand_upper == "8SECONDS":
                            if "에잇세컨즈" in mall_name or "8SECONDS" in mall_upper or "SSF" in mall_upper:
                                is_official = True
                        elif brand_upper == "TOPTEN10":
                            if "탑텐" in mall_name or "TOPTEN" in mall_upper:
                                is_official = True
                                
                        if not is_official:
                            filtered_items.append(item)
                    
                    if filtered_items:
                        # 가격 낮은순 정렬
                        items_sorted = sorted(filtered_items, key=lambda x: int(x.get("lprice", 0)))
                        
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
                    else:
                        print(f"⚠️ 유효 검색 결과 없음 (전부 공식몰): {search_query}")
                else:
                    print(f"⚠️ 검색 결과 없음: {search_query}")
                
                # 네이버 API Rate Limit(초당 10회 권장) 고려하여 휴식
                time.sleep(0.1)

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
