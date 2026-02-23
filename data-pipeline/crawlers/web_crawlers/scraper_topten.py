import os
import asyncio
import re
import json
import subprocess
from datetime import datetime
from playwright.async_api import async_playwright

# --- [설정] ---
BRAND_NAME = "topten"
TODAY_STR = datetime.now().strftime('%Y%m%d')
LOCAL_SAVE_DIR = f"data/{BRAND_NAME}/{TODAY_STR}"
HDFS_ROOT_PATH = f"/raw/{BRAND_NAME}/{TODAY_STR}"  # HDFS 저장 경로

TARGET_MAP = {
    "Men": {
        "Outer": [
            "https://topten10.goodwearmall.com/display/category/list?dspCtgryNo=SSMA42A06",
            "https://topten10.goodwearmall.com/display/category/list?dspCtgryNo=SSMA42A03"
        ]
        # "Top": [
        #     "https://topten10.goodwearmall.com/display/category/list?dspCtgryNo=SSMA42A02",
        #     "https://topten10.goodwearmall.com/display/category/list?dspCtgryNo=SSMA42A01",
        #     "https://topten10.goodwearmall.com/display/category/list?dspCtgryNo=SSMA42A04"
        # ],
        # "Bottom": [
        #     "https://topten10.goodwearmall.com/display/category/list?dspCtgryNo=SSMA42A07",
        #     "https://topten10.goodwearmall.com/display/category/list?dspCtgryNo=SSMA42A21"
        # ]
    },
    "Women": {
        "Outer": [
            "https://topten10.goodwearmall.com/display/category/list?dspCtgryNo=SSMA41A04A01",
            "https://topten10.goodwearmall.com/display/category/list?dspCtgryNo=SSMA41A02"
        ]
        # "Top": [
        #     "https://topten10.goodwearmall.com/display/category/list?dspCtgryNo=SSMA41A01",
        #     "https://topten10.goodwearmall.com/display/category/list?dspCtgryNo=SSMA41A03"
        # ],
        # "Bottom": [
        #     "https://topten10.goodwearmall.com/display/category/list?dspCtgryNo=SSMA41A06"
        # ]
    }
}

visited_products = set()
sem = asyncio.Semaphore(5)

async def extract_product_data_from_dom(page):
    try:
        await asyncio.sleep(1)
        data = await page.evaluate("""() => {
            const result = {};
            result.goodsNo = location.href.match(/\/product\/([A-Z0-9]+)\/detail/)?.[1] || "";
            result.goodsNm = document.querySelector('meta[property="og:title"]')?.content || document.title;
            result.brandName = "TOPTEN10";
            result.thumbnailImageUrl = document.querySelector('meta[property="og:image"]')?.content || "";

            let price = 0;
            const metaPrice = document.querySelector('meta[property="product:price:amount"]')?.content;
            if (metaPrice) price = parseInt(metaPrice);
            if (price === 0) {
                const priceElement = document.querySelector('.price strong, .item-price, .sale-price');
                if (priceElement) price = parseInt(priceElement.innerText.replace(/[^0-9]/g, ''));
            }
            result.price = price;

            let isSoldOut = false;
            const buyBtn = document.querySelector('.btn-buy, .btn-order, .btn-cart');
            if (buyBtn && (buyBtn.innerText.includes('품절') || buyBtn.disabled)) isSoldOut = true;
            if (price === 0) isSoldOut = true;
            result.is_sold_out = isSoldOut;

            const sizeStockInfo = [];
            document.querySelectorAll('.option-list.size button, .size-area button').forEach(btn => {
                const name = btn.innerText.trim();
                if (name && !name.includes('삭제')) {
                    const isItemSoldOut = btn.classList.contains('soldout') || btn.disabled || btn.innerText.includes('품절');
                    sizeStockInfo.push({
                        size: name.replace(/\(.*\)/, '').trim(),
                        is_sold_out: isItemSoldOut,
                        stock_qty: isItemSoldOut ? 0 : 999
                    });
                }
            });
            result.size_stock = sizeStockInfo;

            const otherColorIds = [];
            document.querySelectorAll('.tooltip-box button, .color-chip button, .option-list.color button').forEach(btn => {
                const onclick = btn.getAttribute('onclick') || "";
                const match = onclick.match(/goGodDetail\\(['"]([A-Z0-9]+)['"]/);
                if (match && match[1] && match[1] !== result.goodsNo) otherColorIds.push(match[1]);
            });
            result.other_color_ids = [...new Set(otherColorIds)];

            const images = [];
            document.querySelectorAll('img').forEach(img => {
                const src = img.getAttribute('src') || img.getAttribute('data-src');
                if (src && src.includes('goodwearmall') && !src.includes('icon') && !src.includes('logo')) {
                    images.push(src.startsWith('//') ? 'https:' + src : src);
                }
            });
            result.goodsImages = [...new Set(images)];

            const specInfo = {};
            document.querySelectorAll('table tbody tr').forEach(row => {
                const key = row.querySelector('th')?.innerText.trim();
                const val = row.querySelector('td')?.innerText.trim().replace(/\\n/g, ' ');
                if (key && val) specInfo[key] = val;
            });
            result.goodsMaterial = specInfo;

            return result;
        }""")
        
        if not data.get('goodsNm'): return None
        data['url'] = page.url
        data['scraped_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return data
    except Exception:
        return None

async def process_product(product_id, gender, category, context):
    if product_id in visited_products: return
    visited_products.add(product_id)
    new_ids = []

    async with sem:
        url = f"https://topten10.goodwearmall.com/product/{product_id}/detail"
        p_page = await context.new_page()
        try:
            await p_page.goto(url, timeout=60000, wait_until="domcontentloaded")
            product_dict = await extract_product_data_from_dom(p_page)

            if product_dict:
                if not os.path.exists(LOCAL_SAVE_DIR):
                    os.makedirs(LOCAL_SAVE_DIR)
                
                # --- [1] 먼저 로컬에 낱개 파일로 저장 ---
                filename = f"{BRAND_NAME}_{gender.lower()}_{category.lower()}_{product_id}.json"
                filepath = os.path.join(LOCAL_SAVE_DIR, filename)
                
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(product_dict, f, ensure_ascii=False, indent=4)
                
                print(f"   ✅ [로컬저장] {filename}")
                
                for oid in product_dict.get('other_color_ids', []):
                    if oid not in visited_products: 
                        new_ids.append(oid)
        except Exception as e:
            print(f"   ❌ {product_id} 에러: {str(e)[:50]}")
        finally:
            await p_page.close()

    if new_ids:
        tasks = [process_product(oid, gender, category, context) for oid in new_ids]
        await asyncio.gather(*tasks)

async def crawl_category(gender, category_name, base_url, context):
    print(f"\n>>> 🎯 카테고리 시작: [{gender}-{category_name}]")
    page = await context.new_page()
    all_product_ids = set()
    
    try:
        await page.goto(base_url, timeout=60000, wait_until="domcontentloaded")
        
        for page_num in range(1, 51):
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
                await asyncio.sleep(0.5)
            
            content = await page.content()
            matches = re.findall(r"[A-Z]{3}\d[A-Z]{2}\d{4}[A-Z0-9]+", content)
            pids = {pid for pid in matches if 10 <= len(pid) <= 15}
            
            all_product_ids.update(pids)
            print(f"   📄 {page_num}페이지: 누적 {len(all_product_ids)}개 확인")
            
            try:
                next_num = page_num + 1
                next_btn = await page.query_selector(f".pagination a:has-text('{next_num}')")
                if not next_btn:
                    next_btn = await page.query_selector(".pagination .next, .pagination .btn-next")

                if next_btn:
                    await next_btn.click(timeout=2000)
                    await page.wait_for_load_state("networkidle")
                else:
                    break
            except Exception:
                break
        
        await page.close()
        
        if all_product_ids:
            print(f"   🔎 [{category_name}] 총 {len(all_product_ids)}개 상세 수집 시작...")
            tasks = [process_product(pid, gender, category_name, context) for pid in list(all_product_ids)]
            await asyncio.gather(*tasks)
            
    except Exception as e:
        print(f"   ❌ 카테고리 목록 에러: {e}")
        if not page.is_closed(): await page.close()

async def run():
    print(f"--- [START] {BRAND_NAME} 크롤링 시작 (실시간 로컬 저장) ---")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True) 
        context = await browser.new_context(
            viewport={"width": 1280, "height": 1024},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        
        for gender, categories in TARGET_MAP.items():
            for category, urls in categories.items():
                for url in urls:
                    await crawl_category(gender, category, url, context)
        
        await browser.close()

    print(f"\n📦 크롤링 완료! HDFS에 개별 파일 형태로 업로드를 시작합니다...")
    
    try:
        # subprocess.run(f"hdfs dfs -mkdir -p {HDFS_ROOT_PATH}", shell=True, check=True)
        
        # upload_cmd = f"hdfs dfs -put -f {LOCAL_SAVE_DIR}/*.json {HDFS_ROOT_PATH}/"
        # subprocess.run(upload_cmd, shell=True, check=True)
        
        # print(f"✅ [SUCCESS] 로컬의 모든 파일이 HDFS({HDFS_ROOT_PATH})에 그대로 적재되었습니다.")

            # 26.2.22 네임노드 주소 정의
        HDFS_ADDR = "hdfs://namenode:9000"
        
        # 1. mkdir에도 주소를 명시해서 정확한 곳에 폴더 생성
        subprocess.run(f"hdfs dfs -mkdir -p {HDFS_ADDR}{HDFS_ROOT_PATH}", shell=True, check=True)
        
        # 2. put 명령어 (잘 수정하신 부분)
        upload_cmd = f"hdfs dfs -put -f {LOCAL_SAVE_DIR}/*.json {HDFS_ADDR}{HDFS_ROOT_PATH}/"
        subprocess.run(upload_cmd, shell=True, check=True)
        
        print(f"✅ HDFS 업로드 성공! -> {HDFS_ADDR}{HDFS_ROOT_PATH}")
        
        # 업로드가 확실히 성공하면 그때 로컬 파일을 지우도록 rmtree를 다시 살려도 됩니다. (선택사항)
        # shutil.rmtree(LOCAL_TEMP_DIR)
        
    except subprocess.CalledProcessError as e:
        print(f"❌ HDFS 업로드 에러: {e}")
    ## 26.2.22
        
    except subprocess.CalledProcessError as e:
        print(f"❌ HDFS 업로드 중 오류 발생 (하둡 환경이 맞는지 확인해주세요): {e}")

if __name__ == "__main__":
    asyncio.run(run())