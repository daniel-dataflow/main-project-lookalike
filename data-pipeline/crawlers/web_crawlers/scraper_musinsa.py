import os
import asyncio
import re
import json
from datetime import datetime
from playwright.async_api import async_playwright
from hdfs import InsecureClient # pip install hdfs 필요

# --- 설정 ---
BRAND_NAME = "musinsa"
LOCAL_OUTPUT_PATH = f"crawlers/data/{BRAND_NAME}_json_files"

# --- 하둡 설정 ---
HDFS_URL = "http://your-namenode-host:9870"  # 네임노드 IP 및 포트
HDFS_USER = "hadoop"                         # 하둡 유저명
DATE_STR = datetime.now().strftime('%Y%m%d') # 오늘 날짜 (예: 20240522)
# 저장 구조: raw/brand/date
HDFS_BASE_PATH = f"/user/{HDFS_USER}/raw/{BRAND_NAME}/{DATE_STR}"

# 하둡 클라이언트 초기화
try:
    hdfs_client = InsecureClient(HDFS_URL, user=HDFS_USER)
    # 시작 전 하둡 디렉토리 미리 생성
    hdfs_client.makedirs(HDFS_BASE_PATH)
except Exception as e:
    print(f"⚠️ 하둡 연결 초기화 실패: {e}")

visited_products = set()
sem = asyncio.Semaphore(4)
async def extract_attribute_focus_data(page):
    try:
        # [1] 가벼운 스크롤 (속성 정보 로딩용)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.4)")
        await asyncio.sleep(0.5)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.7)")
        await asyncio.sleep(1.0)

        data = await page.evaluate("""() => {
            const result = {};
            result.scraped_url = location.href;

            // --- A. 기본 정보 ---
            let goodsInfo = {};
            try {
                const script = document.getElementById('__NEXT_DATA__');
                if (script) {
                    const nextData = JSON.parse(script.innerText);
                    const state = nextData.props?.pageProps?.initialState || 
                                  nextData.props?.pageProps?.dehydratedState?.queries?.[0]?.state?.data || {};
                    goodsInfo = state.products?.goods || state.goods || {};
                }
            } catch(e) {}

            result.product_id = goodsInfo.goodsNo || location.href.match(/\d+/)[0];

            // [핵심] 상품명 깔끔하게 정제
            let rawName = document.querySelector('meta[property="og:title"]')?.content || document.title;
            rawName = rawName.split(' - ')[0].trim(); // 뒷부분 제거
            
            result.product_name = rawName
                .replace(/^\[?무신사\s?스탠다드.*?\]?\s*/, '')   // [무신사 스탠다드] 제거
                .replace(/\(MUSINSA STANDARD\)\s*/i, '')     // (MUSINSA STANDARD) 제거
                .replace(/^\[?쿨탠다드.*?\]?\s*/, '')          // [쿨탠다드] 제거
                .trim();

            result.product_code = goodsInfo.goodsCode || "";
            if (!result.product_code) {
                // 품번 텍스트 찾기
                const dt = Array.from(document.querySelectorAll('dt, th, span')).find(e => e.innerText.trim() === '품번');
                if (dt) {
                    const val = dt.nextElementSibling || dt.parentElement.querySelector('dd');
                    if (val) result.product_code = val.innerText.trim();
                }
            }

            // 가격
            let p = goodsInfo.salePrice || goodsInfo.goodsPrice || 0;
            if (typeof p === 'object') p = p.salePrice || 0;
            if (!p || p === 0) {
                 const metaPrice = document.querySelector('meta[property="product:price:amount"]')?.content;
                 if (metaPrice) p = parseInt(metaPrice);
            }
            result.price = p;

            // --- B. 이미지 ---
            const images = [];
            const ogImg = document.querySelector('meta[property="og:image"]')?.content;
            if (ogImg) images.push(ogImg);

            document.querySelectorAll('img').forEach(img => {
                const alt = img.alt || "";
                if (alt.includes('content-img')) {
                    let src = img.getAttribute('data-src') || img.src;
                    if (src && !src.startsWith('data:')) {
                        images.push(src.startsWith('//') ? 'https:' + src : src);
                    }
                }
            });
            result.images = [...new Set(images)];

            // --- C. [핵심] 핏/계절감/두께 추출 (Key-Value 매핑 강화) ---
            const attributes = {};
            const targetKeys = ['핏', '촉감', '신축성', '비침', '두께', '계절', '안감'];
            
            // 1. 테이블 형태 (th -> td) 스캔
            document.querySelectorAll('tr').forEach(tr => {
                const th = tr.querySelector('th');
                const td = tr.querySelector('td');
                if (th && td) {
                    const key = th.innerText.trim();
                    if (targetKeys.includes(key)) {
                        attributes[key] = td.innerText.trim();
                    }
                }
            });

            // 2. 선택형 UI (무신사 특유의 .hausPV 클래스) 스캔
            // 각 섹션(ul, div)을 순회하며 헤더와 선택된 값을 찾음
            if (Object.keys(attributes).length === 0) {
                document.querySelectorAll('.MaterialInfo__MaterialWrap-sc-o69dy9-2, ul, div').forEach(container => {
                    const header = container.querySelector('li, span, th, dt');
                    if (header) {
                        const key = header.innerText.trim();
                        if (targetKeys.includes(key)) {
                            // 해당 컨테이너(Row) 안에서 'hausPV' (선택된 값) 찾기
                            const selected = container.querySelector('.hausPV');
                            if (selected) {
                                attributes[key] = selected.innerText.trim();
                            }
                        }
                    }
                });
            }
            
            // 3. 그래도 없으면 텍스트 기반 보정 (형제 요소 확인)
            if (Object.keys(attributes).length === 0) {
                document.querySelectorAll('dt, th, span').forEach(el => {
                     const txt = el.innerText.trim();
                     if (targetKeys.includes(txt) && !attributes[txt]) {
                         const val = el.nextElementSibling;
                         if (val) attributes[txt] = val.innerText.trim();
                     }
                });
            }

            result.attributes = attributes;
            // 사이즈 테이블은 제거됨

            return result;
        }""")
        
        data['scraped_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return data
    except Exception as e:
        return {"error": str(e)}

async def process_product(product_id, gender, category, context):
    if product_id in visited_products: return
    visited_products.add(product_id)
    
    async with sem:
        url = f"https://www.musinsa.com/products/{product_id}"
        p_page = await context.new_page()
        
        # 리소스 최적화
        await p_page.route("**/*review*", lambda route: route.abort())
        await p_page.route("**/*recommend*", lambda route: route.abort())
        
        try:
            print(f"   🔎 {product_id} 분석 중...")
            await p_page.goto(url, timeout=60000, wait_until="domcontentloaded")
            raw_data = await extract_attribute_focus_data(p_page)
            
            if raw_data and 'product_id' in raw_data:
                raw_data['gender'] = gender
                raw_data['category'] = category
                
                # 1. 로컬에 임시 파일 생성
                if not os.path.exists(LOCAL_OUTPUT_PATH): os.makedirs(LOCAL_OUTPUT_PATH)
                filename = f"musinsa_{product_id}.json"
                local_file_path = os.path.join(LOCAL_OUTPUT_PATH, filename)
                
                with open(local_file_path, 'w', encoding='utf-8') as f:
                    json.dump(raw_data, f, ensure_ascii=False, indent=4)
                
                # 2. 하둡 업로드 및 로컬 삭제
                try:
                    hdfs_full_path = f"{HDFS_BASE_PATH}/{filename}"
                    # overwrite=True: 이미 있으면 덮어쓰기
                    hdfs_client.upload(hdfs_full_path, local_file_path, overwrite=True)
                    
                    # 업로드 성공 시 로컬 삭제
                    if os.path.exists(local_file_path):
                        os.remove(local_file_path)
                        save_status = "HDFS 업로드 완료 & 로컬 삭제"
                except Exception as he:
                    save_status = f"HDFS 전송 실패: {str(he)[:30]}"
                
                attr_str = ", ".join(list(raw_data.get('attributes', {}).keys()))
                print(f"   ✅ {product_id} | {save_status} | 속성: [{attr_str}]")
                
        except Exception as e:
            print(f"   ❌ {product_id} 에러: {str(e)[:50]}")
        finally:
            await p_page.close()

async def crawl_category(gender, category, url, context):
    print(f"\n>>> 🎯 [{gender}-{category}] 목록 검색")
    page = await context.new_page()
    product_ids = set()
    try:
        await page.goto(url, timeout=60000)
        for _ in range(3): 
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1.0)
        hrefs = await page.evaluate("""() => Array.from(document.querySelectorAll('a')).map(a => a.href)""")
        for h in hrefs:
            m = re.search(r'(?:goods|products)\/(\d+)', h)
            if m: product_ids.add(m.group(1))
        await page.close()
        await asyncio.gather(*[process_product(pid, gender, category, context) for pid in list(product_ids)])
    except Exception as e:
        print(f"   ❌ 목록 실패: {e}")
        await page.close()

async def run():
    print("--- [START] 무신사 핏/계절감 집중 크롤러 ---")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        for gender, categories in TARGET_MAP.items():
            for category, urls in categories.items():
                for url in urls: await crawl_category(gender, category, url, context)
        await browser.close()
    print(f"\n✨ 수집 종료")

if __name__ == "__main__":
    asyncio.run(run())