import os
import asyncio
import re
import json
import glob
import shutil
import pandas as pd
from datetime import datetime
from playwright.async_api import async_playwright
from pyspark.sql import SparkSession
from pyspark.sql.types import StructType, StructField, StringType

# --- 설정 ---
BRAND_NAME = "topten"
## 26.2.15
TODAY_STR = datetime.now().strftime('%Y%m%d') # 20260215 형태
# namenode는 docker-compose의 서비스 이름이고, 9000은 설정하신 포트입니다.
#HDFS_ROOT_PATH = f"hdfs://namenode:9000/user/airflow/data/{BRAND_NAME}/{TODAY_STR}"
# TODAY_STR = datetime.now().strftime('%Y%m%d')
HDFS_ROOT_PATH = f"hdfs:///raw/{BRAND_NAME}/{TODAY_STR}"
LOCAL_TEMP_DIR = f"crawlers/data/{BRAND_NAME}_json_files"
SPARK_TEMP_PATH = f"crawlers/data/temp_{BRAND_NAME}_output"

# 수집 대상 URL
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

visited_products = set()
sem = asyncio.Semaphore(2) # 3

schema = StructType([
    StructField("gender", StringType(), True),
    StructField("category", StringType(), True),
    StructField("product_id", StringType(), True),
    StructField("raw_json", StringType(), True)
])

async def extract_product_data_from_dom(page):
    try:
        for _ in range(5):
            await page.mouse.wheel(0, 500)
            await asyncio.sleep(0.5)
        
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
                const containers = document.querySelectorAll('div.d-flex.align-items-end');
                for (const container of containers) {
                    const strongs = container.querySelectorAll('strong');
                    for (const s of strongs) {
                        const txt = s.innerText.replace(/[^0-9]/g, '');
                        if (!s.innerText.includes('%') && txt.length > 0) {
                            price = parseInt(txt);
                            break;
                        }
                    }
                    if (price > 0) break;
                }
            }
            result.price = price;

            let isSoldOut = false;
            if (price === 0) isSoldOut = true;
            const buyBtns = document.querySelectorAll('.btn-buy, .btn-order, button');
            for(let btn of buyBtns) {
                const txt = btn.innerText;
                if ((txt.includes('구매') || txt.includes('장바구니')) && (btn.disabled || txt.includes('품절'))) {
                    isSoldOut = true;
                }
            }
            result.is_sold_out = isSoldOut;

            const sizeStockInfo = [];
            document.querySelectorAll('.option-list.size button, .size-area button').forEach(btn => {
                const name = btn.innerText.trim();
                if (name && name.length < 10 && !name.includes('삭제')) {
                    const isItemSoldOut = btn.classList.contains('soldout') || btn.disabled;
                    sizeStockInfo.push({
                        size: name.replace(/\(.*\)/, '').trim(),
                        is_sold_out: isItemSoldOut,
                        stock_qty: isItemSoldOut ? 0 : 999
                    });
                }
            });
            result.size_stock = sizeStockInfo;

            const otherColorIds = [];
            const colorButtons = document.querySelectorAll('.tooltip-box button, .color-chip button, .option-list.color button');
            colorButtons.forEach(btn => {
                const onclick = btn.getAttribute('onclick') || "";
                const match = onclick.match(/goGodDetail\\(['"]([A-Z0-9]+)['"]/);
                if (match && match[1] && match[1] !== result.goodsNo) {
                    otherColorIds.push(match[1]);
                }
            });
            result.other_color_ids = [...new Set(otherColorIds)];

            const images = [];
            if (result.thumbnailImageUrl) images.push(result.thumbnailImageUrl);
            document.querySelectorAll('img').forEach(img => {
                if (img.src && img.src.includes('goodwearmall') && !img.src.includes('icon') && !img.src.includes('logo')) {
                    images.push(img.src);
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

async def process_product(product_id, gender, category, context, collected_data):
    if product_id in visited_products: return
    visited_products.add(product_id)
    new_ids = []

    async with sem:
        url = f"https://topten10.goodwearmall.com/product/{product_id}/detail"
        p_page = await context.new_page()
        try:
            await p_page.goto(url, timeout=60000, wait_until="domcontentloaded")
            product_dict = None
            for _ in range(2):
                product_dict = await extract_product_data_from_dom(p_page)
                if product_dict and product_dict.get('price', 0) > 0: break
                await asyncio.sleep(2)

            if product_dict:
                collected_data.append({
                    "gender": gender, "category": category, "product_id": product_id,
                    "raw_json": json.dumps(product_dict, ensure_ascii=False)
                })
                print(f"   ✅ {product_id} 완료")
                for oid in product_dict.get('other_color_ids', []):
                    if oid not in visited_products: new_ids.append(oid)
        except Exception as e:
            print(f"   ❌ {product_id} 에러: {str(e)[:50]}")
        finally:
            await p_page.close()

    if new_ids:
        tasks = [process_product(oid, gender, category, context, collected_data) for oid in new_ids]
        await asyncio.gather(*tasks)

async def crawl_category(gender, category_name, target_url, context, collected_data):
    print(f"\n>>> 🎯 [{gender}-{category_name}] 시작")
    page = await context.new_page()
    try:
        await page.goto(target_url, timeout=60000, wait_until="domcontentloaded")
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            await asyncio.sleep(1.5)
        
        content = await page.content()
        matches = re.findall(r"[A-Z]{3}\d[A-Z]{2}\d{4}[A-Z0-9]+", content)
        product_ids = {pid for pid in matches if 10 <= len(pid) <= 15}
        
        await page.close()
        tasks = [process_product(pid, gender, category_name, context, collected_data) for pid in list(product_ids)]
        await asyncio.gather(*tasks)
    except Exception as e:
        print(f"   ❌ 목록 수집 실패: {e}")
        if not page.is_closed(): await page.close()

async def run():
    print(f"--- [START] {BRAND_NAME} 크롤링 및 하둡 적재 ---")
    collected_data = [] 
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True) 
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        
        for gender, categories in TARGET_MAP.items():
            for category, urls in categories.items():
                if isinstance(urls, str): urls = [urls]
                for url in urls:
                    await crawl_category(gender, category, url, context, collected_data)
        #await browser.close()
        # [수정 1] 브라우저와 컨텍스트를 여기서 명시적으로 닫습니다.(26.2.15)
        await context.close()
        await browser.close()

    # [수정 2] 브라우저 종료 후 메모리가 OS로 완전히 반환될 시간을 줍니다.(26.2.15)
    print("\n⏳ 브라우저 종료 중... 메모리 정리를 위해 10초간 대기합니다.")
    await asyncio.sleep(10)

    if collected_data:
        print(f"\n📦 {len(collected_data)}건 수집 완료. Spark HDFS 적재 시작...")
        pdf = pd.DataFrame(collected_data)
        
        # [수정 3] Spark 세션에 메모리 제한을 코드 내에서도 명시합니다.(26.2.15, 512m)
        spark = SparkSession.builder \
            .appName(f"{BRAND_NAME}_to_HDFS") \
            .config("spark.driver.memory", "512m") \
            .config("spark.executor.memory", "512m") \
            .config("spark.sql.execution.arrow.pyspark.enabled", "true") \
            .getOrCreate()
        
        try:
            df = spark.createDataFrame(pdf, schema=schema).coalesce(1)
            
            # 1. HDFS 저장
            print(f"📡 HDFS 저장 중: {HDFS_ROOT_PATH}")
            df.write.mode("overwrite").json(HDFS_ROOT_PATH)
            
            # 2. 로컬 임시 가공
            if not os.path.exists(LOCAL_TEMP_DIR): os.makedirs(LOCAL_TEMP_DIR)
            df.write.mode("overwrite").json(SPARK_TEMP_PATH)
            
            for file in glob.glob(f"{SPARK_TEMP_PATH}/*.json"):
                with open(file, 'r', encoding='utf-8') as f:
                    for line in f:
                        row = json.loads(line)
                        raw_data = json.loads(row['raw_json'])
                        filename = f"{BRAND_NAME}_{row['gender'].lower()}_{row['category'].lower()}_{row['product_id']}.json"
                        with open(os.path.join(LOCAL_TEMP_DIR, filename), 'w', encoding='utf-8') as out_f:
                            json.dump(raw_data, out_f, ensure_ascii=False, indent=4)
            
            # 3. 로컬 클리닝
            if os.path.exists(SPARK_TEMP_PATH):
                shutil.rmtree(SPARK_TEMP_PATH)
            if os.path.exists(LOCAL_TEMP_DIR):
                shutil.rmtree(LOCAL_TEMP_DIR)
            try:
                if not os.listdir("crawlers/data"):
                    os.rmdir("crawlers/data")
            except: pass

            print(f"\n✅ [SUCCESS] 모든 데이터가 HDFS({HDFS_ROOT_PATH})에 저장되었으며 로컬 파일은 삭제되었습니다.")

        except Exception as e:
            print(f"❌ Spark/HDFS 처리 중 오류: {e}")
        finally:
            spark.stop()
    else:
        print("\n❌ 수집된 데이터가 없습니다.")

if __name__ == "__main__":
    asyncio.run(run())