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

# 설정
BRAND_NAME = "8seconds"
LOCAL_OUTPUT_PATH = "crawlers/data/8seconds_json_files"

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

# 방문한 상품 ID를 기록하는 집합 (중복 수집 방지용)
visited_products = set()

# Spark 스키마
schema = StructType([
    StructField("gender", StringType(), True),
    StructField("category", StringType(), True),
    StructField("product_id", StringType(), True),
    StructField("raw_json", StringType(), True)
])

sem = asyncio.Semaphore(3)

async def extract_product_data_from_dom(page):
    try:
        await page.wait_for_selector('.gods-name', timeout=10000)

        data = await page.evaluate("""() => {
            const result = {};

            // [1] 기본 정보
            result.goodsNo = window._godNo || "";
            if (!result.goodsNo) {
                const urlMatch = location.href.match(/([A-Z0-9]+)\/good/);
                result.goodsNo = urlMatch ? urlMatch[1] : "";
            }
            result.goodsNm = document.querySelector('#goodDtlTitle')?.innerText.trim();
            
            // [2] 품절 여부 확인
            const soldOutDiv = document.querySelector('#restockSoldOut');
            const isSoldOutDivVisible = soldOutDiv && soldOutDiv.style.display !== 'none';
            const buyBtn = document.querySelector('.submit .btn.order');
            const isBtnDisabled = buyBtn && buyBtn.classList.contains('disabled');
            result.is_sold_out = isSoldOutDivVisible || isBtnDisabled;
            
            // 가격
            const priceTxt = document.querySelector('#godPrice')?.innerText || "0";
            result.price = parseInt(priceTxt.replace(/[^0-9]/g, ''));
            
            // ---------------------------------------------------------
            // [3] 색상 옵션 추출 (다른 색상 상품 ID 찾기)
            // ---------------------------------------------------------
            const otherColorIds = [];
            // 색상 썸네일 영역만 정확히 타겟팅
            const colorLabels = document.querySelectorAll('.opt-select.color-thumbs label');
            
            colorLabels.forEach(label => {
                const onclickText = label.getAttribute('onclick') || "";
                
                // [수정] 복잡한 URL 전체 매칭 대신, 상품코드 패턴(GM...)만 강력하게 추출
                // 예: .../8-seconds/GM0025103079299/good... -> GM0025103079299 추출
                // SSF샵 상품코드는 보통 GM, GP, GQ 등으로 시작하는 긴 문자열입니다.
                const idMatch = onclickText.match(/\/([A-Z0-9]{10,})\/good/);
                
                if (idMatch) {
                    const extractedId = idMatch[1];
                    // 현재 상품 ID가 아니면 수집 목록에 추가
                    if (extractedId !== result.goodsNo) {
                        otherColorIds.push(extractedId);
                    }
                }
            });
            // 중복 제거
            result.other_color_ids = [...new Set(otherColorIds)];


            // ---------------------------------------------------------
            // [4] 사이즈별 재고 상세 파싱 (배송방법 제외)
            // ---------------------------------------------------------
            const sizeStockInfo = [];
            
            //'사이즈'라는 제목을 가진 .row 영역만 특정해서 찾음
            const rows = Array.from(document.querySelectorAll('.gods-option .row'));
            let sizeRow = null;
            
            for (const row of rows) {
                const title = row.querySelector('.tit')?.innerText.trim();
                if (title === '사이즈') {
                    sizeRow = row;
                    break; 
                }
            }

            if (sizeRow) {
                // '사이즈' 영역 안의 li만 가져옴
                const sizeItems = sizeRow.querySelectorAll('.rdo_group li');
                
                sizeItems.forEach(li => {
                    const sizeName = li.querySelector('label')?.innerText.trim();
                    const inputId = li.querySelector('input')?.id; 
                    
                    if (sizeName && inputId) {
                        // hidden input ID 유추 (ra_ -> sizeItmNo)
                        // 예: ra_IT202510206874710 -> sizeItmNoIT202510206874710
                        const hiddenInputId = inputId.replace('ra_', 'sizeItmNo');
                        const hiddenInput = document.getElementById(hiddenInputId);
                        
                        let stockQty = 0;
                        let isSoldOut = false;

                        if (hiddenInput) {
                            // hidden 태그의 속성값에서 정보 추출
                            stockQty = parseInt(hiddenInput.getAttribute('onlineusefulinvqty') || "0");
                            const statCd = hiddenInput.getAttribute('itmstatcd'); 
                            
                            // 재고 0이거나 품절상태코드면 True
                            if (stockQty <= 0 || statCd === 'SLDOUT') isSoldOut = true;
                        }

                        // li 태그 자체에 disabled 클래스가 있거나 숨겨져 있으면 품절
                        if (li.classList.contains('disabled') || li.style.display === 'none') {
                            isSoldOut = true;
                        }

                        sizeStockInfo.push({
                            size: sizeName,
                            stock_qty: stockQty,
                            is_sold_out: isSoldOut
                        });
                    }
                });
            }
            result.size_stock = sizeStockInfo;


            // [5] 이미지 리스트
            const thumbImgs = Array.from(document.querySelectorAll('.preview-thumb .thumb-item'));
            const detailImgs = Array.from(document.querySelectorAll('.gods-detail-img img'));
            const contentImgs = Array.from(document.querySelectorAll('.gods-tab-view img'));
            
            const allImages = [...thumbImgs.map(el => el.getAttribute('data')), 
                               ...detailImgs.map(img => img.getAttribute('data-original') || img.src),
                               ...contentImgs.map(img => img.getAttribute('data-original') || img.src)];
            
            result.goodsImages = [...new Set(allImages)]
                .filter(url => url && !url.includes('noImg'))
                .map(url => {
                    if (url.startsWith('//')) return 'https:' + url;
                    if (url.startsWith('/')) return 'https://img.ssfshop.com' + url;
                    return url;
                });

            // [6] 상세 스펙 테이블
            const specInfo = {};
            document.querySelectorAll('.tbl-info tbody tr').forEach(row => {
                const key = row.querySelector('th')?.innerText.trim();
                const val = row.querySelector('td')?.innerText.trim().replace(/\\n/g, ' ');
                if(key && val) specInfo[key] = val;
            });
            result.goodsMaterial = specInfo;

            // [7] 실측 사이즈표
            const sizeChart = [];
            const sizeTable = document.querySelector('.brand-size table');
            if (sizeTable) {
                const headers = Array.from(sizeTable.querySelectorAll('thead th')).map(th => th.innerText.trim());
                const rows = sizeTable.querySelectorAll('tbody tr');
                rows.forEach(row => {
                    const rowData = {};
                    const cells = Array.from(row.querySelectorAll('td'));
                    if(cells.length > 0) rowData['SIZE'] = cells[0].innerText.trim();
                    for(let i=1; i<cells.length; i++) {
                        const key = headers[i] || `col_${i}`; 
                        rowData[key] = cells[i].innerText.trim();
                    }
                    sizeChart.push(rowData);
                });
            }
            result.sizeChart = sizeChart;

            return result;
        }""")
        
        data['url'] = page.url
        data['scraped_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return data

    except Exception as e:
        print(f"   ❌ DOM 파싱 실패: {str(e)[:100]}")
        return None

async def process_product(product_id, gender, category, context, collected_data):
    # 1. 이미 수집한 상품(ID)이면 즉시 종료 (무한 루프 방지)
    if product_id in visited_products:
        return
    
    # 방문 도장 찍기
    visited_products.add(product_id)

    async with sem:
        url = f"https://www.ssfshop.com/8-seconds/{product_id}/good?brandShopNo=BDMA07A01&brndShopId=8SBSS"
        p_page = await context.new_page()
        try:
            print(f"   🔎 {product_id} 접속 중...")
            await p_page.goto(url, timeout=60000, wait_until="load")
            await asyncio.sleep(2) 
            
            product_dict = await extract_product_data_from_dom(p_page)
            
            if product_dict:
                # 결과 리스트에 추가
                collected_data.append({
                    "gender": gender,
                    "category": category,
                    "product_id": product_id,
                    "raw_json": json.dumps(product_dict, ensure_ascii=False)
                })
                
                # 로그 출력 (품절 여부 포함)
                status = "🚫품절" if product_dict.get('is_sold_out') else "🟢판매중"
                print(f"   ✅ {product_id} 수집 완료 [{status}]")

                # [재귀 호출] 다른 색상 상품들이 발견되면 수집 목록에 추가
                other_ids = product_dict.get('other_color_ids', [])
                if other_ids:
                    print(f"      ↪️ 다른 색상 {len(other_ids)}개 발견! 추가 수집 시작...")
                    # 현재 페이지 닫고 다른 색상 수집하러 감
                    await p_page.close()
                    
                    # 발견된 다른 색상 ID들에 대해 재귀적으로 process_product 호출
                    tasks = [process_product(oid, gender, category, context, collected_data) for oid in other_ids]
                    await asyncio.gather(*tasks)
                    return # 재귀 호출 끝나면 함수 종료

            else:
                print(f"   ⚠️ {product_id} 데이터 추출 실패")
                
        except Exception as e:
            print(f"   ❌ {product_id} 에러: {str(e)[:50]}")
        finally:
            if not p_page.is_closed():
                await p_page.close()

async def crawl_category(gender, category_name, target_url, context, collected_data):
    print(f"\n>>> 🎯 [{gender}-{category_name}] 목록 수집 시작")
    page = await context.new_page()
    product_codes = set()
    try:
        await page.goto(target_url, timeout=60000)
        for _ in range(5):
            await page.evaluate("window.scrollBy(0, 3000)")
            await asyncio.sleep(1)
        
        codes = await page.evaluate("""() => 
            Array.from(document.querySelectorAll('li.god-item'))
            .map(item => item.getAttribute('view-godno'))
            .filter(c => c !== null)
        """)
        
        for c in codes: product_codes.add(c)
        print(f"   🔗 초기 발견 상품 수: {len(product_codes)}개")
        await page.close()
        
        # 전체 상품 수집 시작
        tasks = [process_product(code, gender, category_name, context, collected_data) for code in list(product_codes)]
        await asyncio.gather(*tasks)

    except Exception as e:
        print(f"   ❌ 목록 수집 실패: {e}")
        await page.close()

async def run():
    print(f"--- [START] {BRAND_NAME} 스마트 수집 (색상추적/품절확인) ---")
    collected_data = [] 
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True) 
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={'width': 1920, 'height': 1080}
        )
        
        for gender, categories in TARGET_MAP.items():
            for category, urls in categories.items():
                for url in urls:
                    await crawl_category(gender, category, url, context, collected_data)
        
        await browser.close()

    # Spark 저장
    if collected_data:
        print(f"\n📦 총 {len(collected_data)}건 수집 완료 (색상 포함). Spark 가공 시작...")
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
            count = 0
            for file in json_files:
                with open(file, 'r', encoding='utf-8') as f:
                    for line in f:
                        row = json.loads(line)
                        raw_data = json.loads(row['raw_json'])
                        
                        filename = f"{BRAND_NAME}_{row['gender'].lower()}_{row['category'].lower()}_{row['product_id']}.json"
                        with open(os.path.join(LOCAL_OUTPUT_PATH, filename), 'w', encoding='utf-8') as out_f:
                            json.dump(raw_data, out_f, ensure_ascii=False, indent=4)
                        count += 1
            
            print(f"\n✨ {count}개의 파일이 '{LOCAL_OUTPUT_PATH}'에 저장되었습니다.")
            
        finally:
            spark.stop()
    else:
        print("\n❌ 수집된 데이터가 없습니다.")

if __name__ == "__main__":
    asyncio.run(run())