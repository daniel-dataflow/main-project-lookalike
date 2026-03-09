import asyncio
import re
import json
import os
import sys
from datetime import datetime
from playwright.async_api import async_playwright
from hdfs import InsecureClient

# --- 설정 ---
BRAND_NAME = "zara"

if len(sys.argv) > 1:
    TODAY_STR = sys.argv[1]
else:
    TODAY_STR = datetime.now().strftime('%Y%m%d')

HDFS_NAMENODE_URL = "http://namenode-main:9870" 
HDFS_USER = "root" 

# 수집 대상 URL
TARGET_MAP = {
    "Men": {
        "Outer": [
            "https://www.zara.com/kr/ko/man-outerwear-l715.html?v1=2606109",
            "https://www.zara.com/kr/ko/man-jackets-l640.html?v1=2536906"
        ],
        "Top": [
            "https://www.zara.com/kr/ko/man-tshirts-l855.html?v1=2432042",
            "https://www.zara.com/kr/ko/man-sweatshirts-l821.html?v1=2432232"
        ],
        "Bottom": [
            "https://www.zara.com/kr/ko/man-trousers-l838.html?v1=2432096",
            "https://www.zara.com/kr/ko/man-jeans-l659.html?v1=2432131"
        ]
    },
    "Women": {
        "Outer": [
            "https://www.zara.com/kr/ko/woman-jackets-l1114.html?v1=2417772",
            "https://www.zara.com/kr/ko/woman-outerwear-l1184.html?v1=2419032"
        ],
        "Top": [
            "https://www.zara.com/kr/ko/woman-shirts-l1217.html?v1=2420369",
            "https://www.zara.com/kr/ko/woman-tshirts-l1362.html?v1=2420417"
        ],
        "Bottom": [
            "https://www.zara.com/kr/ko/woman-trousers-l1335.html?v1=2420795"
        ]
    }
}

visited_products = set()
sem = asyncio.Semaphore(3)

async def extract_product_data_from_dom(page):
    """DOM 데이터 추출 로직"""
    try:
        await page.mouse.wheel(0, 500)
        await asyncio.sleep(1.0)
        
        try:
            await page.evaluate("""() => {
                const addBtns = document.querySelectorAll('button[data-qa-action="add-to-cart"], button.product-detail-cart-buttons__button');
                for (const btn of addBtns) {
                    if (btn.innerText.includes('추가') || btn.innerText.includes('Add')) {
                        btn.click(); break;
                    }
                }
                const infoBtns = document.querySelectorAll('button');
                for (const btn of infoBtns) {
                    if (btn.innerText.includes('소재') || btn.innerText.includes('혼용률')) {
                        btn.click();
                    }
                }
            }""")
            await asyncio.sleep(1.5)
        except:
            pass

        data = await page.evaluate("""() => {
            const result = {};
            const urlMatch = location.href.match(/-p([0-9]+)\.html/);
            
            const copyBtn = document.querySelector('button.product-color-extended-name__copy-action, [data-qa-action="product-detail-info-color-copy"]');
            if (copyBtn && copyBtn.innerText.trim() !== '') {
                result.goodsNo = copyBtn.innerText.trim();
            } else {
                result.goodsNo = urlMatch 
                    ? urlMatch[1] 
                    : location.href.split('?')[0].split('-').pop();
            }
            
            result.goodsNm = document.querySelector('h1')?.innerText.trim() || document.title;
            result.brandName = "ZARA";
            result.thumbnailImageUrl = document.querySelector('meta[property="og:image"]')?.content || "";

            const colorEl = document.querySelector('.product-detail-info__color');
            result.color_name = colorEl ? (colorEl.innerText.includes('|') ? colorEl.innerText.split('|')[0].trim() : colorEl.innerText.trim()) : "";
            result.colors = []; 

            const priceEl = document.querySelector('.price__amount, .money-amount');
            result.price = priceEl ? parseInt(priceEl.innerText.replace(/[^0-9]/g, '')) : 0;

            const sizeStockInfo = [];
            document.querySelectorAll('li.product-detail-size-selector__size-list-item').forEach(li => {
                const nameEl = li.querySelector('[data-qa-qualifier="product-detail-size-selector-size-list-item-name"]');
                const name = nameEl ? nameEl.innerText.trim() : li.innerText.split('\\n')[0].trim();
                if (name) {
                    let isItemSoldOut = li.getAttribute('aria-disabled') === 'true' || li.classList.contains('disabled') || li.innerText.includes('Coming soon') || li.innerText.includes('품절');
                    sizeStockInfo.push({ size: name, is_sold_out: isItemSoldOut, stock_qty: isItemSoldOut ? 0 : 999 });
                }
            });
            result.size_stock = sizeStockInfo;
            
            let isSoldOut = sizeStockInfo.length > 0 ? sizeStockInfo.every(s => s.is_sold_out) : false;
            if (!isSoldOut && sizeStockInfo.length === 0) {
                const addBtn = document.querySelector('button[data-qa-action="add-to-cart"]');
                if (addBtn && (addBtn.disabled || addBtn.innerText.includes('품절'))) isSoldOut = true;
            }
            result.is_sold_out = isSoldOut;

            const images = [];
            document.querySelectorAll('img.media-image__image, .media-wrap__image').forEach(img => {
                let src = img.src;
                if (src && src.includes('static.zara.net')) images.push(src.split('?')[0]);
            });
            result.goodsImages = [...new Set(images)];

            const specInfo = {};
            const descEl = document.querySelector('.product-detail-description div.expandable-text__inner-content');
            if (descEl) specInfo['description'] = descEl.innerText.replace(/\\n+/g, ' ').trim();

            const compContainer = document.querySelector('.product-detail-composition');
            if (compContainer) {
                compContainer.querySelectorAll('.product-detail-composition__item').forEach(item => {
                    const partName = item.querySelector('.product-detail-composition__part-name')?.innerText.trim() || "소재";
                    const ingredients = [];
                    item.querySelectorAll('li').forEach(li => ingredients.push(li.innerText.trim()));
                    if (ingredients.length > 0) specInfo[partName] = ingredients.join(', ');
                });
            }
            result.goodsMaterial = specInfo;
            return result;
        }""")
        
        if not data or not data.get('goodsNm'): return None
        data['url'] = page.url
        data['scraped_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return data

    except Exception:
        return None

async def process_product(product_id, gender, category, context, collected_data, product_url):
    if product_id in visited_products: return
    visited_products.add(product_id)

    async with sem:
        p_page = await context.new_page()
        try:
            print(f"   🔎 접속 중... {product_id}")
            await p_page.set_extra_http_headers({"Referer": "https://www.google.com/"})
            await p_page.goto(product_url, timeout=60000, wait_until="domcontentloaded")
            
            product_dict = None
            for _ in range(2):
                product_dict = await extract_product_data_from_dom(p_page)
                if product_dict and product_dict.get('price', 0) > 0: break
                await asyncio.sleep(2)

            if product_dict:
                collected_data.append({
                    "gender": gender, "category": category, "product_id": product_id, "data": product_dict 
                })
                # 추출된 정확한 품번을 함께 출력하여 확인
                real_goodsNo = product_dict.get('goodsNo', 'Unknown')
                print(f"   ✅ {product_id} 수집됨 (품번: {real_goodsNo})")
            else:
                print(f"   ⚠️ {product_id} 수집 실패 (데이터 렌더링 지연)")
        except Exception as e:
            print(f"   ❌ {product_id} 에러: {str(e)[:50]}")
        finally:
            await p_page.close()

async def crawl_category(gender, category_name, target_url, context, collected_data):
    print(f"\n>>> 🎯 [{gender}-{category_name}] 목록 수집 시작")
    page = await context.new_page()
    product_map = {} 
    try:
        response = await page.goto(target_url, timeout=90000, wait_until="domcontentloaded")
        
        for _ in range(5):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)
        
        links = await page.evaluate("() => Array.from(document.querySelectorAll('a[href*=\"-p\"][href*=\".html\"]')).map(a => a.href)")
        for link in links:
            match = re.search(r'-p([0-9]+)\.html', link)
            if match:
                pid = match.group(1)
                if pid not in product_map: product_map[pid] = link.split('?')[0]
        
        print(f"   🔗 총 발견된 상품 수: {len(product_map)}개")
        await page.close()
        tasks = [process_product(pid, gender, category_name, context, collected_data, url) for pid, url in product_map.items()]
        await asyncio.gather(*tasks)
    except Exception as e:
        print(f"   ❌ 목록 수집 실패: {e}")
        if not page.is_closed(): await page.close()

async def run():
    print(f"--- [START] ZARA 하둡 수집기 (Xvfb 모드 + Stealth) ---")
    collected_data = [] 
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, # 서버 환경에 맞게 Headless True로 변경
            args=[
                "--start-maximized", 
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage"
            ]
        ) 
        
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080}, 
            locale="ko-KR",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        
        stealth_js = """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['ko-KR', 'ko', 'en-US', 'en'] });
        """
        await context.add_init_script(stealth_js)

        for gender, categories in TARGET_MAP.items():
            for category, urls in categories.items():
                if isinstance(urls, str): urls = [urls]
                for url in urls:
                    await crawl_category(gender, category, url, context, collected_data)
        await browser.close()

    # --- HDFS 파일 저장 로직 ---
    if collected_data:
        print(f"\n📦 {len(collected_data)}건 수집 완료. HDFS 저장 시작...")
        
        target_dir = f"/raw/{BRAND_NAME}/{TODAY_STR}" 
        
        try:
            client = InsecureClient(HDFS_NAMENODE_URL, user=HDFS_USER)
            
            try:
                client.makedirs(target_dir)
                print(f"   📁 HDFS 타겟 디렉토리 확인: {target_dir}")
            except Exception as e:
                print(f"   ℹ️ HDFS 디렉토리 생성 메시지(이미 존재할 수 있음): {e}")

            saved_count = 0
            for item in collected_data:
                try:
                    safe_goods_no = str(item['data'].get('goodsNo', item['product_id'])).replace('/', '-')
                    filename = f"{BRAND_NAME}_{item['gender'].lower()}_{item['category'].lower()}_{safe_goods_no}.json"
                    
                    hdfs_file_path = f"{target_dir}/{filename}"
                    final_data = item['data']
                    
                    with client.write(hdfs_file_path, encoding='utf-8', overwrite=True) as writer:
                        json.dump(final_data, writer, ensure_ascii=False, indent=4)
                    saved_count += 1
                except Exception as e:
                    print(f"   ❌ HDFS 저장 실패 ({item.get('product_id')}): {e}")

            print(f"\n✨ 총 {saved_count}개 파일 HDFS 저장 완료")
            print(f"   👉 저장 위치: {target_dir}")

        except Exception as e:
            print(f"\n🚨 HDFS 연결/저장 오류: {e}")
    else:
        print("\n❌ 수집된 데이터가 없습니다.")

if __name__ == "__main__":
    asyncio.run(run())
