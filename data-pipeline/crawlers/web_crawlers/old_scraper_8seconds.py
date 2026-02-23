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

# [설정]
BRAND_NAME = "8seconds"
## 26.2.15
TODAY_STR = datetime.now().strftime('%Y%m%d') # 20260215 형태
# namenode는 docker-compose의 서비스 이름이고, 9000은 설정하신 포트입니다.
#HDFS_ROOT_PATH = f"hdfs://namenode:9000/user/airflow/data/{BRAND_NAME}/{TODAY_STR}"
HDFS_ROOT_PATH = f"/raw/{BRAND_NAME}/{TODAY_STR}" # 'hdfs://' 빼고 깔끔하게 경로만
# TODAY_STR = datetime.now().strftime('%Y%m%d')
# HDFS_ROOT_PATH = f"hdfs:///raw/{BRAND_NAME}/{TODAY_STR}"

LOCAL_TEMP_JSON_DIR = "crawlers/data/8seconds_json_files"
SPARK_TEMP_PATH = f"crawlers/data/temp_{BRAND_NAME}_output"

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

visited_products = set()

schema = StructType([
    StructField("gender", StringType(), True),
    StructField("category", StringType(), True),
    StructField("product_id", StringType(), True),
    StructField("raw_json", StringType(), True)
])

sem = asyncio.Semaphore(3) # 3

async def extract_product_data_from_dom(page):
    try:
        await page.wait_for_selector('.gods-name', timeout=10000)
        data = await page.evaluate("""() => {
            const result = {};
            result.goodsNo = window._godNo || "";
            if (!result.goodsNo) {
                const urlMatch = location.href.match(/([A-Z0-9]+)\/good/);
                result.goodsNo = urlMatch ? urlMatch[1] : "";
            }
            result.goodsNm = document.querySelector('#goodDtlTitle')?.innerText.trim();
            
            const soldOutDiv = document.querySelector('#restockSoldOut');
            const isSoldOutDivVisible = soldOutDiv && soldOutDiv.style.display !== 'none';
            const buyBtn = document.querySelector('.submit .btn.order');
            const isBtnDisabled = buyBtn && buyBtn.classList.contains('disabled');
            result.is_sold_out = isSoldOutDivVisible || isBtnDisabled;
            
            const priceTxt = document.querySelector('#godPrice')?.innerText || "0";
            result.price = parseInt(priceTxt.replace(/[^0-9]/g, ''));
            
            const otherColorIds = [];
            const colorLabels = document.querySelectorAll('.opt-select.color-thumbs label');
            colorLabels.forEach(label => {
                const onclickText = label.getAttribute('onclick') || "";
                const idMatch = onclickText.match(/\/([A-Z0-9]{10,})\/good/);
                if (idMatch) {
                    const extractedId = idMatch[1];
                    if (extractedId !== result.goodsNo) otherColorIds.push(extractedId);
                }
            });
            result.other_color_ids = [...new Set(otherColorIds)];

            const sizeStockInfo = [];
            const rows = Array.from(document.querySelectorAll('.gods-option .row'));
            let sizeRow = rows.find(row => row.querySelector('.tit')?.innerText.trim() === '사이즈');

            if (sizeRow) {
                const sizeItems = sizeRow.querySelectorAll('.rdo_group li');
                sizeItems.forEach(li => {
                    const sizeName = li.querySelector('label')?.innerText.trim();
                    const inputId = li.querySelector('input')?.id; 
                    if (sizeName && inputId) {
                        const hiddenInputId = inputId.replace('ra_', 'sizeItmNo');
                        const hiddenInput = document.getElementById(hiddenInputId);
                        let stockQty = 0, isSoldOut = false;
                        if (hiddenInput) {
                            stockQty = parseInt(hiddenInput.getAttribute('onlineusefulinvqty') || "0");
                            if (stockQty <= 0 || hiddenInput.getAttribute('itmstatcd') === 'SLDOUT') isSoldOut = true;
                        }
                        if (li.classList.contains('disabled')) isSoldOut = true;
                        sizeStockInfo.push({ size: sizeName, stock_qty: stockQty, is_sold_out: isSoldOut });
                    }
                });
            }
            result.size_stock = sizeStockInfo;
            return result;
        }""")
        data['url'] = page.url
        data['scraped_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return data
    except Exception:
        return None

async def process_product(product_id, gender, category, context, collected_data):
    if product_id in visited_products: return
    visited_products.add(product_id)

    async with sem:
        url = f"https://www.ssfshop.com/8-seconds/{product_id}/good?brandShopNo=BDMA07A01&brndShopId=8SBSS"
        p_page = await context.new_page()
        try:
            await p_page.goto(url, timeout=60000, wait_until="load")
            await asyncio.sleep(1.5) 
            product_dict = await extract_product_data_from_dom(p_page)
            
            if product_dict:
                collected_data.append({
                    "gender": gender, "category": category, "product_id": product_id,
                    "raw_json": json.dumps(product_dict, ensure_ascii=False)
                })
                print(f"   ✅ {product_id} 수집 완료")
                other_ids = product_dict.get('other_color_ids', [])
                if other_ids:
                    tasks = [process_product(oid, gender, category, context, collected_data) for oid in other_ids]
                    await asyncio.gather(*tasks)
        except Exception as e:
            print(f"   ❌ {product_id} 에러: {str(e)[:50]}")
        finally:
            if not p_page.is_closed(): await p_page.close()

async def crawl_category(gender, category_name, target_url, context, collected_data):
    print(f"\n>>> 🎯 [{gender}-{category_name}] 시작")
    page = await context.new_page()
    try:
        await page.goto(target_url, timeout=60000)
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, 3000)")
            await asyncio.sleep(1)
        
        codes = await page.evaluate("""() => 
            Array.from(document.querySelectorAll('li.god-item')).map(item => item.getAttribute('view-godno')).filter(c => c !== null)
        """)
        await page.close()
        tasks = [process_product(code, gender, category_name, context, collected_data) for code in list(set(codes))]
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
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        for gender, categories in TARGET_MAP.items():
            for category, urls in categories.items():
                for url in urls:
                    await crawl_category(gender, category, url, context, collected_data)
        await browser.close()

    if collected_data:
        print(f"\n📦 총 {len(collected_data)}건 수집 완료. Spark 가공 시작...")
        pdf = pd.DataFrame(collected_data)
        
        spark = SparkSession.builder \
            .appName(f"{BRAND_NAME}_to_HDFS") \
            .config("spark.sql.execution.arrow.pyspark.enabled", "true") \
            .getOrCreate()
        
        try:
            df = spark.createDataFrame(pdf, schema=schema).coalesce(1)
            
            # 1. HDFS 저장 (raw/brand/date)
            print(f"📡 HDFS 저장 경로: {HDFS_ROOT_PATH}")
            df.write.mode("overwrite").json(HDFS_ROOT_PATH)
            
            # 2. 로컬 가공 (개별 파일 생성이 필요한 로직 유지)
            if not os.path.exists(LOCAL_TEMP_JSON_DIR): os.makedirs(LOCAL_TEMP_JSON_DIR)
            df.write.mode("overwrite").json(SPARK_TEMP_PATH)
            
            for file in glob.glob(f"{SPARK_TEMP_PATH}/*.json"):
                with open(file, 'r', encoding='utf-8') as f:
                    for line in f:
                        row = json.loads(line)
                        raw_data = json.loads(row['raw_json'])
                        filename = f"{BRAND_NAME}_{row['gender'].lower()}_{row['category'].lower()}_{row['product_id']}.json"
                        with open(os.path.join(LOCAL_TEMP_JSON_DIR, filename), 'w', encoding='utf-8') as out_f:
                            json.dump(raw_data, out_f, ensure_ascii=False, indent=4)
            
            print(f"✨ 로컬 가공 완료. 클리닝 작업을 시작합니다.")

            # 3. 로컬 파일 및 디렉토리 삭제
            if os.path.exists(SPARK_TEMP_PATH):
                shutil.rmtree(SPARK_TEMP_PATH)
                print(f"🗑️ 삭제 완료: {SPARK_TEMP_PATH}")
            
            if os.path.exists(LOCAL_TEMP_JSON_DIR):
                shutil.rmtree(LOCAL_TEMP_JSON_DIR)
                print(f"🗑️ 삭제 완료: {LOCAL_TEMP_JSON_DIR}")
                
            try:
                if not os.listdir("crawlers/data"):
                    os.rmdir("crawlers/data")
            except: pass

            print(f"\n✅ [SUCCESS] 모든 데이터가 HDFS에 저장되었으며 로컬 환경은 정리되었습니다.")

        except Exception as e:
            print(f"❌ Spark 처리 중 오류 발생: {e}")
        finally:
            spark.stop()
    else:
        print("\n❌ 수집된 데이터가 없어 종료합니다.")

if __name__ == "__main__":
    asyncio.run(run())