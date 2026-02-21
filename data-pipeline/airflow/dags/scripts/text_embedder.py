import argparse
from pymongo import MongoClient
from sentence_transformers import SentenceTransformer

class FashionVectorGenerator:
    def __init__(self, mongo_uri, db_name='datadb', collection_name='analyzed_metadata', model_name='paraphrase-multilingual-MiniLM-L12-v2'):
        print(f"⏳ 모델 로딩 중: {model_name}...")
        self.model = SentenceTransformer(model_name)
        
        # 몽고DB 연결 설정
        self.client = MongoClient(mongo_uri)
        self.db = self.client[db_name]
        self.collection = self.db[collection_name]

    def save_vectors_to_mongo(self, brand_name):
        # 1. 몽고DB에서 데이터 가져오기 (이미 벡터화된 데이터는 제외)
        # 만약 특정 브랜드만 가져오려면 {"brand": brand_name, "text_vector": {"$exists": False}} 로 수정하세요!
        query = {"text_vector": {"$exists": False}}
        docs = list(self.collection.find(query))
        
        if not docs:
            print("❌ 벡터화할 새로운 데이터가 몽고DB에 없습니다.")
            return

        print(f"📂 변환 대상 데이터: {len(docs)}개 문서")

        for doc in docs:
            try:
                # 2. 몽고DB 문서에서 텍스트 추출 (기존 로직 차용)
                analysis = doc.get('analysis', {})
                desc = analysis.get('description', '')
                keywords_list = analysis.get('keywords', [])
                keywords = ", ".join(keywords_list) if isinstance(keywords_list, list) else str(keywords_list)
                category = doc.get('category', 'unknown') 

                text_input = f"브랜드: {brand_name}, 카테고리: {category}, 설명: {desc}, 키워드: {keywords}"

                # 3. 벡터 생성
                vector_value = self.model.encode(text_input).tolist()
                
                # 4. 생성된 벡터를 기존 몽고DB 문서에 추가 (업데이트)
                self.collection.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"text_vector": vector_value}}
                )
                print(f"✅ 성공 (ID: {doc['_id']})")

            except Exception as e:
                print(f"⚠️ 실패 (ID: {doc.get('_id')}): {e}")

        print("🎉 몽고DB 벡터 업데이트 완료!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="텍스트 벡터 추출 및 몽고DB 저장")
    # 로컬 base_path 대신 몽고DB 주소를 받도록 변경
    parser.add_argument("--mongo_uri", type=str, required=True, help="MongoDB 접속 URI")
    parser.add_argument("--brand_name", type=str, required=True, help="처리할 브랜드 이름")
    
    args = parser.parse_args()

    generator = FashionVectorGenerator(mongo_uri=args.mongo_uri)
    generator.save_vectors_to_mongo(brand_name=args.brand_name)