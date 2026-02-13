import os
import asyncio
import subprocess
import re
from datetime import datetime
from playwright.async_api import async_playwright

# 브랜드 및 경로 정보
BRAND_NAME = "topten" 
HDFS_BASE_PATH = f"/raw/{BRAND_NAME}"
LOCAL_ROOT_DIR = f"./{BRAND_NAME}_raw_data"

# 수집 대상
TARGET_MAP = {
    "Men": {
        "Outer": [
            "https://topten10.goodwearmall.com/display/category/list?dspCtgryNo=SSMA42A06",
            "https://topten10.goodwearmall.com/display/category/list?dspCtgryNo=SSMA42A03"
        ],
        "Top": [
            "https://topten10.goodwearmall.com/display/category/list?dspCtgryNo=SSMA42A02",
            "https://topten10.goodwearmall.com/display/category/list?dspCtgryNo=SSMA42A01",
            "https://topten10.goodwearmall.com/display/category/list?dspCtgryNo=SSMA42A04"
        ],
        "Bottom": [
            "https://topten10.goodwearmall.com/display/category/list?dspCtgryNo=SSMA42A07",
            "https://topten10.goodwearmall.com/display/category/list?dspCtgryNo=SSMA42A21"
        ]
    },
    "Women": {
        "Outer": [
            "https://topten10.goodwearmall.com/display/category/list?dspCtgryNo=SSMA41A04A01",
            "https://topten10.goodwearmall.com/display/category/list?dspCtgryNo=SSMA41A02"
        ],
        "Top": [
            "https://topten10.goodwearmall.com/display/category/list?dspCtgryNo=SSMA41A01",
            "https://topten10.goodwearmall.com/display/category/list?dspCtgryNo=SSMA41A03"
        ],
        "Bottom": [
            "https://topten10.goodwearmall.com/display/category/list?dspCtgryNo=SSMA41A06"
        ]
    }
}

os.makedirs(LOCAL_ROOT_DIR, exist_ok=True)

async def save_to_hdfs_via_docker(local_path, today_date, filename):
    # 최종 경로: /raw/topten/20260209/html/
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

async def crawl_category(gender, category_name, target_url, context):
    print(f"\n>>> 🎯 [{gender}-{category_name}] 목록 수집 시작: {target_url}")
    page = await context.new_page()
    product_ids = set()

    try:
        await page.goto(target_url, timeout=60000, wait_until="domcontentloaded")
        page_num, MAX_PAGES = 1, 10 

        while page_num <= MAX_PAGES:
            print(f"   [Page {page_num}] 스캔 중...", end="\r")
            for _ in range(2):
                await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
                await asyncio.sleep(0.5)
            
            new_ids = await page.evaluate("""() => {
                const ids = [];
                document.querySelectorAll('a.tile-link').forEach(a => {
                    const onclick = a.getAttribute('onclick');
                    if (onclick && onclick.includes('goGodDetail')) {
                        const match = onclick.match(/goGodDetail\\('([^']+)'/);
                        if (match) ids.push(match[1]);
                    }
                });
                return ids;
            }""")

            for pid in new_ids: product_ids.add(pid)

            next_btn = page.locator("div.pagination button.next")
            if await next_btn.count() > 0 and not await next_btn.is_disabled():
                await next_btn.click()
                await asyncio.sleep(1.5)
                page_num += 1
            else:
                break
        await page.close()
    except Exception as e:
        print(f"❌ 목록 에러: {e}")
        await page.close()
        return

    target_ids = list(product_ids)
    print(f"\n>>> [{gender}-{category_name}] {len(target_ids)}개 상세 HTML 저장 시작...")
    sem = asyncio.Semaphore(5)

    async def process_product(god_no):
        url = f"https://topten10.goodwearmall.com/product/{god_no}/detail"
        async with sem:
            p_page = await context.new_page()
            try:
                await p_page.goto(url, timeout=60000, wait_until="domcontentloaded")
                
                today_date = datetime.now().strftime('%Y%m%d')
                time_now = datetime.now().strftime('%H%M')

                # 로컬 임시 폴더 생성
                DATE_DIR = os.path.join(LOCAL_ROOT_DIR, today_date)
                os.makedirs(DATE_DIR, exist_ok=True)

                # 파일명 및 경로 설정
                filename_html = f"{BRAND_NAME}_{gender}_{category_name}_{god_no}_{time_now}.html"
                local_html_path = os.path.join(DATE_DIR, filename_html)

                # 1. HTML 내용 추출 및 로컬 임시 저장
                content = await p_page.content()
                with open(local_html_path, "w", encoding="utf-8") as f:
                    f.write(content)

                # 2. HDFS 업로드 실행
                success = await save_to_hdfs_via_docker(local_html_path, today_date, filename_html)

                # 3. 업로드 성공 시 로컬 파일 즉시 삭제 (용량 관리)
                if success and os.path.exists(local_html_path):
                    os.remove(local_html_path)

            except Exception as e:
                pass
            finally:
                await p_page.close()

    await asyncio.gather(*[process_product(pid) for pid in target_ids])
    print(f">>> [{category_name}] 완료.")

async def run():
    print(f"--- [START] {BRAND_NAME.upper()} HTML 수집 프로세스 ---")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        for gender, categories in TARGET_MAP.items():
            for category, urls in categories.items():
                if isinstance(urls, str): urls = [urls]
                for url in urls:
                    await crawl_category(gender, category, url, context)
        await browser.close()
    print("--- [END] 모든 작업 완료 및 로컬 정리 종료 ---")

if __name__ == "__main__":
    asyncio.run(run())