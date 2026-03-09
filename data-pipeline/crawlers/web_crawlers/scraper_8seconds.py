import os
import sys
import asyncio
import json
import shutil
from datetime import datetime
from playwright.async_api import async_playwright
from hdfs import InsecureClient

# --- [설정] ---
BRAND_NAME = "8seconds"

if len(sys.argv) > 1:
    TODAY_STR = sys.argv[1]
else:
    TODAY_STR = datetime.now().strftime('%Y%m%d')

LOCAL_TEMP_DIR = f"data/{BRAND_NAME}/{TODAY_STR}"
HDFS_ROOT_PATH = f"/raw/{BRAND_NAME}/{TODAY_STR}"

HDFS_URL = "http://namenode:9870" 

TARGET_MAP = {
    "Men": {
        "Outer": [
            "https://www.ssfshop.com/8seconds/Coats/list?dspCtgryNo=SFMA42A05A02&brandShopNo=BDMA07A01&brndShopId=8SBSS"
        ]
    }
}
visited_products = set()
sem = asyncio.Semaphore(3)

async def extract_product_data_from_dom(page):
    try:
        await page.wait_for_selector('.gods-name', timeout=10000)
        await asyncio.sleep(1.5) 
        
        data = await page.evaluate("""() => {
            const result = {};
            
            // --- 1. 기본 정보 ---
            result.goodsNo = window._godNo || "";
            if (!result.goodsNo) {
                const urlMatch = location.href.match(/\/([A-Z0-9]+)\/good/);
                result.goodsNo = urlMatch ? urlMatch[1] : "";
            }
            result.goodsNm = document.querySelector('#goodDtlTitle')?.innerText.trim() || document.title;
            
            const soldOutDiv = document.querySelector('#restockSoldOut');
            const isSoldOutDivVisible = soldOutDiv && soldOutDiv.style.display !== 'none';
            const buyBtn = document.querySelector('.submit .btn.order');
            const isBtnDisabled = buyBtn && buyBtn.classList.contains('disabled');
            result.is_sold_out = isSoldOutDivVisible || isBtnDisabled;
            
            const priceTxt = document.querySelector('#godPrice')?.innerText || "0";
            result.price = parseInt(priceTxt.replace(/[^0-9]/g, ''));
            
            // --- 2. 연관 컬러 ---
            const otherColorIds = [];
            document.querySelectorAll('.opt-select.color-thumbs label').forEach(label => {
                const onclickText = label.getAttribute('onclick') || "";
                const idMatch = onclickText.match(/\/([A-Z0-9]{10,})\/good/);
                if (idMatch && idMatch[1] !== result.goodsNo) {
                    otherColorIds.push(idMatch[1]);
                }
            });
            result.other_color_ids = [...new Set(otherColorIds)];

            // --- 3. 사이즈 및 재고 ---
            const sizeStockInfo = [];
            const rows = Array.from(document.querySelectorAll('.gods-option .row'));
            let sizeRow = rows.find(row => row.querySelector('.tit')?.innerText.trim() === '사이즈');

            if (sizeRow) {
                sizeRow.querySelectorAll('.rdo_group li').forEach(li => {
                    const sizeName = li.querySelector('label')?.innerText.trim();
                    const inputId = li.querySelector('input')?.id; 
                    if (sizeName && inputId) {
                        const hiddenInputId = inputId.replace('ra_', 'sizeItmNo');
                        const hiddenInput = document.getElementById(hiddenInputId);
                        let stockQty = 0, isItemSoldOut = false;
                        if (hiddenInput) {
                            stockQty = parseInt(hiddenInput.getAttribute('onlineusefulinvqty') || "0");
                            if (stockQty <= 0 || hiddenInput.getAttribute('itmstatcd') === 'SLDOUT') isItemSoldOut = true;
                        }
                        if (li.classList.contains('disabled')) isItemSoldOut = true;
                        sizeStockInfo.push({ size: sizeName, stock_qty: stockQty, is_sold_out: isItemSoldOut });
                    }
                });
            }
            result.size_stock = sizeStockInfo;

            // --- 4. 상세 이미지 (goodsImages) 복구 ---
            const images = [];
            document.querySelectorAll('img').forEach(img => {
                let src = img.getAttribute('src') || img.getAttribute('data-src');
                if (src && src.includes('ssfshop.com') && !src.includes('blank')) {
                    if (src.startsWith('//')) src = 'https:' + src;
                    images.push(src);
                    
                    if(src.includes('THNAIL')) {
                        const highRes = src.replace('https://img.ssfshop.com/', 'https://img.ssfshop.com/cmd/RB_750x/src/https://img.ssfshop.com/');
                        images.push(highRes);
                    }
                }
            });
            result.goodsImages = [...new Set(images)];

            // --- 5. 상세 스펙 및 정보 (goodsMaterial) 복구 ---
            const material = {};
            document.querySelectorAll('table tbody tr').forEach(tr => {
                const th = tr.querySelector('th');
                const td = tr.querySelector('td');
                if (th && td && th.innerText.trim()) {
                    material[th.innerText.trim()] = td.innerText.trim().replace(/\\n/g, ' ');
                }
            });
            document.querySelectorAll('dl').forEach(dl => {
                const dt = dl.querySelector('dt');
                const dd = dl.querySelector('dd');
                if (dt && dd && dt.innerText.trim()) {
                    material[dt.innerText.trim()] = dd.innerText.trim().replace(/\\n/g, ' ');
                }
            });
            result.goodsMaterial = material;

            // --- 6. 사이즈 표 (sizeChart) 복구 ---
            let sizeChart = [];
            const tables = document.querySelectorAll('table');
            const targetTable = Array.from(tables).find(t => t.innerText.includes('가슴둘레') || t.innerText.includes('신체사이즈') || t.innerText.includes('총장'));
            
            if (targetTable) {
                const headers = Array.from(targetTable.querySelectorAll('thead th, tr:first-child th, tr:first-child td')).map(el => el.innerText.trim());
                const dataRows = Array.from(targetTable.querySelectorAll('tbody tr'));
                
                dataRows.forEach((tr, index) => {
                    if (index === 0 && tr.querySelector('th') && !tr.querySelector('td')) return;
                    const cells = Array.from(tr.querySelectorAll('td, th'));
                    if (cells.length > 0 && !tr.querySelector('td[colspan]')) {
                        const rowObj = {};
                        cells.forEach((cell, idx) => {
                            const key = headers[idx] || `Column_${idx}`;
                            rowObj[key] = cell.innerText.trim();
                        });
                        sizeChart.push(rowObj);
                    }
                });
            }
            result.sizeChart = sizeChart;

            return result;
        }""")
        
        data['url'] = page.url
        data['scraped_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return data
    except Exception as e:
        print(f"        ⚠️ 데이터 추출 중 에러: {str(e).splitlines()[0][:50]}")
        return None

async def process_product(product_id, gender, category, context):
    if product_id in visited_products: return []
    visited_products.add(product_id)

    new_found_ids = []
    async with sem:
        url = f"https://www.ssfshop.com/8-seconds/{product_id}/good?brandShopNo=BDMA07A01&brndShopId=8SBSS"
        p_page = await context.new_page()
        try:
            print(f"    🔍 [접속 시도 중...] {product_id}")
            await p_page.goto(url, timeout=30000, wait_until="domcontentloaded")
            product_dict = await extract_product_data_from_dom(p_page)
            
            if product_dict:
                if not os.path.exists(LOCAL_TEMP_DIR):
                    os.makedirs(LOCAL_TEMP_DIR)
                    
                filename = f"{BRAND_NAME}_{gender.lower()}_{category.lower()}_{product_id}.json"
                filepath = os.path.join(LOCAL_TEMP_DIR, filename)
                
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(product_dict, f, ensure_ascii=False, indent=4)
                    
                print(f"    ✅ [수집/로컬저장 완료] {filename}")
                
                new_found_ids = product_dict.get('other_color_ids', [])
                    
        except Exception as e:
            print(f"    ⚠️ [수집 실패/스킵] {product_id} - 사유: {str(e).splitlines()[0][:50]}")
        finally:
            if not p_page.is_closed(): await p_page.close()
            
    return new_found_ids

async def crawl_category(gender, category_name, target_url, context):
    print(f"\n>>> 🎯 [{gender}-{category_name}] 시작")
    page = await context.new_page()
    try:
        await page.goto(target_url, timeout=30000, wait_until="domcontentloaded")
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, 3000)")
            await asyncio.sleep(1)
        
        codes = await page.evaluate("""() => 
            Array.from(document.querySelectorAll('li.god-item')).map(item => item.getAttribute('view-godno')).filter(c => c !== null)
        """)
        await page.close()
        
        pending_codes = list(set(codes))
        while pending_codes:
            tasks = [process_product(code, gender, category_name, context) for code in pending_codes]
            results = await asyncio.gather(*tasks)
            
            new_codes = []
            for res in results:
                if res: new_codes.extend(res)
            
            pending_codes = [c for c in set(new_codes) if c not in visited_products]
            
    except Exception as e:
        print(f"    ❌ 목록 수집 실패: {str(e).splitlines()[0][:50]}")
        if not page.is_closed(): await page.close()

async def run():
    print(f"--- [START] {BRAND_NAME} 크롤링 시작 ---")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True) 
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        for gender, categories in TARGET_MAP.items():
            for category, urls in categories.items():
                for url in urls:
                    await crawl_category(gender, category, url, context)
        await browser.close()

    if os.path.exists(LOCAL_TEMP_DIR) and os.listdir(LOCAL_TEMP_DIR):
        print(f"\n📦 수집 완료. HDFS({HDFS_ROOT_PATH}) 업로드 시작...")
        try:
            hdfs_client = InsecureClient(HDFS_URL, user='root')
            
            # 폴더 생성
            hdfs_client.makedirs(HDFS_ROOT_PATH)
            
            # JSON 파일 하나씩 업로드
            upload_count = 0
            for filename in os.listdir(LOCAL_TEMP_DIR):
                if filename.endswith(".json"):
                    local_file = os.path.join(LOCAL_TEMP_DIR, filename)
                    hdfs_file = f"{HDFS_ROOT_PATH}/{filename}"
                    hdfs_client.upload(hdfs_file, local_file, overwrite=True)
                    upload_count += 1
                    
            print(f"✅ HDFS 업로드 성공! (총 {upload_count}개 파일) -> {HDFS_ROOT_PATH}")
            
        except Exception as e:
            print(f"❌ HDFS 업로드 에러: {e}")
    else:
        print("\n❌ 저장된 데이터가 없습니다.")

if __name__ == "__main__":
    asyncio.run(run())
