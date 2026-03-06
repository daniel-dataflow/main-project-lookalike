import os
import sys
import json
import gc
import argparse
from hdfs import InsecureClient
from pymongo import MongoClient
from datetime import datetime
from ollama import Client
from sentence_transformers import SentenceTransformer

# 1. 고정 연결 및 모델 설정
HDFS_URL = "http://namenode:9870"
MONGO_URI = "mongodb://datauser:DataPass2026!@mongo-main:27017/datadb?authSource=admin"
VLM_MODEL_NAME = "gemma3:4b"
EMBED_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

# 클라이언트 초기화
hdfs_client = InsecureClient(HDFS_URL, user='root')
mongo_client = MongoClient(MONGO_URI)
db = mongo_client['datadb']
collection = db['analyzed_metadata']

ollama_client = Client(host='http://172.31.11.240:11434')

print(f"⏳ 텍스트 임베딩 모델 로딩 중: {EMBED_MODEL_NAME}...")
embedding_model = SentenceTransformer(EMBED_MODEL_NAME)

def parse_filename_category(filename):
    """파일명에서 브랜드, 성별, 카테고리, 상품코드 추출"""
    try:
        name_without_ext = filename.split('.')[0]
        parts = name_without_ext.split('_')

        brand = parts[0] if len(parts) > 0 else "Unknown"
        product_code = parts[3] if len(parts) > 3 else "Unknown"
        product_id = f"{brand}_{product_code}"

        return {
            "brand": brand,
            "gender": parts[1] if len(parts) > 1 else "Unknown",
            "category_code": parts[2] if len(parts) > 2 else "Unknown",
            "product_code": product_code,
            "product_id": product_id
        }
    except:
        return {"brand": "Unknown", "gender": "Unknown", "category_code": "Unknown", "product_code": "Unknown", "product_id": "Unknown"}

def analyze_with_ollama(img_bytes, category):
    """HDFS 이미지 바이트를 받아 Ollama로 분석"""
    try:
        messages = [
            {
                'role': 'system',
                'content': """
                당신은 패션 상품의 검색용 설명을 생성하는 전문가입니다.

                규칙:
                - 출력은 반드시 JSON 포맷으로만 작성하세요. 마크다운이나 다른 설명은 덧붙이지 마세요.
                - 모든 답변은 반드시 한국어(Korean)로만 작성하세요.
                - 확실하지 않은 정보는 "(추정)"이라고 표기하세요.
                - search_text는 한국어 자연어 검색에 최적화된 1~2문장으로 작성합니다.
                - search_text에는 반드시 다음 요소를 포함해야 합니다:
                세부 카테고리, 색상, 디자인, 패턴, 소재(추정 가능), 실루엣/핏, 어울리는 상황
                """
            },
            {
                'role': 'user',
                "content": f"""
                카테고리: {category}

                이 이미지에서 {category} 의류만 분석하세요. 다른 의류나 배경은 무시하세요.

                아래 JSON 형식으로만 작성하세요:
                {{
                "summary": "",
                "details": {{
                    "color": "예: 네이비, 차콜, 연청, 베이지 등",
                    "design": "예: 스트라이프, 무지, 핀턱, 밴딩 등",
                    "pattern": "예: 무지, 체크, 스트라이프, 아가일 등",
                    "material": "예: 면, 데님, 린넨, 울, 폴리에스테르 (추정)",
                    "silhouette": "예: 와이드핏, 테이퍼드핏, 슬림핏, 레귤러핏"
                }},
                "occasion": [],
                "search_text": ""
                }}

                occasion은 다음 중 3개만 선택:
                [면접, 하객, 출근, 데이트, 데일리, 격식있는행사, 여행, 파티, 캐주얼모임]

                search_text는 한국어 자연어 검색에 적합한 1문장으로 작성하세요.
                (카테고리, 색상, 디자인, 패턴, 소재, 핏, 어울리는 상황 포함)
                """,
                'images': [img_bytes]
            }
        ]

        response = ollama_client.chat(
            model=VLM_MODEL_NAME,
            messages=messages,
            options={
                'temperature': 0.1,
                'num_predict': 300
            }
        )
        return response['message']['content']

    except Exception as e:
        print(f"❌ [VLM 분석 에러]: {e}")
        return None

def main(brand_name):
    HDFS_PATH = f"/raw/{brand_name}/image"
    print(f"📂 타겟 HDFS 경로 설정됨: {HDFS_PATH}")

    try:
        all_files = hdfs_client.list(HDFS_PATH)
        img_exts = ('.jpg', '.jpeg', '.png', '.JPG', '.PNG')
        images = [os.path.join(HDFS_PATH, f) for f in all_files if f.endswith(img_exts)]
    except Exception as e:
        print(f"❌ HDFS 접속 실패 또는 경로 없음: {e}")
        sys.exit(1)

    if not images:
        print(f"❌ 분석할 이미지가 없습니다. (경로: {HDFS_PATH})")
        sys.exit(1)

    total_imgs = len(images)
    print(f"🚀 Ollama 가동 ({VLM_MODEL_NAME}) - 총 {total_imgs}장 분석 시작")

    for idx, img_path in enumerate(images, 1):
        filename = os.path.basename(img_path)
        basic_info = parse_filename_category(filename)
        product_id = basic_info.get("product_id")

        try:
            existing_doc = collection.find_one({"product_id": product_id, "text_vector": {"$exists": True}})
            if existing_doc:
                print(f"⏭️ [{idx}/{total_imgs}] [건너뜀] 이미 처리된 문서입니다: {product_id}")
                continue

            status = hdfs_client.status(img_path)
            file_size = status.get('length', 0)
            if file_size < 5120:
                print(f"🚨 [{idx}/{total_imgs}] [건너뜀] 파일 크기가 너무 작습니다 (더미 의심, {file_size}B): {filename}")
                continue

            with hdfs_client.read(img_path) as reader:
                img_bytes = reader.read()

            raw_result = analyze_with_ollama(img_bytes, basic_info.get('category_code', 'Unknown'))
            
            if not raw_result:
                print(f"⚠️ [{idx}/{total_imgs}] [건너뜀] VLM이 응답을 주지 않았습니다: {filename}")
                continue

            clean_json = raw_result.replace("```json", "").replace("```", "").strip()
            if "{" in clean_json:
                clean_json = clean_json[clean_json.find("{"):clean_json.rfind("}")+1]
            
            parsed_data = json.loads(clean_json)

            search_text = parsed_data.get('search_text', '')
            occasion_list = parsed_data.get('occasion', [])
            occasions = ", ".join(occasion_list) if isinstance(occasion_list, list) else str(occasion_list)
            category = basic_info.get('category_code', 'unknown')
            gender = basic_info.get('gender', 'unknown')

            text_input = f"브랜드: {basic_info.get('brand')}, 성별: {gender}, 카테고리: {category}, 상황: {occasions}, 설명: {search_text}"
            text_vector = embedding_model.encode(text_input).tolist()

            update_query = {
                "$set": {
                    "basic_info": basic_info,
                    "analysis": parsed_data,
                    "text_vector": text_vector,
                    "analyzed_at": datetime.now().isoformat()
                }
            }

            collection.update_one({"product_id": product_id}, update_query, upsert=True)
            print(f"✅ [{idx}/{total_imgs}] 저장 완료: {product_id}")

        except json.JSONDecodeError:
            print(f"❌ [{idx}/{total_imgs}] [JSON 파싱 에러 스킵] Ollama가 이상한 대답을 했습니다: {filename}")
            continue
        except Exception as e:
            print(f"❌ [{idx}/{total_imgs}] [에러 발생 스킵] {filename} - {e}")
            continue
        finally:
            gc.collect()

    print(f"\n🎉 [종료] 모든 VLM 분석 및 임베딩 작업이 끝났습니다!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VLM Text Analysis Pipeline")
    parser.add_argument("--brand_name", type=str, required=True, help="Target brand name (e.g., zara, 8seconds)")
    
    args = parser.parse_args()
    
    main(args.brand_name)
