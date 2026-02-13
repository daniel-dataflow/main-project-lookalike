import asyncio
import re
import json
from datetime import datetime
from playwright.async_api import async_playwright
from hdfs import InsecureClient

# --- 설정 ---
BRAND_NAME = "zara"

# HDFS 설정
HDFS_NAMENODE_URL = "http://localhost:9870"  # WebHDFS 포트 (9870 or 50070)
HDFS_USER = "hadoop"  # HDFS 유저명

# 수집 대상 URL
TARGET_MAP = {
    "Men": {
        "Outer": [
            "https://www.zara.com/kr/ko/man-blazers-l608.html?v1=2436311"
        ]
    }
}

visited_products = set()
sem = asyncio.Semaphore(3)

async def extract_product_data_from_dom(page):
    """DOM 데이터 추출 로직 (이전과 동일)"""
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
            result.goodsNo = urlMatch ? urlMatch[1] : location.href.split('?')[0].split('-').pop(); 
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
            await p_page.set_extra_http_headers({"Referer": "https://www.google.com/", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"})
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
                print(f"   ✅ {product_id} 수집됨")
            else:
                print(f"   ⚠️ {product_id} 실패")
        except Exception as e:
            print(f"   ❌ {product_id} 에러: {str(e)[:50]}")
        finally:
            await p_page.close()

async def crawl_category(gender, category_name, target_url, context, collected_data):
    print(f"\n>>> 🎯 [{gender}-{category_name}] 목록 수집 시작")
    page = await context.new_page()
    product_map = {} 
    try:
        await page.goto(target_url, timeout=90000, wait_until="domcontentloaded")
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
        await page.close()

async def run():
    print(f"--- [START] ZARA HDFS 수집기 ---")
    collected_data = [] 
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--start-maximized", "--disable-blink-features=AutomationControlled"]) 
        context = await browser.new_context(viewport={"width": 1920, "height": 1080}, locale="ko-KR")
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        for gender, categories in TARGET_MAP.items():
            for category, urls in categories.items():
                if isinstance(urls, str): urls = [urls]
                for url in urls:
                    await crawl_category(gender, category, url, context, collected_data)
        await browser.close()

    # --- HDFS 저장 로직 (경로 수정됨) ---
    if collected_data:
        print(f"\n📦 {len(collected_data)}건 수집 완료. HDFS 업로드 시작...")
        
        try:
            client = InsecureClient(HDFS_NAMENODE_URL, user=HDFS_USER)
            
            # [수정됨] 날짜 기반 동적 경로 생성
            # 예: /raw/zara/2024-05-21
            today_date = datetime.now().strftime('%Y-%m-%d')
            target_dir = f"/raw/{BRAND_NAME}/{today_date}"
            
            # 디렉토리 생성 (있으면 무시, 없으면 생성)
            try:
                client.makedirs(target_dir)
                print(f"   📁 타겟 디렉토리 확인: {target_dir}")
            except Exception as e:
                # 이미 존재하거나 권한 문제일 수 있음
                print(f"   ℹ️ 디렉토리 생성 메시지: {e}")

            saved_count = 0
            for item in collected_data:
                try:
                    filename = f"{BRAND_NAME}_{item['gender'].lower()}_{item['category'].lower()}_{item['product_id']}.json"
                    hdfs_file_path = f"{target_dir}/{filename}"
                    
                    final_data = item['data']
                    
                    # HDFS 쓰기
                    with client.write(hdfs_file_path, encoding='utf-8', overwrite=True) as writer:
                        json.dump(final_data, writer, ensure_ascii=False, indent=4)
                    
                    saved_count += 1
                except Exception as e:
                    print(f"   ❌ 업로드 실패 ({item.get('product_id')}): {e}")

            print(f"\n✨ 총 {saved_count}개 파일 업로드 완료")
            print(f"   👉 경로: {target_dir}")

        except Exception as e:
            print(f"\n🚨 HDFS 오류: {e}")
    else:
        print("\n❌ 수집된 데이터가 없습니다.")

if __name__ == "__main__":
    asyncio.run(run())