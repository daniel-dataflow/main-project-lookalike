import os
import asyncio
import re
import hashlib
import json
from datetime import datetime
from playwright.async_api import async_playwright
from hdfs import InsecureClient

# --- [1. 설정 정보] ---
BRAND_NAME = "zara" 
LOCAL_ROOT_DIR = f"./{BRAND_NAME}_raw_data"
os.makedirs(LOCAL_ROOT_DIR, exist_ok=True)

# 하둡 설정
HDFS_URL = "http://namenode-main:9870" 
HDFS_USER = "root"
hdfs_client = InsecureClient(HDFS_URL, user=HDFS_USER)

TARGET_MAP = {
    "Men": {
        "Outer": [
            "https://www.zara.com/kr/ko/man-outerwear-l715.html?v1=2606109",
            "https://www.zara.com/kr/ko/man-jackets-l640.html?v1=2536906"
        ]
    }
}

# --- [2. HDFS 저장 함수] ---
async def save_to_hdfs_library(local_path, today_date, filename):
    """Spark 배치가 읽을 수 있도록 지정된 경로에 JSON 업로드"""
    hdfs_full_path = f"/raw/{BRAND_NAME}/{today_date}/{filename}"
    try:
        hdfs_client.upload(hdfs_full_path, local_path, overwrite=True)
        return True
    except Exception as e:
        print(f"❌ [HDFS] 업로드 실패: {e}")
        return False

# --- [3. 상세 페이지 수집 함수] ---
async def process_product(url, gender, category_name, context, sem):
    async with sem:
        p_page = await context.new_page()
        try:
            # 자라 보안 회피를 위한 랜덤 대기
            await asyncio.sleep(2) 
            
            # 페이지 이동 (타임아웃 60초, 네트워크 유휴 상태까지 대기)
            await p_page.goto(url, timeout=60000, wait_until="commit") 
            await asyncio.sleep(3) # 페이지 로딩 완료를 위한 추가 대기
            
            # 상품 ID 추출
            product_id = url.split("-p")[-1].split(".html")[0]
            product_id = re.sub(r'[^0-9]', '', product_id)
            
            # 상품 정보 추출
            try:
                product_name = await p_page.inner_text('h1.product-detail-info__header-name', timeout=5000)
            except:
                product_name = "Unknown Product"
                
            try:
                price_text = await p_page.inner_text('.money-amount__main', timeout=5000)
                price = re.sub(r'[^0-9]', '', price_text)
            except:
                price = "0"
            
            # 이미지 추출 (첫 번째 메인 이미지 위주)
            img_elements = await p_page.query_selector_all('picture img')
            images = []
            for img in img_elements:
                src = await img.get_attribute('src')
                if src and "https" in src:
                    images.append(src)

            # JSON 데이터 구성
            product_data = {
                "goodsNo": product_id,
                "goodsNm": product_name.strip(),
                "price": price,
                "gender": gender,
                "category": category_name,
                "goodsImages": images,
                "url": url,
                "brand": BRAND_NAME.upper()
            }

            today_date = datetime.now().strftime('%Y%m%d')
            time_now = datetime.now().strftime('%H%M%S')
            
            # 로컬 저장
            save_dir = os.path.join(LOCAL_ROOT_DIR, today_date)
            os.makedirs(save_dir, exist_ok=True)
            
            filename = f"{BRAND_NAME}_{gender}_{category_name}_{product_id}_{time_now}.json"
            local_path = os.path.join(save_dir, filename)

            with open(local_path, "w", encoding="utf-8") as f:
                json.dump(product_data, f, ensure_ascii=False, indent=4)

            # HDFS 업로드 호출 (이제 상단에 정의되어 에러가 나지 않음)
            if await save_to_hdfs_library(local_path, today_date, filename):
                if os.path.exists(local_path):
                    os.remove(local_path)
                    
        except Exception as e:
            print(f"⚠️ 상세페이지 실패 ({url}): {e}")
        finally:
            await p_page.close()

# --- [4. 카테고리 순회 함수] ---
async def crawl_category(gender, category_name, target_url, context):
    print(f"\n>>> 🎯 [{gender}-{category_name}] 목록 수집: {target_url}")
    page = await context.new_page()
    product_links = set()

    try:
        await page.goto(target_url, timeout=90000, wait_until="domcontentloaded")
        
        # 무한 스크롤 처리
        for _ in range(5): 
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)
        
        # 상품 링크 추출 및 필터링
        hrefs = await page.evaluate("""() => 
            Array.from(document.querySelectorAll('a[href*="-p"][href*=".html"]'))
            .map(a => a.href)
            .filter(href => {
                // 수집 제외 키워드 (Blacklist)
                const blackList = ['perfumes', 'beauty', 'living-room', 'pets', 'kids', 'help', 'polos', 'special-prices'];
                return !blackList.some(word => href.toLowerCase().includes(word));
            })
        """)
        
        for h in hrefs: 
            product_links.add(h.split('?')[0])
        
        print(f"🔗 필터링 후 유효 상품 수: {len(product_links)}개")
        await page.close()
    except Exception as e:
        print(f"❌ 목록 수집 오류: {e}")
        await page.close()
        return

    # 동시 실행 제한 (자라는 2개가 가장 안정적임)
    sem = asyncio.Semaphore(2) 
    if product_links:
        print(f">>> 상세 수집 시작 (진행 중...)")
        tasks = [process_product(u, gender, category_name, context, sem) for u in list(product_links)]
        await asyncio.gather(*tasks)
    
    print(f">>> [{gender}-{category_name}] 카테고리 완료.")

# --- [5. 실행 메인 함수] ---
async def run():
    print("--- [START] ZARA 크롤러 (안정화 버전) ---")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="ko-KR"
        )
        # 웹드라이버 감지 방지
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        for gender, categories in TARGET_MAP.items():
            for category, urls in categories.items():
                for url in urls:
                    await crawl_category(gender, category, url, context)
        
        await browser.close()
    print("--- [END] ZARA 수집 종료 ---")

if __name__ == "__main__":
    asyncio.run(run())