import os
import asyncio
import re
import json
from datetime import datetime
from playwright.async_api import async_playwright
from hdfs import InsecureClient

# --- 설정 ---
BRAND_NAME = "musinsa"
TODAY_STR = datetime.now().strftime('%Y%m%d') 

# 로컬 저장 경로
LOCAL_OUTPUT_PATH = f"data/{BRAND_NAME}/{TODAY_STR}"

HDFS_NAMENODE_URL = "http://namenode-main:9870"  # WebHDFS 포트
HDFS_USER = "root" 
HDFS_ROOT_PATH = f"/raw/{BRAND_NAME}/{TODAY_STR}"

TARGET_MAP = {
    "Men": {
        "Outer": [
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=002003&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=002002&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=002017&gf=A", 
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=002006&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=002007&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=002009&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=002024&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=002008&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=002020&gf=A"
        ],
        "Top": [
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=001006&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=001004&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=001005&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=001010&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=001001&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=001002&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=001011&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=001003&gf=A"
        ],
        "Bottom": [
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=003002&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=003004&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=003008&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=003007&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=003009&gf=A",
            "https://www.musinsa.com/brand/musinsastandard/products?categoryCode=003006&gf=A"
        ]
    },
    "Women": {
        "Outer": [
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=002003&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=002002&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=002017&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=002014&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=002007&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=002009&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=002024&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=002008&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=002020&gf=A"
        ],
        "Top": [
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=001006&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=001004&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=001005&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=001010&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=001001&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=001002&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=001011&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=001008&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=001003&gf=A"
        ],
        "Bottom": [
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=003002&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=003004&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=003008&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=003007&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=003005&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=003009&gf=A",
            "https://www.musinsa.com/brand/musinsastandardwoman/products?categoryCode=003006&gf=A"
        ]
    }
}

visited_products = set()
sem = asyncio.Semaphore(4) 

async def extract_attribute_focus_data(page):
    try:
        await asyncio.sleep(1.0)

        data = await page.evaluate("""() => {
            const result = {};
            result.scraped_url = location.href;

            const script = document.getElementById('__NEXT_DATA__');
            if (!script) return { error: "No __NEXT_DATA__ script found" };

            const nextData = JSON.parse(script.innerText);
            const metaData = nextData?.props?.pageProps?.meta?.data || {};

            result.goodsNo = metaData.goodsNo ? String(metaData.goodsNo) : location.href.match(/\\d+/)[0];
            result.product_code = metaData.styleNo || "";
            
            let rawName = metaData.goodsNm || document.title.split(' - ')[0];
            result.goodsNm = rawName
                .replace(/^\\[?무신사\\s?스탠다드.*?\\]?\\s*/, '')
                .replace(/\\(MUSINSA STANDARD\\)\\s*/i, '')
                .replace(/^\\[?쿨탠다드.*?\\]?\\s*/, '')
                .trim();

            result.brandName = metaData.brandInfo?.brandName || "MUSINSA";
            result.price = metaData.goodsPrice?.salePrice || metaData.goodsPrice?.normalPrice || 0;

            let thumbUrl = metaData.thumbnailImageUrl || "";
            if (!thumbUrl && metaData.goodsImages && metaData.goodsImages.length > 0) {
                thumbUrl = metaData.goodsImages[0].imageUrl;
            }
            if (thumbUrl.startsWith('//')) thumbUrl = 'https:' + thumbUrl;
            else if (thumbUrl.startsWith('/')) thumbUrl = 'https://image.msscdn.net' + thumbUrl;
            
            result.thumbnailImageUrl = thumbUrl;
            result.url = result.scraped_url;

            const detailImages = [];
            if (metaData.goodsContents) {
                const imgRegex = /<img[^>]+src=["']([^"']+)["']/gi;
                let match;
                while ((match = imgRegex.exec(metaData.goodsContents)) !== null) {
                    let src = match[1];
                    if (src.startsWith('//')) src = 'https:' + src;
                    else if (src.startsWith('/')) src = 'https://image.msscdn.net' + src;
                    detailImages.push(src);
                }
            }
            result.detailImages = detailImages;

            const attributes = {};
            if (metaData.goodsMaterial && metaData.goodsMaterial.materials) {
                metaData.goodsMaterial.materials.forEach(mat => {
                    const key = mat.name; 
                    const selectedItem = mat.items.find(item => item.isSelected);
                    if (selectedItem) {
                        attributes[key] = selectedItem.name.replace(/\\|/g, ' '); 
                    }
                });
            }
            result.goodsMaterial = attributes;

            return result;
        }""")
        
        data['scraped_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return data
    except Exception as e:
        return {"error": str(e)}

async def process_product(product_id, gender, category, context):
    if product_id in visited_products: return
    visited_products.add(product_id)
    
    filename = f"{BRAND_NAME}_{gender.lower()}_{category.lower()}_{product_id}.json"
    local_file_path = os.path.join(LOCAL_OUTPUT_PATH, filename)
    
    if os.path.exists(local_file_path):
        print(f"   ⏩ {product_id} 이미 저장됨 (스킵)")
        return

    async with sem:
        url = f"https://www.musinsa.com/products/{product_id}"
        max_retries = 3 
        
        for attempt in range(max_retries):
            p_page = await context.new_page()
            
            await p_page.route("**/*review*", lambda route: route.abort())
            await p_page.route("**/*recommend*", lambda route: route.abort())
            
            try:
                if attempt == 0:
                    print(f"   🔎 {product_id} 분석 중...")
                
                await p_page.goto(url, timeout=60000, wait_until="domcontentloaded")
                raw_data = await extract_attribute_focus_data(p_page)
                
                if raw_data and not raw_data.get('error') and raw_data.get('goodsNo'):
                    with open(local_file_path, 'w', encoding='utf-8') as f:
                        json.dump(raw_data, f, ensure_ascii=False, indent=4)
                    
                    attr_str = ", ".join([f"{k}:{v}" for k, v in raw_data.get('goodsMaterial', {}).items()])
                    print(f"   ✅ [저장완료] {filename} | 속성: [{attr_str}]")
                    break
                    
                else:
                    print(f"   ⚠️ {product_id} 데이터 추출 실패 (품절 등)")
                    break
                    
            except Exception as e:
                error_msg = str(e)
                if "ERR_NAME_NOT_RESOLVED" in error_msg or "Timeout" in error_msg:
                    print(f"   ⏳ {product_id} 네트워크 지연 ({attempt+1}/{max_retries}). 5초 대기 후 재시도...")
                    await asyncio.sleep(5.0)
                else:
                    print(f"   ❌ {product_id} 치명적 에러: {error_msg[:50]}")
                    break
                    
            finally:
                if not p_page.is_closed():
                    await p_page.close()
        
        await asyncio.sleep(1.0)

async def crawl_category(gender, category, base_url, context):
    print(f"\n>>> 🎯 [{gender} - {category.upper()}] 목록 검색 시작: {base_url}")
    product_ids = set()
    page = await context.new_page()
    
    try:
        await page.goto(base_url, timeout=60000, wait_until="domcontentloaded")
        await asyncio.sleep(3.0)
        
        last_height = await page.evaluate("document.body.scrollHeight")
        no_change_count = 0
        
        while True:
            hrefs = await page.evaluate("() => Array.from(document.querySelectorAll('a')).map(a => a.href)")
            for h in hrefs:
                m = re.search(r'(?:goods|products)\/(\d+)', h)
                if m: product_ids.add(m.group(1))
            
            await page.evaluate("window.scrollBy(0, 600)")
            await asyncio.sleep(0.5)
            
            is_bottom = await page.evaluate("window.innerHeight + window.scrollY >= document.body.scrollHeight - 10")
            
            if is_bottom:
                await asyncio.sleep(1.5)
                new_height = await page.evaluate("document.body.scrollHeight")
                
                if new_height == last_height:
                    no_change_count += 1
                    if no_change_count >= 3:
                        print(f"   ⬇️ 더 이상 로딩되는 상품이 없습니다. 스크롤 종료.")
                        break
                else:
                    no_change_count = 0 
                    last_height = new_height
            else:
                no_change_count = 0

        print(f"   💡 최종 수집 대기열: {len(product_ids)}개 확인 완료. 상세 정보 추출 시작...")
        await page.close()
        
        if product_ids:
            await asyncio.gather(*[process_product(pid, gender, category, context) for pid in list(product_ids)])
            
    except Exception as e:
        print(f"   ❌ 목록 페이지 실패: {e}")
        if not page.is_closed(): await page.close()

async def run():
    print(f"--- [START] {BRAND_NAME.upper()} 크롤링 시작 (로컬 저장 + HDFS 연동) ---")
    
    os.makedirs(LOCAL_OUTPUT_PATH, exist_ok=True)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        
        for gender, categories in TARGET_MAP.items():
            for category, urls in categories.items():
                for url in urls: 
                    await crawl_category(gender, category, url, context)
        
        await browser.close()
    
    if os.path.exists(LOCAL_OUTPUT_PATH):
        local_files = [f for f in os.listdir(LOCAL_OUTPUT_PATH) if f.endswith('.json')]
        if local_files:
            print(f"\n📦 로컬 수집 완료 ({len(local_files)}건). HDFS 저장을 시작합니다...")
            try:
                client = InsecureClient(HDFS_NAMENODE_URL, user=HDFS_USER)
                
                # HDFS 폴더 생성
                try:
                    client.makedirs(HDFS_ROOT_PATH)
                    print(f"   📁 HDFS 타겟 디렉토리 확인: {HDFS_ROOT_PATH}")
                except Exception as e:
                    print(f"   ℹ️ HDFS 디렉토리 메시지: {e}")

                saved_count = 0
                for filename in local_files:
                    local_file_path = os.path.join(LOCAL_OUTPUT_PATH, filename)
                    hdfs_file_path = f"{HDFS_ROOT_PATH}/{filename}"
                    
                    try:
                        # 파일 업로드 (덮어쓰기 허용)
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
