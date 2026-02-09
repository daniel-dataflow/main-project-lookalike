import os
import asyncio
import subprocess
import re
from datetime import datetime
from urllib.parse import urljoin
from playwright.async_api import async_playwright

BRAND_NAME = "musinsa" 
HDFS_BASE_PATH = f"/raw/{BRAND_NAME}"
LOCAL_ROOT_DIR = f"./{BRAND_NAME}_raw_data"
# 수집 대상
TARGET_MAP = {
    "Men": {
        "Outer": [
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=002003&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=002002&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=002017&gf=A", 
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=002006&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=002007&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=002009&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=002024&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=002008&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=002020&gf=A"
        ],
        "Top": [
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=001006&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=001004&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=001005&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=001010&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=001001&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=001002&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=001011&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=001003&gf=A"
        ],
        "Bottom": [
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=003002&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=003004&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=003008&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=003007&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=003009&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=003006&gf=A"
        ]
    },
    "Women": {
        "Outer": [
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=002003&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=002002&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=002017&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=002014&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=002007&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=002009&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=002024&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=002008&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=002020&gf=A"
        ],
        "Top": [
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=001006&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=001004&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=001005&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=001010&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=001001&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=001002&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=001011&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=001008&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=001003&gf=A"
        ],
        "Bottom": [
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=003002&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=003004&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=003008&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=003007&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=003005&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=003009&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=003006&gf=A"
        ]
    }
}

async def save_to_hdfs_via_docker(local_path, today_date, filename):
    """
    최종 경로: /raw/zara/20260209/html/파일명.html
    """
    # 구조: /raw/{브랜드}/{날짜}/html
    hdfs_full_path = f"{HDFS_BASE_PATH}/{today_date}/html"
    
    try:
        # 1. HDFS 디렉토리 생성
        subprocess.run(f"docker exec fashion-namenode hdfs dfs -mkdir -p {hdfs_full_path}", shell=True, check=True, stdout=subprocess.DEVNULL)
        # 2. 로컬 -> Docker 컨테이너 복사
        subprocess.run(f"docker cp {local_path} fashion-namenode:/tmp/{filename}", shell=True, check=True, stdout=subprocess.DEVNULL)
        # 3. Docker -> HDFS 업로드
        subprocess.run(f"docker exec fashion-namenode hdfs dfs -put -f /tmp/{filename} {hdfs_full_path}/{filename}", shell=True, check=True, stdout=subprocess.DEVNULL)
        # 4. Docker 임시 파일 삭제
        subprocess.run(f"docker exec fashion-namenode rm /tmp/{filename}", shell=True, stdout=subprocess.DEVNULL)
        return True
    except Exception as e:
        print(f"   ❌ [HDFS] 업로드 실패 ({filename}): {e}")
        return False

# 크롤러
async def save_to_hdfs_via_docker(local_path, today_date, filename):
    hdfs_full_path = f"{HDFS_BASE_PATH}/{today_date}/html"
    try:
        subprocess.run(f"docker exec fashion-namenode hdfs dfs -mkdir -p {hdfs_full_path}", shell=True, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(f"docker cp {local_path} fashion-namenode:/tmp/{filename}", shell=True, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(f"docker exec fashion-namenode hdfs dfs -put -f /tmp/{filename} {hdfs_full_path}/{filename}", shell=True, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(f"docker exec fashion-namenode rm /tmp/{filename}", shell=True, stdout=subprocess.DEVNULL)
        return True
    except Exception as e:
        print(f"   ❌ [HDFS] 업로드 실패: {e}")
        return False

async def crawl_category(gender, category_name, target_url, context):
    print(f"\n>>> 🎯 [{gender}-{category_name}] 목록 수집: {target_url}")
    page = await context.new_page()
    product_urls = set()

    try:
        await page.goto(target_url, timeout=60000)
        await page.wait_for_load_state("networkidle")
        
        # 무한 스크롤 및 링크 수집
        for _ in range(15):
            await page.evaluate("window.scrollBy(0, 1500)")
            await asyncio.sleep(0.5)
            hrefs = await page.evaluate("() => Array.from(document.querySelectorAll(\"a[href*='/products/'], a[href*='/goods/']\")).map(a => a.href)")
            for h in hrefs:
                if re.search(r'/(?:products|goods)/\d+', h):
                    product_urls.add(h.split("?")[0])

        await page.close()
    except:
        await page.close()
        return

    sem = asyncio.Semaphore(5)
    async def process_product(url):
        async with sem:
            p_page = await context.new_page()
            try:
                await p_page.goto(url, timeout=60000, wait_until="domcontentloaded")
                product_id = re.findall(r'/(?:goods|products)/(\d+)', url)[0]
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

    await asyncio.gather(*[process_product(u) for u in list(product_urls)])
    print(f">>> [{category_name}] HTML 수집 완료.")

async def run():
    print("--- [START] 무신사 HTML 수집 전용 크롤러 ---")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={'width': 1920, 'height': 1080}
        )
        
        for gender, categories in TARGET_MAP.items():
            for category, urls in categories.items():
                for url in urls:
                    await crawl_category(gender, category, url, context)
                    await asyncio.sleep(1)

        await browser.close()
    print(f"--- [END] 모든 작업 완료. 데이터 경로: {LOCAL_TEMP_DIR}")

if __name__ == "__main__":
    asyncio.run(run())