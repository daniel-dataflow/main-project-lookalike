import os
import sys
import json
import ollama
import io
from hdfs import InsecureClient
from pymongo import MongoClient
from tqdm import tqdm
from deep_translator import GoogleTranslator
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
translator = GoogleTranslator(source='en', target='ko')

def parse_filename_category(filename):
    """파일명에서 브랜드, 성별, 카테고리 추출"""
    try:
        parts = filename.split('_')
        return {
            "brand": parts[0] if len(parts) > 0 else "Unknown",
            "gender": parts[1] if len(parts) > 1 else "Unknown",
            "original_category": parts[2] if len(parts) > 2 else "Unknown"
        }
    except:
        return {"brand": "Unknown", "gender": "Unknown", "original_category": "Unknown"}
        
def translate_recursive(data):
    """JSON 결과 내 영문을 한글로 번역"""
    if isinstance(data, dict):
        return {k: translate_recursive(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [translate_recursive(i) for i in data]
    elif isinstance(data, str):
        try:
            return translator.translate(data)
        except: return data
    return data

def analyze_with_ollama(hdfs_img_path, basic_info):
    """HDFS 이미지를 읽어 Ollama로 분석"""
    prompt = f"""
    You are a professional fashion editor. Analyze this '{basic_info['gender']} {basic_info['original_category']}'.
    OUTPUT FORMAT (JSON ONLY):
    {{
      "summary": "one-sentence hook",
      "description": "3-4 sentences about fit and details",
      "details": {{ "color_tone": "", "material_feel": "", "silhouette": "" }},
      "tags": [], "keywords": []
    }}
    """

    try:
        # HDFS에서 이미지 데이터를 메모리로 읽기
        with hdfs_client.read(hdfs_img_path) as reader:
            img_bytes = reader.read()

        # 🌟 수정: 함수 안에 있던 중복 Client 선언을 제거하고 전역 클라이언트(ollama_client) 사용
        response = ollama_client.chat(
            model=MODEL_NAME,
            messages=[{'role': 'user', 'content': prompt, 'images': [img_bytes]}],
            options={"temperature": 0.2}
        )
        return response['message']['content']
        
    except Exception as e:
        print(f"❌ 분석 에러 ({hdfs_img_path}): {e}")
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
            # JSON 클리닝 및 파싱
            clean_json = raw_result.replace("```json", "").replace("```", "").strip()
            if "{" in clean_json:
                clean_json = clean_json[clean_json.find("{"):clean_json.rfind("}")+1]
            
            data_en = json.loads(clean_json)
            data_ko = translate_recursive(data_en)

            # 4. MongoDB 저장 포맷 구성
            final_doc = {
                "filename": filename,
                "basic_info": basic_info,
                "analysis": data_ko,
                "analyzed_at": datetime.now().isoformat(),
                "model_used": MODEL_NAME
            }

            collection.insert_one(final_doc)

        except Exception as e:
            print(f"⚠️ 처리 실패 ({filename}): {e}")

    print(f"\n🎉 모든 분석 결과가 MongoDB에 저장되었습니다.")

if __name__ == "__main__":
    main()
