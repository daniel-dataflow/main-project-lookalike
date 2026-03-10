import argparse
from pymongo import MongoClient
from sentence_transformers import SentenceTransformer

class FashionVectorGenerator:
    def __init__(self, mongo_uri, db_name='datadb', collection_name='analyzed_metadata', model_name='jhgan/ko-sroberta-sts'):
        print(f"⏳ 모델 로딩 중: {model_name}...")
        self.model = SentenceTransformer(model_name)

        # 몽고DB 연결 설정
        self.client = MongoClient(mongo_uri)
        self.db = self.client[db_name]
        self.collection = self.db[collection_name]

    def save_vectors_to_mongo(self, brand_name):
        # 1. 몽고DB에서 데이터 가져오기 (이미 텍스트 벡터화된 데이터는 제외)
        query = {"text_vector": {"$exists": False}}
        docs = list(self.collection.find(query))

        if not docs:
            print("❌ 벡터화할 새로운 데이터가 몽고DB에 없습니다.")
            return

        print(f"📂 변환 대상 데이터: {len(docs)}개 문서")

        for doc in docs:
            try:
                # 파일명 통일 로직 (image_filename이 없으면 filename을 씀)
                img_filename = doc.get('image_filename') or doc.get('filename') or "unknown.jpg"

                # 2. 몽고DB 문서에서 텍스트 추출 (새로운 구조에 맞게 수정!)
                analysis = doc.get('analysis', {})
                basic_info = doc.get('basic_info', {})

                # search_text 가져오기
                search_text = analysis.get('search_text', '')
                
                # occasion (배열) 가져와서 쉼표 문자열로 만들기
                occasion_list = analysis.get('occasion', [])
                occasions = ", ".join(occasion_list) if isinstance(occasion_list, list) else str(occasion_list)
                
                # basic_info에서 카테고리, 성별 가져오기
                category = basic_info.get('category_code', 'unknown')
                gender = basic_info.get('gender', 'unknown')

                # 🌟 벡터화의 핵심 재료 조립! 
                text_input = f"브랜드: {brand_name}, 성별: {gender}, 카테고리: {category}, 상황: {occasions}, 설명: {search_text}"

                # 3. 벡터 생성 (다국어 모델 사용)
                vector_value = self.model.encode(text_input).tolist()
                                                                                                        
                # 4. 몽고DB 업데이트 쿼리 구성
                update_query = {
                    "$set": {
                        "text_vector": vector_value,         # 텍스트 벡터 추가
                        "image_filename": img_filename       # 이름표 강제 고정
                    }
                }

                # 기존 문서에 'filename'이 있다면 삭제 (구조 통일)
                if 'filename' in doc:
                    update_query["$unset"] = {"filename": ""}

                # 몽고DB에 최종 반영
                self.collection.update_one(
                    {"_id": doc["_id"]},
                    update_query
                )
                print(f"✅ 성공 (파일명: {img_filename})")

            except Exception as e:
                print(f"❌ 실패 (ID: {doc.get('_id')}): {e}")

        print("🎉 몽고DB 벡터 업데이트 완료!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="텍스트 벡터 추출 및 몽고DB 저장")
    parser.add_argument("--mongo_uri", type=str, required=True, help="MongoDB 접속 URI")
    parser.add_argument("--brand_name", type=str, required=True, help="처리할 브랜드 이름")

    args = parser.parse_args()

    generator = FashionVectorGenerator(mongo_uri=args.mongo_uri)
    generator.save_vectors_to_mongo(brand_name=args.brand_name)
