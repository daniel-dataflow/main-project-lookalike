import os
import sys
import json
import io
from hdfs import InsecureClient
from pymongo import MongoClient
from tqdm import tqdm
from datetime import datetime
from ollama import Client
from sentence_transformers import SentenceTransformer

# 1. 연결 및 모델 설정
HDFS_URL = "http://namenode:9870"
MONGO_URI = "mongodb://datauser:DataPass2026!@mongo-main:27017/datadb?authSource=admin"
HDFS_PATH = "/raw/zara/image"
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
        # 예: 8seconds_men_bottom_GM0025061094487.jpg
        name_without_ext = filename.split('.')[0]
        parts = name_without_ext.split('_')
        
        brand = parts[0] if len(parts) > 0 else "Unknown"
        product_code = parts[3] if len(parts) > 3 else "Unknown"
        product_id = f"{brand}_{product_code}" # 기존 이미지 벡터의 ID 포맷과 맞춤!

        return {
            "brand": brand,
            "gender": parts[1] if len(parts) > 1 else "Unknown",
            "category_code": parts[2] if len(parts) > 2 else "Unknown",
            "product_code": product_code,
            "product_id": product_id
        }
    except:
        return {"brand": "Unknown", "gender": "Unknown", "category_code": "Unknown", "product_code": "Unknown", "product_id": "Unknown"}

def analyze_with_ollama(img_path, basic_info):
    """HDFS 이미지를 읽어 Ollama로 분석"""
    category = basic_info.get('category_code', 'Unknown')

    try:
        with hdfs_client.read(img_path) as reader:
            img_bytes = reader.read()

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
        print(f"❌ 분석 에러 ({img_path}): {e}")
        return None

def main():
    try:
        all_files = hdfs_client.list(HDFS_PATH)
        img_exts = ('.jpg', '.jpeg', '.png', '.JPG', '.PNG')
        images = [os.path.join(HDFS_PATH, f) for f in all_files if f.endswith(img_exts)]
    except Exception as e:
        print(f"❌ HDFS 접속 실패: {e}")
        sys.exit(1)

    if not images:
        print("❌ 분석할 이미지가 없습니다.")
        sys.exit(1)

    print(f"🚀 Ollama 가동 ({VLM_MODEL_NAME}) - 총 {len(images)}장 분석 시작")
    
    for img_path in tqdm(images):
        filename = os.path.basename(img_path)
        basic_info = parse_filename_category(filename)
        product_id = basic_info.get("product_id")

        # 벡터가 업데이트 된 문서라면 건너뛰기
        existing_doc = collection.find_one({"product_id": product_id, "text_vector": {"$exists": True}})
        if existing_doc:
            continue

        # 1. VLM 이미지 분석
        raw_result = analyze_with_ollama(img_path, basic_info)
        if not raw_result: continue

        try:
            # 2. JSON 파싱
            clean_json = raw_result.replace("```json", "").replace("```", "").strip()
            if "{" in clean_json:
                clean_json = clean_json[clean_json.find("{"):clean_json.rfind("}")+1]
            parsed_data = json.loads(clean_json)

            # 3. 텍스트 임베딩 생성
            search_text = parsed_data.get('search_text', '')
            occasion_list = parsed_data.get('occasion', [])
            occasions = ", ".join(occasion_list) if isinstance(occasion_list, list) else str(occasion_list)
            category = basic_info.get('category_code', 'unknown')
            gender = basic_info.get('gender', 'unknown')

            text_input = f"브랜드: {basic_info.get('brand')}, 성별: {gender}, 카테고리: {category}, 상황: {occasions}, 설명: {search_text}"
            text_vector = embedding_model.encode(text_input).tolist()

            # 4. 몽고DB 기존 문서에 업데이트
            update_query = {
                "$set": {
                    "basic_info": basic_info,    # 혹시 비어있을까봐 채워줌
                    "analysis": parsed_data,     # VLM 결과 합체
                    "text_vector": text_vector,  # 텍스트 벡터 합체
                    "analyzed_at": datetime.now().isoformat()
                }
            }

            collection.update_one({"product_id": product_id}, update_query, upsert=True)

        except Exception as e:
            print(f"⚠️ 처리 실패 ({filename}): {e}")

    print(f"\n🎉 모든 분석 및 임베딩 결과가 기존 문서에 성공적으로 병합되었습니다.")

if __name__ == "__main__":
    main()
