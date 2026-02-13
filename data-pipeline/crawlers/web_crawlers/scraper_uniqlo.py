import os
import asyncio
import re
import json
import glob
import pandas as pd
from datetime import datetime
from playwright.async_api import async_playwright
from pyspark.sql import SparkSession
from pyspark.sql.types import StructType, StructField, StringType

# --- 설정 ---
BRAND_NAME = "uniqlo"
LOCAL_OUTPUT_PATH = f"crawlers/data/{BRAND_NAME}_json_files"

TARGET_MAP = {
    "Men": {
        "Outer": [
            "https://www.uniqlo.com/kr/ko/men/outerwear/coats"
        ]
    }
}

visited_products = set()
sem = asyncio.Semaphore(3)

# Spark Schema
schema = StructType([
    StructField("gender", StringType(), True),
    StructField("category", StringType(), True),
    StructField("product_id", StringType(), True),
    StructField("raw_json", StringType(), True)
])

async def extract_product_data_from_dom(page, product_id):
    """갤러리 그리드 타겟팅으로 이미지 전수 조사"""
    try:
        # [1] 로딩 유도 & 아코디언 강제 개방
        await page.mouse.wheel(0, 2000)
        await asyncio.sleep(1.5)
        
        # CSS 강제 조작 (숨겨진 내용 펼치기)
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

            // 1. 기본 정보
            const urlMatch = location.href.match(/\/products\/([A-Z0-9-]+)/);
            result.goodsNo = urlMatch ? urlMatch[1] : ""; 
            
            const bodyText = document.body.innerText;
            const productNoMatch = bodyText.match(/제품 번호[:\\s]*([0-9]+)/);
            result.display_goods_no = productNoMatch ? productNoMatch[1] : "";

            const metaTitle = document.querySelector('meta[property="og:title"]')?.content;
            result.goodsNm = metaTitle ? metaTitle.split('|')[0].trim() : document.title;
            result.brandName = "UNIQLO";
            result.thumbnailImageUrl = document.querySelector('meta[property="og:image"]')?.content || "";

            // 2. 가격
            let price = 0;
            const priceEl = document.querySelector('.fr-ec-price-text') || document.querySelector('.price');
            if (priceEl) price = parseInt(priceEl.innerText.replace(/[^0-9]/g, '') || "0");
            if (price === 0) {
                const ariaPrice = document.querySelector('.fr-ec-price');
                if (ariaPrice) {
                    const match = ariaPrice.getAttribute('aria-label').match(/([0-9,]+)원/);
                    if (match) price = parseInt(match[1].replace(/,/g, ''));
                }
            }
            result.price = price;

            // 3. 품절 여부
            let isSoldOut = false;
            if (price === 0) isSoldOut = true;
            document.querySelectorAll('button').forEach(btn => {
                const txt = btn.innerText;
                if ((txt.includes('구매') || txt.includes('장바구니')) && btn.disabled) isSoldOut = true;
                if (txt.includes('품절')) isSoldOut = true;
            });
            result.is_sold_out = isSoldOut;

            // 4. 사이즈
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

            // 5. 색상
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

            // ---------------------------------------------------------
            // [6] 이미지 (사용자 제보 구조 반영: .media-gallery--grid)
            // ---------------------------------------------------------
            const images = [];
            
            // 1) 갤러리 그리드 내부 이미지 우선 탐색 (여기에 다 모여있음)
            const galleryImgs = document.querySelectorAll('.media-gallery--grid img');
            
            galleryImgs.forEach(img => {
                // data-src가 있으면 우선 사용 (Lazy Loading 대응), 없으면 src 사용
                let src = img.getAttribute('data-src') || img.src;
                
                if (src && src.includes('uniqlo.com')) {
                    // ?width=600 등 쿼리 스트링 제거하여 원본 화질 확보
                    src = src.split('?')[0];
                    images.push(src);
                }
            });

            // 2) 만약 갤러리에서 못 찾았으면(구조 변경 등), 페이지 전체 이미지 탐색 (백업)
            if (images.length === 0) {
                document.querySelectorAll('img').forEach(img => {
                    let src = img.src;
                    if (src && (src.includes('/goods/') || src.includes('/item/') || src.includes('/sub/')) && !src.includes('/chip/')) {
                        src = src.split('?')[0];
                        images.push(src);
                    }
                });
            }

            result.goodsImages = [...new Set(images)]; // 중복 제거

            // ---------------------------------------------------------
            // 7. 상세 정보
            // ---------------------------------------------------------
            const detailedInfo = { "description": "", "material_info": {} };
            
            // 설명 (ID 우선)
            const descEl = document.getElementById('productLongDescription-content');
            if (descEl) {
                detailedInfo['description'] = descEl.innerText.replace(/\\n+/g, ' ').trim();
            } else {
                // 텍스트 마이닝
                const pTags = document.querySelectorAll('p, div.typography');
                let capture = false;
                for (const p of pTags) {
                    if (p.innerText.includes('제품 상세 설명')) capture = true;
                    else if (p.innerText.includes('소재 정보')) capture = false;
                    else if (capture && p.innerText.length > 20) {
                        detailedInfo['description'] += p.innerText + " ";
                    }
                }
            }

            // 소재 (ID 우선)
            const matEl = document.getElementById('productMaterialDescription-content');
            let rawMatText = matEl ? matEl.innerText : "";
            
            if (!rawMatText) {
                const bodyTxt = document.body.innerText;
                const match = bodyTxt.match(/소재 정보[\\s\\S]{0,800}품질보증기준/);
                if (match) rawMatText = match[0];
            }

            if (rawMatText) {
                const lines = rawMatText.split('\\n');
                let currentKey = null;
                const keywords = ['소재', '세탁 방법', '제조국', '제조연월', '제조사/수입자', '품질보증기준'];
                
                lines.forEach(line => {
                    const txt = line.trim();
                    if (!txt) return;
                    if (keywords.includes(txt)) {
                        currentKey = txt;
                    } else if (currentKey) {
                        if (!detailedInfo['material_info'][currentKey]) {
                            detailedInfo['material_info'][currentKey] = txt;
                        } else {
                            detailedInfo['material_info'][currentKey] += " " + txt;
                        }
                    }
                });
            }
            result.goodsMaterial = detailedInfo;

            return result;
        }""")
        
        if not data or not data.get('goodsNm') or "Notice" in data.get('goodsNm', ''): 
            return None
            
        data['url'] = page.url
        data['scraped_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return data

    except Exception:
        return None

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
            
            product_dict = None
            for _ in range(2):
                product_dict = await extract_product_data_from_dom(p_page, clean_id)
                if product_dict: break
                await asyncio.sleep(1)

            if product_dict:
                colors = product_dict.get('colors', [])
                # 색상이 있으면 분할 저장, 없으면 단일 저장
                if colors:
                    print(f"   🎨 {clean_id} 색상 {len(colors)}개 발견")
                    for color in colors:
                        final_data = product_dict.copy()
                        final_data['color_name'] = color['color_name'] 
                        final_data['goodsNo'] = f"{clean_id}_{color['color_code']}" 
                        final_data['url'] = f"{url}?colorDisplayCode={color['color_code']}"
                        if color['icon_url']: final_data['thumbnailImageUrl'] = color['icon_url']

                        collected_data.append({
                            "gender": gender, "category": category, 
                            "product_id": final_data['goodsNo'], 
                            "raw_json": json.dumps(final_data, ensure_ascii=False)
                        })
                else:
                    collected_data.append({
                        "gender": gender, "category": category, 
                        "product_id": clean_id, 
                        "raw_json": json.dumps(product_dict, ensure_ascii=False)
                    })
                    print(f"   ✅ {clean_id} 단일 저장 (가격: {product_dict.get('price')}원)")
            else:
                print(f"   ⚠️ {clean_id} 데이터 추출 실패")

        except Exception as e:
            print(f"   ❌ {clean_id} 에러: {str(e)[:50]}")
        finally:
            await p_page.close()

async def crawl_category(gender, category_name, target_url, context, collected_data):
    print(f"\n>>> 🎯 [{gender}-{category_name}] 목록 수집 시작")
    page = await context.new_page()
    product_ids = set()
    
    try:
        await page.goto(target_url, timeout=60000, wait_until="domcontentloaded")
        print("   📜 목록 스크롤 중...")
        for _ in range(5):
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            await asyncio.sleep(1.0)
        
        content = await page.content()
        matches = re.findall(r"/products/([A-Z0-9-]+)", content)
        for pid in matches:
            if len(pid) >= 5 and "review" not in pid:
                product_ids.add(pid)
        
        print(f"   🔗 총 발견된 상품 수: {len(product_ids)}개")
        await page.close()
        
        tasks = [process_product(pid, gender, category_name, context, collected_data) for pid in list(product_ids)]
        await asyncio.gather(*tasks)

    except Exception as e:
        print(f"   ❌ 목록 수집 실패: {e}")
        await page.close()

async def run():
    print(f"--- [START] UNIQLO 안전 모드 수집 ---")
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