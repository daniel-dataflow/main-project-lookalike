import os
import asyncio
import re
import json
import glob
import pandas as pd
import random
from datetime import datetime
from playwright.async_api import async_playwright
from pyspark.sql import SparkSession
from pyspark.sql.types import StructType, StructField, StringType

# --- 설정 ---
BRAND_NAME = "topten"
LOCAL_OUTPUT_PATH = f"crawlers/data/{BRAND_NAME}_json_files"

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
sem = asyncio.Semaphore(3)

# Spark 스키마
schema = StructType([
    StructField("gender", StringType(), True),
    StructField("category", StringType(), True),
    StructField("product_id", StringType(), True),
    StructField("raw_json", StringType(), True)
])

async def extract_product_data_from_dom(page):
    try:
        for _ in range(3):
            await page.mouse.wheel(0, 500)
            await asyncio.sleep(0.5)
        
        data = await page.evaluate("""() => {
            const result = {};
            
            // ---------------------------------------------------------
            // [1] 기본 정보
            // ---------------------------------------------------------
            result.goodsNo = location.href.match(/\/product\/([A-Z0-9]+)\/detail/)?.[1] || "";
            result.goodsNm = document.querySelector('meta[property="og:title"]')?.content || document.title;
            result.brandName = "TOPTEN10";
            result.thumbnailImageUrl = document.querySelector('meta[property="og:image"]')?.content || "";

            // ---------------------------------------------------------
            // [2] 가격 (정밀 타겟팅)
            // ---------------------------------------------------------
            let price = 0;
            const metaPrice = document.querySelector('meta[property="product:price:amount"]')?.content;
            if (metaPrice) price = parseInt(metaPrice);
            
            if (price === 0) {
                // 사용자 제보 구조: div.d-flex.align-items-end > strong
                const containers = document.querySelectorAll('div.d-flex.align-items-end');
                for (const container of containers) {
                    const strongs = container.querySelectorAll('strong');
                    for (const s of strongs) {
                        const txt = s.innerText.replace(/[^0-9]/g, '');
                        // %가 없고 숫자가 있는 경우
                        if (!s.innerText.includes('%') && txt.length > 0) {
                            price = parseInt(txt);
                            break;
                        }
                    }
                    if (price > 0) break;
                }
            }
            result.price = price;

            // ---------------------------------------------------------
            // [3] 품절 여부
            // ---------------------------------------------------------
            let isSoldOut = false;
            if (price === 0) isSoldOut = true; // 가격 못 찾으면 품절로 간주
            
            const buyBtns = document.querySelectorAll('.btn-buy, .btn-order, button');
            for(let btn of buyBtns) {
                const txt = btn.innerText;
                if ((txt.includes('구매') || txt.includes('장바구니')) && (btn.disabled || txt.includes('품절'))) {
                    isSoldOut = true;
                }
            }
            result.is_sold_out = isSoldOut;

            // ---------------------------------------------------------
            // [4] 사이즈 재고
            // ---------------------------------------------------------
            const sizeStockInfo = [];
            document.querySelectorAll('.option-list.size button, .size-area button').forEach(btn => {
                const name = btn.innerText.trim();
                // "전체삭제" 같은 UI 버튼 제외, 10글자 이하만 사이즈로 인정
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

            // ---------------------------------------------------------
            // [5] 색상 옵션 (사용자 제보 구조 반영: tooltip-box) - 핵심!
            // ---------------------------------------------------------
            const otherColorIds = [];
            
            // "컬러" 텍스트가 있는 영역을 먼저 찾고, 그 주변의 버튼들을 탐색
            // 제공해주신 HTML 구조: .tooltip-box col-auto > button[onclick*='goGodDetail']
            
            const colorButtons = document.querySelectorAll('.tooltip-box button, .color-chip button, .option-list.color button');
            
            colorButtons.forEach(btn => {
                const onclick = btn.getAttribute('onclick') || "";
                
                // goGodDetail('MSF4KG1501BK', ...) 패턴 추출
                const match = onclick.match(/goGodDetail\\(['"]([A-Z0-9]+)['"]/);
                
                if (match && match[1]) {
                    const id = match[1];
                    // 현재 상품 ID와 다르면 추가
                    if (id !== result.goodsNo) {
                        otherColorIds.push(id);
                    }
                }
            });
            
            // 중복 제거
            result.other_color_ids = [...new Set(otherColorIds)];

            // ---------------------------------------------------------
            // [6] 이미지
            // ---------------------------------------------------------
            const images = [];
            if (result.thumbnailImageUrl) images.push(result.thumbnailImageUrl);
            
            document.querySelectorAll('img').forEach(img => {
                if (img.src && img.src.includes('goodwearmall') && 
                    !img.src.includes('icon') && !img.src.includes('logo') && 
                    !img.src.includes('banner')) {
                    images.push(img.src);
                }
            });
            result.goodsImages = [...new Set(images)];

            // ---------------------------------------------------------
            // [7] 스펙
            // ---------------------------------------------------------
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
            print(f"   🔎 {product_id} 접속 중...")
            await p_page.goto(url, timeout=60000, wait_until="domcontentloaded")
            
            # 재시도 로직
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
                print(f"   ✅ {product_id} 완료 (가격: {product_dict.get('price')}원)")
                
                for oid in product_dict.get('other_color_ids', []):
                    if oid not in visited_products: new_ids.append(oid)
            else:
                print(f"   ⚠️ {product_id} 데이터 없음")

        except Exception as e:
            print(f"   ❌ {product_id} 에러: {str(e)[:50]}")
        finally:
            await p_page.close()

    if new_ids:
        tasks = [process_product(oid, gender, category, context, collected_data) for oid in new_ids]
        await asyncio.gather(*tasks)

async def crawl_category(gender, category_name, target_url, context, collected_data):
    print(f"\n>>> 🎯 [{gender}-{category_name}] 목록 수집 시작")
    page = await context.new_page()
    product_ids = set()
    
    try:
        await page.goto(target_url, timeout=60000, wait_until="domcontentloaded")
        
        print("   ⏳ 데이터 로딩 중 (HTML 전체 스캔 준비)...")
        # 스크롤을 내려서 lazy loading된 상품들도 HTML에 로드되게 함
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            await asyncio.sleep(1.5)
        
        content = await page.content()
        
        # 패턴: 영문3자 + 숫자1자 + 영문2자 + 숫자4자 + 영문/숫자 (예: MSG2KG1001CH)
        matches = re.findall(r"[A-Z]{3}\d[A-Z]{2}\d{4}[A-Z0-9]+", content)
        
        for pid in matches:
            # 너무 짧거나 긴 노이즈 데이터 제외 (상품코드는 보통 10~15자)
            if 10 <= len(pid) <= 15:
                product_ids.add(pid)
        
        print(f"   🔗 추출된 후보 ID: {len(product_ids)}개") # 이 메시지가 나와야 최신 코드임
        await page.close()
        
        if len(product_ids) == 0:
            print("   ❌ ID 추출 실패. HTML 소스에 패턴이 없거나 로딩되지 않았습니다.")
            return

        tasks = [process_product(pid, gender, category_name, context, collected_data) for pid in list(product_ids)]
        await asyncio.gather(*tasks)

    except Exception as e:
        print(f"   ❌ 목록 수집 실패: {e}")
        await page.close()

async def run():
    print(f"--- [START] TOPTEN 최종 정규식 버전 ---")
    collected_data = [] 
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"]
        ) 
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="ko-KR",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        for gender, categories in TARGET_MAP.items():
            for category, urls in categories.items():
                if isinstance(urls, str): urls = [urls]
                for url in urls:
                    await crawl_category(gender, category, url, context, collected_data)
        
        await browser.close()

    if collected_data:
        print(f"\n📦 {len(collected_data)}건 수집 완료. Spark 저장 시작...")
        pdf = pd.DataFrame(collected_data)
        
        spark = SparkSession.builder \
            .appName(f"{BRAND_NAME}_Crawler") \
            .config("spark.master", "local[1]") \
            .config("spark.driver.memory", "4g") \
            .config("spark.sql.execution.arrow.pyspark.enabled", "true") \
            .getOrCreate()
        
        try:
            df = spark.createDataFrame(pdf, schema=schema).coalesce(1)
            temp_path = f"crawlers/data/temp_{BRAND_NAME}_output"
            df.write.mode("overwrite").json(temp_path)
            
            if not os.path.exists(LOCAL_OUTPUT_PATH):
                os.makedirs(LOCAL_OUTPUT_PATH)

            json_files = glob.glob(f"{temp_path}/*.json")
            for file in json_files:
                with open(file, 'r', encoding='utf-8') as f:
                    for line in f:
                        row = json.loads(line)
                        raw_data = json.loads(row['raw_json'])
                        filename = f"{BRAND_NAME}_{row['gender'].lower()}_{row['category'].lower()}_{row['product_id']}.json"
                        with open(os.path.join(LOCAL_OUTPUT_PATH, filename), 'w', encoding='utf-8') as out_f:
                            json.dump(raw_data, out_f, ensure_ascii=False, indent=4)
            print(f"\n✨ 저장 완료: {LOCAL_OUTPUT_PATH}")
        finally:
            spark.stop()
    else:
        print("\n❌ 수집된 데이터가 없습니다.")

if __name__ == "__main__":
    asyncio.run(run())