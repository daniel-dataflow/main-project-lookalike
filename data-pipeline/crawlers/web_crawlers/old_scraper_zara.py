import os
import asyncio
import subprocess
import re
import hashlib
from datetime import datetime
from urllib.parse import urljoin
from playwright.async_api import async_playwright

# 수집 대상
TARGET_MAP = {
    "Men": {
        "Outer": [
            "https://www.zara.com/kr/ko/man-outerwear-l715.html?v1=2606109",
            "https://www.zara.com/kr/ko/man-jackets-l640.html?v1=2536906"
        ],
        "Top": [
            "https://www.zara.com/kr/ko/man-tshirts-l855.html?v1=2432042",
            "https://www.zara.com/kr/ko/man-sweatshirts-l821.html?v1=2432232"
        ],
        "Bottom": [
            "https://www.zara.com/kr/ko/man-trousers-l838.html?v1=2432096",
            "https://www.zara.com/kr/ko/man-jeans-l659.html?v1=2432131"
        ]
    },
    "Women": {
        "Outer": [
            "https://www.zara.com/kr/ko/woman-jackets-l1114.html?v1=2417772",
            "https://www.zara.com/kr/ko/woman-outerwear-l1184.html?v1=2419032"
        ],
        "Top": [
            "https://www.zara.com/kr/ko/woman-shirts-l1217.html?v1=2420369",
            "https://www.zara.com/kr/ko/woman-tshirts-l1362.html?v1=2420417"
        ],
        "Bottom": [
            "https://www.zara.com/kr/ko/woman-trousers-l1335.html?v1=2420795"
        ]
    }
}

HDFS_PATH = "/datalake/raw/zara"
LOCAL_ROOT_DIR = "./zara_raw_data"

# 폴더 생성
os.makedirs(LOCAL_ROOT_DIR, exist_ok=True)

BRAND_NAME = "zara" 
HDFS_BASE_PATH = f"/raw/{BRAND_NAME}"
LOCAL_ROOT_DIR = f"./{BRAND_NAME}_raw_data"

async def save_to_hdfs_via_docker(local_path, today_date, filename):
    """
    최종 경로: /raw/zara/20260209/html/파일명.html
    """
    # 구조: /raw/{브랜드}/{날짜}/html
    hdfs_full_path = f"{HDFS_BASE_PATH}/{today_date}/html"
    
    try:
        # 1. HDFS 디렉토리 생성
        subprocess.run(f"docker exec namenode-main hdfs dfs -mkdir -p {hdfs_full_path}", shell=True, check=True, stdout=subprocess.DEVNULL)
        # 2. 로컬 -> Docker 컨테이너 복사
        subprocess.run(f"docker cp {local_path} namenode-main:/tmp/{filename}", shell=True, check=True, stdout=subprocess.DEVNULL)
        # 3. Docker -> HDFS 업로드
        subprocess.run(f"docker exec namenode-main hdfs dfs -put -f /tmp/{filename} {hdfs_full_path}/{filename}", shell=True, check=True, stdout=subprocess.DEVNULL)
        # 4. Docker 임시 파일 삭제
        subprocess.run(f"docker exec namenode-main rm /tmp/{filename}", shell=True, stdout=subprocess.DEVNULL)
        return True
    except Exception as e:
        print(f"   ❌ [HDFS] 업로드 실패 ({filename}): {e}")
        return False

# 크롤러
BRAND_NAME = "zara" 
HDFS_BASE_PATH = f"/raw/{BRAND_NAME}"
LOCAL_ROOT_DIR = f"./{BRAND_NAME}_raw_data"

async def crawl_category(gender, category_name, target_url, context):
    print(f"\n>>> 🎯 [{gender}-{category_name}] 목록 수집: {target_url}")
    page = await context.new_page()
    product_links = set()

    try:
        #####
        # wait_until을 "networkidle"로 변경 (네트워크 통신이 잦아들 때까지 대기)
        await page.goto(target_url, timeout=90000, wait_until="networkidle")
        
        # 스크롤 전 잠시 대기
        await asyncio.sleep(2)
        #await page.goto(target_url, timeout=90000)

        for _ in range(5): 
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1.5)
        
        # 선택자가 나타날 때까지 명시적으로 기다림
        await page.wait_for_selector('a[href*="-p"]', timeout=10000)
        
        hrefs = await page.evaluate("() => Array.from(document.querySelectorAll('a[href*=\"-p\"][href*=\".html\"]')).map(a => a.href)")
        # 링크가 잘 긁혔는지 로그 추가
        print(f"    🔎 발견된 상품 링크: {len(hrefs)}개")
        #####


        for h in hrefs: product_links.add(h.split('?')[0])
        await page.close()
    except:
        await page.close()
        return

    sem = asyncio.Semaphore(5)
    async def process_product(url):
        async with sem:
            p_page = await context.new_page()
            try:
                # 자라는 탐지 방지를 위해 약간 더 보수적으로 로드
                await p_page.goto(url, timeout=60000, wait_until="domcontentloaded")
                
                try:
                    product_id = url.split("-p")[-1].split(".html")[0]
                    product_id = re.sub(r'[^0-9]', '', product_id)
                except: product_id = "UNKNOWN"

                today_date = datetime.now().strftime('%Y%m%d')
                time_now = datetime.now().strftime('%H%M')
                DATE_DIR = os.path.join(LOCAL_ROOT_DIR, today_date)
                os.makedirs(DATE_DIR, exist_ok=True)

                filename_html = f"{BRAND_NAME}_{gender}_{category_name}_{product_id}_{time_now}.html"
                local_html_path = os.path.join(DATE_DIR, filename_html)

                content = await p_page.content()
                with open(local_html_path, "w", encoding="utf-8") as f:
                    f.write(content)

                success = await save_to_hdfs_via_docker(local_html_path, today_date, filename_html)
                if success and os.path.exists(local_html_path):
                    os.remove(local_html_path)
            except: pass
            finally: await p_page.close()

    await asyncio.gather(*[process_product(u) for u in list(product_links)])
    ###
    target_links = list(product_links)[:20] 
    if target_links:
        print(f">>> 상세 페이지 HTML 수집 시작 ({len(target_links)}개)...")
        for u in target_links:
            await process_product(u)
            await asyncio.sleep(0.5) # 개별 상품 간 짧은 휴식
    ###
    print(f">>> [{gender}-{category_name}] 카테고리 처리 완료.")


async def run():
    print("--- [START] ZARA HTML 전용 크롤러 ---")
    
    async with async_playwright() as p:
        #browser = await p.chromium.launch(headless=False, args=["--no-sandbox", "--disable-dev-shm-usage"])
        browser = await p.chromium.launch(
            headless=True,  # 서버 환경을 위해 True 유지
            args=[
                "--no-sandbox", 
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled" # 봇 감지 우회 핵심 옵션
            ]
        )

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        # 웹드라이버 속성 제거
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        for gender, categories in TARGET_MAP.items():
            for category, urls in categories.items():
                if isinstance(urls, str): urls = [urls]
                for url in urls:
                    await crawl_category(gender, category, url, context)

        await browser.close()
    print("--- [END] 모든 브랜드 데이터 수집 프로세스 종료 ---")

if __name__ == "__main__":
    asyncio.run(run())