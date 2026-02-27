import os
import sys
import json
import ollama
import io
from hdfs import InsecureClient
from pymongo import MongoClient
from tqdm import tqdm
from datetime import datetime
from PIL import Image
from ollama import Client

# 1. 연결 설정 (AWS Docker 환경에 맞게 컨테이너 이름으로 통일)
HDFS_URL = "http://namenode:9870"
MONGO_URI = "mongodb://datauser:DataPass2026!@mongo-main:27017/datadb?authSource=admin"
HDFS_PATH = "/raw/8seconds/image"
MODEL_NAME = "gemma3:4b"

# 클라이언트 초기화
hdfs_client = InsecureClient(HDFS_URL, user='root')
mongo_client = MongoClient(MONGO_URI)
db = mongo_client['datadb']
collection = db['analyzed_metadata']

ollama_client = Client(host='http://172.31.11.240:11434') 

def parse_filename_category(filename):
    """파일명에서 브랜드, 성별, 카테고리 추출"""
    try:
        parts = filename.split('_')
        return {
            "brand": parts[0] if len(parts) > 0 else "Unknown",
            "gender": parts[1] if len(parts) > 1 else "Unknown",
            "category_code": parts[2] if len(parts) > 2 else "Unknown" # ✅ 이름 통일
        }
    except:
        return {"brand": "Unknown", "gender": "Unknown", "category_code": "Unknown"}

def analyze_with_ollama(img_path, basic_info):
    """HDFS 이미지를 읽어 Ollama로 분석"""
    
    # 1. 카테고리 정보 꺼내기
    category = basic_info.get('category_code', 'Unknown')

    try:
        # 2. HDFS에서 이미지 데이터를 '바이트(bytes)' 형태로 직접 읽어오기
        with hdfs_client.read(img_path) as reader:
            img_bytes = reader.read()

        # 3. 유저님의 고도화된 완벽한 프롬프트 적용!
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
                    "color": "",
                    "design": "",
                    "pattern": "",
                    "material": "",
                    "silhouette": ""
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

        # 4. Ollama 모델 호출
        response = ollama_client.chat(
            model=MODEL_NAME, 
            messages=messages,
            options={
                'temperature': 0.7,
                'num_predict': 300
            }
        )
        return response['message']['content']
        
    except Exception as e:
        print(f"❌ 분석 에러 ({img_path}): {e}")
        return None

def main():
    # 2. HDFS 이미지 목록 가져오기
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

    print(f"🚀 Ollama 가동 ({MODEL_NAME}) - 총 {len(images)}장 분석 시작")
    
    for img_path in tqdm(images):
        filename = os.path.basename(img_path)

        # 3. 중복 체크 (이미 DB에 저장된 파일인지 확인)
        if collection.find_one({"filename": filename}):
            continue

        basic_info = parse_filename_category(filename)
        raw_result = analyze_with_ollama(img_path, basic_info)
        
        if not raw_result: continue

        try:
            # ✅ 번역 과정 삭제! VLM이 뱉어낸 순수 JSON을 바로 파싱합니다.
            clean_json = raw_result.replace("```json", "").replace("```", "").strip()
            if "{" in clean_json:
                clean_json = clean_json[clean_json.find("{"):clean_json.rfind("}")+1]
            
            parsed_data = json.loads(clean_json)

            # 4. MongoDB 저장 포맷 구성
            final_doc = {
                "filename": filename,
                "basic_info": basic_info,
                "analysis": parsed_data, # 한국어로 된 JSON 결과를 그대로 쏙!
                "analyzed_at": datetime.now().isoformat(),
                "model_used": MODEL_NAME
            }

            collection.insert_one(final_doc)

        except Exception as e:
            print(f"⚠️ 처리 실패 ({filename}): {e}")

    print(f"\n🎉 모든 분석 결과가 MongoDB에 저장되었습니다.")

if __name__ == "__main__":
    main()
