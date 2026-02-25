import os
import asyncio
import re
import json
import shutil
from datetime import datetime
from playwright.async_api import async_playwright
# WebHDFS 라이브러리 추가
from hdfs import InsecureClient

# 설정
BRAND_NAME = "uniqlo"
TODAY_STR = datetime.now().strftime('%Y%m%d')

LOCAL_TEMP_DIR = f"data/{BRAND_NAME}/{TODAY_STR}"
HDFS_ROOT_PATH = f"/raw/{BRAND_NAME}/{TODAY_STR}"

# WebHDFS 주소 설정 (포트 9870 사용)
HDFS_WEB_URL = "http://namenode-main:9870"
HDFS_USER = "root"

TARGET_MAP = {
    "Men": {
        "Outer": [
            "https://www.uniqlo.com/kr/ko/men/outerwear"
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
        
        # 아코디언 개방
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
                src = src.split('?')[0]; // 고해상도 원본 유지를 위해 쿼리 제거
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

    async with sem:
        clean_id = product_id.split('?')[0]
        url = f"https://www.uniqlo.com/kr/ko/products/{clean_id}"
        
        p_page = await context.new_page()
        try:
            print(f"   🔎 {clean_id} 접속 중...")
            await p_page.goto(url, timeout=60000, wait_until="domcontentloaded")
            
            # 1. 공통 데이터 추출
            base_data = None
            for _ in range(2):
                base_data = await extract_product_base_data(p_page, clean_id)
                if base_data: break
                await asyncio.sleep(1)

            if base_data:
                if not os.path.exists(LOCAL_TEMP_DIR): os.makedirs(LOCAL_TEMP_DIR)
                colors = base_data.get('colors', [])
                
                # 2. 색상이 여러 개면 하나씩 클릭하며 이미지 추출
                if colors:
                    print(f"   🎨 {clean_id} 색상 {len(colors)}개 발견. 개별 이미지 스캔 중...")
                    for color in colors:
                        color_code = color['color_code']
                        
                        # 화면에서 해당 색상 버튼 클릭
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
                        
                        # 현재 바뀐 화면의 이미지 긁어오기
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
                        filepath = os.path.join(LOCAL_TEMP_DIR, filename)
                        
                        with open(filepath, 'w', encoding='utf-8') as f:
                            json.dump(final_data, f, ensure_ascii=False, indent=4)
                
                # 색상이 단일인 경우
                else:
                    base_data['goodsImages'] = await extract_current_images(p_page)
                    base_data['scraped_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    filename = f"{BRAND_NAME}_{gender.lower()}_{category.lower()}_{clean_id}.json"
                    filepath = os.path.join(LOCAL_TEMP_DIR, filename)
                    
                    with open(filepath, 'w', encoding='utf-8') as f:
                        json.dump(base_data, f, ensure_ascii=False, indent=4)
                    
                print(f"   ✅ [수집 완료] {clean_id}")
            else:
                print(f"   ⚠️ {clean_id} 데이터 추출 실패")

        except Exception as e:
            print(f"   ❌ {clean_id} 에러: {str(e)[:50]}")
        finally:
            await p_page.close()

async def crawl_category(gender, category_name, target_url, context):
    print(f"\n>>> 🎯 [{gender}-{category_name}] 목록 수집 시작")
    page = await context.new_page()
    product_ids = set()
    
    try:
        await page.goto(target_url, timeout=60000, wait_until="domcontentloaded")
        for _ in range(5):
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            await asyncio.sleep(1.0)
        
        content = await page.content()
        matches = re.findall(r"/products/([A-Z0-9-]+)", content)
        for pid in matches:
            if len(pid) >= 5 and "review" not in pid:
                product_ids.add(pid)
        
        await page.close()
        tasks = [process_product(pid, gender, category_name, context) for pid in list(product_ids)]
        await asyncio.gather(*tasks)

    except Exception as e:
        print(f"   ❌ 목록 수집 실패: {e}")
        if not page.is_closed(): await page.close()

async def run():
    print(f"--- [START] UNIQLO 크롤링 시작 ---")
    
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

    # --- HDFS 업로드 로직 (WebHDFS 방식) ---
    if os.path.exists(LOCAL_TEMP_DIR) and os.listdir(LOCAL_TEMP_DIR):
        print(f"\n📦 크롤링 완료! WebHDFS({HDFS_WEB_URL}{HDFS_ROOT_PATH})를 통해 업로드 시작...")
        
        try:
            # 1. HDFS 클라이언트 연결
            client = InsecureClient(HDFS_WEB_URL, user=HDFS_USER)
            
            # 2. HDFS 내 타겟 디렉토리 생성 (없으면 자동 생성됨)
            client.makedirs(HDFS_ROOT_PATH)
            print(f"   📁 HDFS 폴더 생성/확인 완료: {HDFS_ROOT_PATH}")
            
            # 3. 로컬 폴더의 모든 JSON 파일을 HDFS로 업로드
            success_count = 0
            for filename in os.listdir(LOCAL_TEMP_DIR):
                if filename.endswith(".json"):
                    local_file = os.path.join(LOCAL_TEMP_DIR, filename)
                    hdfs_file = f"{HDFS_ROOT_PATH}/{filename}"
                    
                    # 파일 업로드 (덮어쓰기 허용)
                    client.upload(hdfs_file, local_file, overwrite=True)
                    success_count += 1
                    
            print(f"✅ HDFS 업로드 성공! (총 {success_count}개 파일 전송 완료)")
            
            # 4. (선택) 업로드 성공 후 로컬 임시 폴더 삭제
            # shutil.rmtree(LOCAL_TEMP_DIR)
            # print(f"🧹 로컬 임시 폴더 삭제 완료")
            
        except Exception as e:
            print(f"❌ HDFS 업로드 에러: WebHDFS 연결 또는 전송에 실패했습니다. 에러 내용: {e}")
    else:
        print("\n❌ 저장된 데이터가 없습니다.")

if __name__ == "__main__":
    asyncio.run(run())