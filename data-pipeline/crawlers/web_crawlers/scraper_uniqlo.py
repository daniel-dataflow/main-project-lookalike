import os
import asyncio
import sys
import re
import json
from datetime import datetime
from playwright.async_api import async_playwright
from hdfs import InsecureClient

# --- [설정] ---
BRAND_NAME = "uniqlo"

if len(sys.argv) > 1:
    TODAY_STR = sys.argv[1]
else:
    TODAY_STR = datetime.now().strftime('%Y%m%d')

LOCAL_SAVE_DIR = f"data/{BRAND_NAME}/{TODAY_STR}"

HDFS_NAMENODE_URL = "http://namenode-main:9870"
HDFS_USER = "root" 
HDFS_ROOT_PATH = f"/raw/{BRAND_NAME}/{TODAY_STR}"

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
sem = asyncio.Semaphore(3)

async def extract_product_base_data(page, product_id):
    """기본 정보, 가격, 옵션, 소재 정보 등 추출 (이미지 제외)"""
    try:
        await page.mouse.wheel(0, 1000)
        await asyncio.sleep(1.0)
        
        await page.evaluate("""() => {
            document.querySelectorAll('.rah-static, [aria-hidden="true"]').forEach(el => {
                el.style.display = 'block'; el.style.height = 'auto'; el.style.visibility = 'visible';
            });
            document.querySelectorAll('button').forEach(btn => {
                if(btn.innerText.includes('상세 설명') || btn.innerText.includes('소재 정보')) btn.click();
            });
        }""")
        await asyncio.sleep(0.5)

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
            if (price === 0) {
                const ariaPrice = document.querySelector('.fr-ec-price');
                if (ariaPrice) {
                    const match = ariaPrice.getAttribute('aria-label').match(/([0-9,]+)원/);
                    if (match) price = parseInt(match[1].replace(/,/g, ''));
                }
            }
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

            const detailedInfo = { "description": "", "material_info": {} };
            const descEl = document.getElementById('productLongDescription-content');
            if (descEl) {
                detailedInfo['description'] = descEl.innerText.replace(/\\n+/g, ' ').trim();
            }
            result.goodsMaterial = detailedInfo;

            return result;
        }""")
        
        if not data or not data.get('goodsNm') or "Notice" in data.get('goodsNm', ''): 
            return None
        return data
    except Exception:
        return None

async def extract_current_images(page):
    """현재 화면에 렌더링된 이미지만 수집"""
    return await page.evaluate("""() => {
        const images = [];
        const galleryImgs = document.querySelectorAll('.media-gallery--grid img');
        
        galleryImgs.forEach(img => {
            let src = img.getAttribute('data-src') || img.src;
            if (src && src.includes('uniqlo.com')) {
                src = src.split('?')[0];
                images.push(src);
            }
        });
        
        if (images.length === 0) {
            document.querySelectorAll('img').forEach(img => {
                let src = img.src;
                if (src && (src.includes('/goods/') || src.includes('/item/') || src.includes('/sub/')) && !src.includes('/chip/')) {
                    src = src.split('?')[0];
                    images.push(src);
                }
            });
        }
        return [...new Set(images)];
    }""")

async def process_product(product_id, gender, category, context):
    if product_id in visited_products: return
    visited_products.add(product_id)

    clean_id = product_id.split('?')[0]
    
    if os.path.exists(LOCAL_SAVE_DIR):
        existing_files = os.listdir(LOCAL_SAVE_DIR)
        if any(clean_id in f for f in existing_files):
            print(f"   ⏩ {clean_id} 이미 수집됨 (스킵)")
            return

    async with sem:
        url = f"https://www.uniqlo.com/kr/ko/products/{clean_id}"
        max_retries = 3
        
        for attempt in range(max_retries):
            p_page = await context.new_page()
            
            # 리소스 최적화
            await p_page.route("**/*review*", lambda route: route.abort())
            await p_page.route("**/*recommend*", lambda route: route.abort())
            
            try:
                if attempt == 0:
                    print(f"   🔎 {clean_id} 접속 중...")
                    
                await p_page.goto(url, timeout=60000, wait_until="domcontentloaded")
                
                # 1. 공통 데이터 추출
                base_data = None
                for _ in range(2):
                    base_data = await extract_product_base_data(p_page, clean_id)
                    if base_data: break
                    await asyncio.sleep(1)

                if base_data:
                    os.makedirs(LOCAL_SAVE_DIR, exist_ok=True)
                    colors = base_data.get('colors', [])
                    
                    # 2. 색상이 여러 개면 하나씩 클릭하며 이미지 추출
                    if colors:
                        print(f"   🎨 {clean_id} 색상 {len(colors)}개 발견. 개별 스캔 중...")
                        for color in colors:
                            color_code = color['color_code']
                            
                            # 색상 버튼 클릭
                            await p_page.evaluate(f"""(code) => {{
                                const btns = document.querySelectorAll('.collection-list-horizontal button.chip, .color-chip button');
                                for (let btn of btns) {{
                                    if (btn.value === code || btn.id.includes(code)) {{
                                        btn.click();
                                        break;
                                    }}
                                }}
                            }}""", color_code)

                            await asyncio.sleep(0.8) 
                            
                            current_images = await extract_current_images(p_page)
                            
                            # 최종 데이터 조립
                            final_data = base_data.copy()
                            final_data['color_name'] = color['color_name'] 
                            final_data['goodsNo'] = f"{clean_id}_{color_code}" 
                            final_data['url'] = f"{url}?colorDisplayCode={color_code}"
                            final_data['goodsImages'] = current_images
                            final_data['scraped_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            if color['icon_url']: 
                                final_data['thumbnailImageUrl'] = color['icon_url']
                            
                            filename = f"{BRAND_NAME}_{gender.lower()}_{category.lower()}_{final_data['goodsNo']}.json"
                            filepath = os.path.join(LOCAL_SAVE_DIR, filename)
                            
                            with open(filepath, 'w', encoding='utf-8') as f:
                                json.dump(final_data, f, ensure_ascii=False, indent=4)
                    
                    # 색상이 단일인 경우
                    else:
                        base_data['goodsImages'] = await extract_current_images(p_page)
                        base_data['scraped_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        filename = f"{BRAND_NAME}_{gender.lower()}_{category.lower()}_{clean_id}.json"
                        filepath = os.path.join(LOCAL_SAVE_DIR, filename)
                        
                        with open(filepath, 'w', encoding='utf-8') as f:
                            json.dump(base_data, f, ensure_ascii=False, indent=4)
                    
                    print(f"   ✅ [저장 완료] {clean_id} 및 파생 옵션")
                    break # 성공 시 재시도 루프 탈출
                
                else:
                    print(f"   ⚠️ {clean_id} 데이터 추출 실패 (페이지 없음/로딩 오류)")
                    break # 구조적 문제면 탈출

            except Exception as e:
                error_msg = str(e)
                if "ERR_NAME_NOT_RESOLVED" in error_msg or "Timeout" in error_msg:
                    print(f"   ⏳ {clean_id} 네트워크 지연 ({attempt+1}/{max_retries}). 5초 대기 후 재시도...")
                    await asyncio.sleep(5.0)
                else:
                    print(f"   ❌ {clean_id} 치명적 에러: {error_msg[:50]}")
                    break
            finally:
                if not p_page.is_closed(): await p_page.close()
                
        await asyncio.sleep(1.0)

async def crawl_category(gender, category_name, target_url, context):
    print(f"\n>>> 🎯 [{gender}-{category_name}] 목록 수집 시작")
    page = await context.new_page()
    product_ids = set()
    
    try:
        await page.goto(target_url, timeout=60000, wait_until="domcontentloaded")
        
        last_height = await page.evaluate("document.body.scrollHeight")
        for _ in range(15): 
            await page.evaluate("window.scrollBy(0, 1000)")
            await asyncio.sleep(0.8)
            new_height = await page.evaluate("document.body.scrollHeight")
            if new_height == last_height:
                break # 더 이상 늘어나지 않으면 스크롤 중지
            last_height = new_height
        
        content = await page.content()
        matches = re.findall(r"/products/([A-Z0-9-]+)", content)
        for pid in matches:
            if len(pid) >= 5 and "review" not in pid:
                product_ids.add(pid)
        
        print(f"   💡 총 {len(product_ids)}개 상품 발견 완료")
        await page.close()
        
        if product_ids:
            tasks = [process_product(pid, gender, category_name, context) for pid in list(product_ids)]
            await asyncio.gather(*tasks)

    except Exception as e:
        print(f"   ❌ 목록 수집 실패: {e}")
        if not page.is_closed(): await page.close()

async def run():
    print(f"--- [START] {BRAND_NAME.upper()} 크롤링 시작 (로컬 저장 + HDFS 연동) ---")
    
    os.makedirs(LOCAL_SAVE_DIR, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--start-maximized"]) 
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )

        for gender, categories in TARGET_MAP.items():
            for category, urls in categories.items():
                if isinstance(urls, str): urls = [urls]
                for url in urls:
                    await crawl_category(gender, category, url, context)
        
        await browser.close()
    
    if os.path.exists(LOCAL_SAVE_DIR):
        local_files = [f for f in os.listdir(LOCAL_SAVE_DIR) if f.endswith('.json')]
        if local_files:
            print(f"\n📦 로컬 수집 완료 ({len(local_files)}건). HDFS 저장을 시작합니다...")
            try:
                client = InsecureClient(HDFS_NAMENODE_URL, user=HDFS_USER)
                
                # 디렉토리 생성 시도
                try:
                    client.makedirs(HDFS_ROOT_PATH)
                    print(f"   📁 HDFS 타겟 디렉토리 확인: {HDFS_ROOT_PATH}")
                except Exception as e:
                    print(f"   ℹ️ HDFS 디렉토리 메시지: {e}")

                saved_count = 0
                for filename in local_files:
                    local_file_path = os.path.join(LOCAL_SAVE_DIR, filename)
                    hdfs_file_path = f"{HDFS_ROOT_PATH}/{filename}"
                    
                    try:
                        # 로컬 파일을 HDFS로 업로드 (덮어쓰기 허용)
                        client.upload(hdfs_file_path, local_file_path, overwrite=True)
                        saved_count += 1
                    except Exception as e:
                        print(f"   ❌ HDFS 저장 실패 ({filename}): {e}")

                print(f"\n✨ 총 {saved_count}개 파일 HDFS 업로드 성공!")
                print(f"   👉 저장 위치: {HDFS_ROOT_PATH}")

            except Exception as e:
                print(f"\n🚨 HDFS 연결/접근 오류: {e}")
        else:
            print("\n❌ 로컬에 수집된 파일이 없어 HDFS 업로드를 생략합니다.")
    else:
        print("\n❌ 저장 디렉토리를 찾을 수 없습니다.")

if __name__ == "__main__":
    asyncio.run(run())
