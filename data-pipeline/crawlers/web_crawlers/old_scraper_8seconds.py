import os
import asyncio
import subprocess
from datetime import datetime
from playwright.async_api import async_playwright

# 수집 대상
TARGET_MAP = {
    "Men": {
        "Outer": [
            "https://www.ssfshop.com/8seconds/Coats/list?dspCtgryNo=SFMA42A05A02&brandShopNo=BDMA07A01&brndShopId=8SBSS",
            "https://www.ssfshop.com/8seconds/LeatherJacket/list?dspCtgryNo=SFMA42A05A06&brandShopNo=BDMA07A01&brndShopId=8SBSS",
            "https://www.ssfshop.com/8seconds/DenimJacket/list?dspCtgryNo=SFMA42A05A07&brandShopNo=BDMA07A01&brndShopId=8SBSS",
            "https://www.ssfshop.com/8seconds/Jackets/list?dspCtgryNo=SFMA42A19A01&brandShopNo=BDMA07A01&brndShopId=8SBSS",
            "https://www.ssfshop.com/8seconds/Cardigans/list?dspCtgryNo=SFMA42A03A02&brandShopNo=BDMA07A01&brndShopId=8SBSS"
        ],
        "Top": [
            "https://www.ssfshop.com/8seconds/T-Shirts/list?dspCtgryNo=SFMA42A01&brandShopNo=BDMA07A01&brndShopId=8SBSS",
            "https://www.ssfshop.com/8seconds/Pullovers/list?dspCtgryNo=SFMA42A03A01&brandShopNo=BDMA07A01&brndShopId=8SBSS",
            "https://www.ssfshop.com/8seconds/Shirts/list?dspCtgryNo=SFMA42A02&brandShopNo=BDMA07A01&brndShopId=8SBSS"
        ],
        "Bottom": [
            "https://www.ssfshop.com/8seconds/Pants-Trousers/list?dspCtgryNo=SFMA42A04&brandShopNo=BDMA07A01&brndShopId=8SBSS"
        ]
    },
    "Women": {
        "Outer": [
            "https://www.ssfshop.com/8seconds/Jackets/list?dspCtgryNo=SFMA41A21A01&brandShopNo=BDMA07A01&brndShopId=8SBSS",
            "https://www.ssfshop.com/8seconds/Leather-Jackets/list?dspCtgryNo=SFMA41A21A04&brandShopNo=BDMA07A01&brndShopId=8SBSS",
            "https://www.ssfshop.com/8seconds/Coats/list?dspCtgryNo=SFMA41A07A02&brandShopNo=BDMA07A01&brndShopId=8SBSS",
            "https://www.ssfshop.com/8seconds/Cardigans/list?dspCtgryNo=SFMA41A03A02&brandShopNo=BDMA07A01&brndShopId=8SBSS"
        ],
        "Top": [
            "https://www.ssfshop.com/8seconds/Pullovers/list?dspCtgryNo=SFMA41A03A01&brandShopNo=BDMA07A01&brndShopId=8SBSS",
            "https://www.ssfshop.com/8seconds/T-shirts/list?dspCtgryNo=SFMA41A01&brandShopNo=BDMA07A01&brndShopId=8SBSS",
            "https://www.ssfshop.com/8seconds/Shirts-Blouses/list?dspCtgryNo=SFMA41A02&brandShopNo=BDMA07A01&brndShopId=8SBSS"
        ],
        "Bottom": [
            "https://www.ssfshop.com/8seconds/Pants-Trousers/list?dspCtgryNo=SFMA41A04&brandShopNo=BDMA07A01&brndShopId=8SBSS"
        ]
    }
}

BRAND_NAME = "8seconds" 
HDFS_BASE_PATH = f"/raw/{BRAND_NAME}"
#LOCAL_ROOT_DIR = f"./{BRAND_NAME}_raw_data"
LOCAL_ROOT_DIR = '/home/lookalike/main-project-lookalike/data/raw/8seconds'

os.makedirs(LOCAL_ROOT_DIR, exist_ok=True)

async def save_to_hdfs_via_docker(local_path, today_date, filename):
    hdfs_full_path = f"{HDFS_BASE_PATH}/{today_date}/html"
    try:
        subprocess.run(f"docker exec namenode-main hdfs dfs -mkdir -p {hdfs_full_path}", shell=True, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(f"docker cp {local_path} namenode-main:/tmp/{filename}", shell=True, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(f"docker exec namenode-main hdfs dfs -put -f /tmp/{filename} {hdfs_full_path}/{filename}", shell=True, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(f"docker exec namenode-main rm /tmp/{filename}", shell=True, stdout=subprocess.DEVNULL)
        return True
    except Exception as e:

        print(f"   ❌ [HDFS] 업로드 실패 ({filename}): {e}")
        return False

async def crawl_category(gender, category_name, target_url, context):
    print(f"\n>>> 🎯 [{gender}-{category_name}] 목록 수집 시작: {target_url}")
    page = await context.new_page()
    product_codes = set() 

    try:
        await page.goto(target_url, timeout=60000)
        try: await page.evaluate("document.querySelectorAll('.btn_close').forEach(b => b.click())")
        except: pass

        MAX_PAGES = 6
        for page_num in range(1, MAX_PAGES + 1):
            if page_num > 1:
                try:
                    await page.click(f"a#page_{page_num}", timeout=5000)
                    await asyncio.sleep(2) 
                except: break

            for _ in range(2):
                await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
                await asyncio.sleep(0.5)

            codes = await page.evaluate("""() => Array.from(document.querySelectorAll('li.god-item')).map(item => item.getAttribute('view-godno')).filter(c => c !== null)""")
            for code in codes: product_codes.add(code)
            print(f"   [Page {page_num}] 현재 누적: {len(product_codes)}")
        await page.close()
    except Exception as e:
        await page.close()
        return

    target_list = list(product_codes)
    print(f">>> [{gender}-{category_name}] {len(target_list)}개 HTML 저장 시작...")
    sem = asyncio.Semaphore(5) 

    async def process_product(model_code):
        async with sem:
            url = f"https://www.ssfshop.com/8seconds/{model_code}/goodDetail"
            p_page = await context.new_page()
            try:
                await p_page.goto(url, timeout=60000, wait_until="domcontentloaded")
                try: await p_page.wait_for_selector(".gods-about", timeout=3000)
                except: pass

                today_date = datetime.now().strftime('%Y%m%d')
                time_now = datetime.now().strftime('%H%M')

                DATE_DIR = os.path.join(LOCAL_ROOT_DIR, today_date)
                os.makedirs(DATE_DIR, exist_ok=True)

                filename_html = f"{BRAND_NAME}_{gender}_{category_name}_{model_code}_{time_now}.html"
                local_html_path = os.path.join(DATE_DIR, filename_html)

                content = await p_page.content()
                with open(local_html_path, "w", encoding="utf-8") as f:
                    f.write(content)

                success = await save_to_hdfs_via_docker(local_html_path, today_date, filename_html)
                if success: os.remove(local_html_path)

            except Exception: pass
            finally: await p_page.close()

    tasks = [process_product(code) for code in target_list]
    await asyncio.gather(*tasks)

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        for gender, categories in TARGET_MAP.items():
            for category, urls in categories.items():
                for url in urls: await crawl_category(gender, category, url, context)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())