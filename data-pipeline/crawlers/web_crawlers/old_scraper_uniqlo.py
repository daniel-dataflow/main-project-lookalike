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
BRAND_NAME = "uniqlo"
## 26.2.15
TODAY_STR = datetime.now().strftime('%Y%m%d') # 20260215 형태
# namenode는 docker-compose의 서비스 이름이고, 9000은 설정하신 포트입니다.
#HDFS_ROOT_PATH = f"hdfs://namenode:9000/user/airflow/data/{BRAND_NAME}/{TODAY_STR}"
HDFS_ROOT_PATH = f"/raw/{BRAND_NAME}/{TODAY_STR}" # 'hdfs://' 빼고 깔끔하게 경로만
# TODAY_STR = datetime.now().strftime('%Y%m%d')
# HDFS_ROOT_PATH = f"hdfs:///raw/{BRAND_NAME}/{TODAY_STR}"

LOCAL_TEMP_DIR = f"crawlers/data/{BRAND_NAME}_json_files"
SPARK_TEMP_PATH = f"crawlers/data/temp_{BRAND_NAME}_output"

TARGET_MAP = {
    "Men": {
        "Outer": [
            "https://www.uniqlo.com/kr/ko/men/outerwear/jackets",
            "https://www.uniqlo.com/kr/ko/men/outerwear/coats",
            "https://www.uniqlo.com/kr/ko/men/sweaters-and-knitwear"
        ],
        "Top": [
            "https://www.uniqlo.com/kr/ko/men/tops/sweatshirts-and-hoodies?path=%2C%2C58040%2C",
            "https://www.uniqlo.com/kr/ko/men/tops/t-shirts?path=%2C%2C58039%2C",
            "https://www.uniqlo.com/kr/ko/men/shirts-and-polo-shirts"     
        ],
        "Bottom": [
            "https://www.uniqlo.com/kr/ko/men/bottoms"
        ]
    },
    "Women": {
        "Outer": [
            "https://www.uniqlo.com/kr/ko/women/outerwear/jackets",
            "https://www.uniqlo.com/kr/ko/women/outerwear/coats",
            "https://www.uniqlo.com/kr/ko/women/sweaters-and-knitwear"
        ],
        "Top": [
            "https://www.uniqlo.com/kr/ko/women/tops",
            "https://www.uniqlo.com/kr/ko/women/shirts-and-blouses"
        ],
        "Bottom": [
            "https://www.uniqlo.com/kr/ko/women/bottoms"
        ]
    }
}

visited_products = set()
sem = asyncio.Semaphore(3) # 3

schema = StructType([
    StructField("gender", StringType(), True),
    StructField("category", StringType(), True),
    StructField("product_id", StringType(), True),
    StructField("raw_json", StringType(), True)
])

async def extract_product_data_from_dom(page, product_id):
    try:
        await page.mouse.wheel(0, 2000)
        await asyncio.sleep(1.5)
        
        await page.evaluate("""() => {
            document.querySelectorAll('.rah-static, [aria-hidden="true"]').forEach(el => {
                el.style.display = 'block';
                el.style.height = 'auto';
                el.style.visibility = 'visible';
            });
            document.querySelectorAll('button').forEach(btn => {
                if(btn.innerText.includes('상세 설명') || btn.innerText.includes('소재 정보')) {
                    btn.click();
                }
            });
        }""")
        await asyncio.sleep(1.0)

        data = await page.evaluate("""() => {
            const result = {};
            const urlMatch = location.href.match(/\/products\/([A-Z0-9-]+)/);
            result.goodsNo = urlMatch ? urlMatch[1] : ""; 
            
            const bodyText = document.body.innerText;
            const productNoMatch = bodyText.match(/제품 번호[:\\s]*([0-9]+)/);
            result.display_goods_no = productNoMatch ? productNoMatch[1] : "";

            const metaTitle = document.querySelector('meta[property="og:title"]')?.content;
            result.goodsNm = metaTitle ? metaTitle.split('|')[0].trim() : document.title;
            result.brandName = "UNIQLO";
            result.thumbnailImageUrl = document.querySelector('meta[property="og:image"]')?.content || "";

            let price = 0;
            const priceEl = document.querySelector('.fr-ec-price-text') || document.querySelector('.price');
            if (priceEl) price = parseInt(priceEl.innerText.replace(/[^0-9]/g, '') || "0");
            result.price = price;

            let isSoldOut = false;
            if (price === 0) isSoldOut = true;
            document.querySelectorAll('button').forEach(btn => {
                const txt = btn.innerText;
                if ((txt.includes('구매') || txt.includes('장바구니')) && btn.disabled) isSoldOut = true;
                if (txt.includes('품절')) isSoldOut = true;
            });
            result.is_sold_out = isSoldOut;

            const sizeStockInfo = [];
            document.querySelectorAll('button.chip, label.chip').forEach(chip => {
                const name = chip.innerText.trim();
                if (name && name.length < 6 && /^[0-9A-Z]+$/.test(name)) {
                    let isItemSoldOut = chip.classList.contains('disabled') || chip.disabled || chip.getAttribute('aria-disabled') === 'true';
                    if (!sizeStockInfo.some(s => s.size === name)) {
                        sizeStockInfo.push({ size: name, is_sold_out: isItemSoldOut, stock_qty: isItemSoldOut ? 0 : 999 });
                    }
                }
            });
            result.size_stock = sizeStockInfo;

            const colors = [];
            document.querySelectorAll('.collection-list-horizontal button.chip, .color-chip button').forEach(btn => {
                const code = btn.value || (btn.id.split('-').length > 1 ? btn.id.split('-')[1] : ""); 
                const img = btn.querySelector('img');
                const name = img ? img.alt : ""; 
                const iconUrl = img ? img.src : "";
                if (code && name) {
                    colors.push({ color_code: code, color_name: name, icon_url: iconUrl });
                }
            });
            result.colors = colors;

            const images = [];
            const galleryImgs = document.querySelectorAll('.media-gallery--grid img');
            galleryImgs.forEach(img => {
                let src = img.getAttribute('data-src') || img.src;
                if (src && src.includes('uniqlo.com')) {
                    src = src.split('?')[0];
                    images.push(src);
                }
            });
            result.goodsImages = [...new Set(images)];

            const detailedInfo = { "description": "", "material_info": {} };
            const descEl = document.getElementById('productLongDescription-content');
            if (descEl) detailedInfo['description'] = descEl.innerText.replace(/\\n+/g, ' ').trim();

            const matEl = document.getElementById('productMaterialDescription-content');
            let rawMatText = matEl ? matEl.innerText : "";
            if (rawMatText) {
                const lines = rawMatText.split('\\n');
                let currentKey = null;
                const keywords = ['소재', '세탁 방법', '제조국', '제조연월', '제조사/수입자'];
                lines.forEach(line => {
                    const txt = line.trim();
                    if (!txt) return;
                    if (keywords.includes(txt)) currentKey = txt;
                    else if (currentKey) {
                        detailedInfo['material_info'][currentKey] = (detailedInfo['material_info'][currentKey] || "") + " " + txt;
                    }
                });
            }
            result.goodsMaterial = detailedInfo;
            return result;
        }""")
        
        if not data or not data.get('goodsNm'): return None
        data['url'] = page.url
        data['scraped_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return data
    except Exception: return None

async def process_product(product_id, gender, category, context, collected_data):
    if product_id in visited_products: return
    visited_products.add(product_id)

    async with sem:
        clean_id = product_id.split('?')[0]
        url = f"https://www.uniqlo.com/kr/ko/products/{clean_id}"
        p_page = await context.new_page()
        try:
            print(f"   🔎 {clean_id} 접속 중...")
            await p_page.goto(url, timeout=60000, wait_until="domcontentloaded")
            product_dict = await extract_product_data_from_dom(p_page, clean_id)

            if product_dict:
                colors = product_dict.get('colors', [])
                if colors:
                    for color in colors:
                        final_data = product_dict.copy()
                        final_data['color_name'] = color['color_name'] 
                        final_data['goodsNo'] = f"{clean_id}_{color['color_code']}" 
                        final_data['url'] = f"{url}?colorDisplayCode={color['color_code']}"
                        if color['icon_url']: final_data['thumbnailImageUrl'] = color['icon_url']
                        collected_data.append({
                            "gender": gender, "category": category, "product_id": final_data['goodsNo'], 
                            "raw_json": json.dumps(final_data, ensure_ascii=False)
                        })
                else:
                    collected_data.append({
                        "gender": gender, "category": category, "product_id": clean_id, 
                        "raw_json": json.dumps(product_dict, ensure_ascii=False)
                    })
                print(f"   ✅ {clean_id} 처리 완료")
        except Exception as e:
            print(f"   ❌ {clean_id} 에러: {str(e)[:50]}")
        finally:
            await p_page.close()

async def crawl_category(gender, category_name, target_url, context, collected_data):
    print(f"\n>>> 🎯 [{gender}-{category_name}] 시작")
    page = await context.new_page()
    try:
        await page.goto(target_url, timeout=60000, wait_until="domcontentloaded")
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            await asyncio.sleep(1.0)
        
        content = await page.content()
        matches = re.findall(r"/products/([A-Z0-9-]+)", content)
        product_ids = {pid for pid in matches if len(pid) >= 5 and "review" not in pid}
        
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
        await browser.close()

    if collected_data:
        print(f"\n📦 {len(collected_data)}건 수집 완료. Spark HDFS 적재 시작...")
        pdf = pd.DataFrame(collected_data)
        
        spark = SparkSession.builder \
            .appName(f"{BRAND_NAME}_to_HDFS") \
            .config("spark.sql.execution.arrow.pyspark.enabled", "true") \
            .getOrCreate()
        
        try:
            df = spark.createDataFrame(pdf, schema=schema).coalesce(1)
            
            # 1. HDFS 저장
            print(f"📡 HDFS 저장 경로: {HDFS_ROOT_PATH}")
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
            if os.path.exists(SPARK_TEMP_PATH): shutil.rmtree(SPARK_TEMP_PATH)
            if os.path.exists(LOCAL_TEMP_DIR): shutil.rmtree(LOCAL_TEMP_DIR)
            
            try:
                if not os.listdir("crawlers/data"): os.rmdir("crawlers/data")
            except: pass

            print(f"\n✅ [SUCCESS] HDFS 적재 완료 및 로컬 정리 성공.")
        except Exception as e:
            print(f"❌ 오류 발생: {e}")
        finally:
            spark.stop()
    else:
        print("\n❌ 수집된 데이터가 없습니다.")

if __name__ == "__main__":
    asyncio.run(run())