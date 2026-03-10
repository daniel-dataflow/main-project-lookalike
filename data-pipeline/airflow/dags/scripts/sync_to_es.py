import argparse
import os
from pymongo import MongoClient
from elasticsearch import Elasticsearch, helpers

def sync_mongo_to_es(brand_name, mongo_uri, es_uri):
    print(f"🔄 [{brand_name.lower()}] MongoDB -> ElasticSearch 동기화 시작...")

    # 1. 연결 설정
    mongo_client = MongoClient(mongo_uri)
    db = mongo_client["datadb"] 
    collection = db["analyzed_metadata"] # 🚨 컬렉션 이름 맞춤!

    es = Elasticsearch(es_uri, basic_auth=("elastic", "password"))
    index_name = "multimodal_products"

    mapping = {
        "mappings": {
            "properties": {
                "filename":    {"type": "keyword"}, 
                "brand":       {"type": "keyword"},
                "category":    {"type": "keyword"},
                "description": {"type": "text"},
                "image_url":   {"type": "keyword"},
                "vec_image": {
                    "type": "dense_vector",
                    "dims": 512, 
                    "index": True,
                    "similarity": "cosine"
                },
                "vec_text": {
                    "type": "dense_vector",
                    "dims": 384, 
                    "index": True,
                    "similarity": "cosine"
                }
            }
        }
    }

    # 2. 인덱스 생성
    if not es.indices.exists(index=index_name):
        es.indices.create(index=index_name, body=mapping)
        print(f"✅ 인덱스 '{index_name}' 생성 완료")
    else:
        print(f"ℹ️ 인덱스 '{index_name}'가 이미 존재합니다.")

    # 3. 데이터 이관
    def generate_es_docs():
        # 🚨 대소문자 주의! basic_info.brand 에서 소문자로 찾음
        cursor = collection.find({"basic_info.brand": brand_name.lower()})
        
        for doc in cursor:
            # 방금 확인한 JSON 구조에 맞춰서 데이터 여부 확인
            has_text_vec = "text_vector" in doc
            has_image_vec = "image_vector" in doc # 나중에 이미지 임베딩이 들어갈 이름 (가정)

            if not has_image_vec and not has_text_vec:
                continue

            basic_info = doc.get("basic_info", {})
            analysis = doc.get("analysis", {})

            # 문서 구조 생성
            es_doc = {
                "_index": index_name,
                "_id": str(doc["_id"]),
                "_source": {
                    "filename": doc.get("filename"),
                    "brand": basic_info.get("brand", brand_name.lower()),
                    "category": basic_info.get("original_category"),
                    "description": analysis.get("description", ""),
                    "image_url": doc.get("filename") # 일단 파일명을 넣고 나중에 URL로 수정 가능
                }
            }

            if has_image_vec:
                es_doc["_source"]["vec_image"] = doc["image_vector"]
            
            if has_text_vec:
                es_doc["_source"]["vec_text"] = doc["text_vector"]

            yield es_doc

    try:
        success, failed = helpers.bulk(es, generate_es_docs())
        print(f"🚀 [{brand_name.lower()}] 동기화 완료: 성공 {success}건, 실패 {failed}건")
        if failed:
            print("⚠️ 일부 데이터가 들어가지 않았습니다. 데이터 형식을 확인해주세요.")
    except Exception as e:
        print(f"❌ 에러 발생: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MongoDB to ES 동기화 스크립트")
    parser.add_argument("--brand_name", type=str, required=True, help="처리할 브랜드 이름")
    
    # 🚨 주의: 에어플로우(도커 안)에서 실행되므로 mongo-main 주소를 사용해야 합니다!
    mongo_user = os.environ.get("MONGODB_USER", "datauser")
    mongo_pass = os.environ.get("MONGODB_PASSWORD", "")
    default_mongo = os.environ.get("MONGO_URI", f"mongodb://{mongo_user}:{mongo_pass}@mongo-main:27017/?authSource=admin")
    parser.add_argument("--mongo_uri", type=str, default=default_mongo, help="MongoDB 접속 URI")
    parser.add_argument("--es_uri", type=str, default="http://elasticsearch-main:9200", help="ElasticSearch 접속 URI")
    
    args = parser.parse_args()
    
    sync_mongo_to_es(brand_name=args.brand_name, mongo_uri=args.mongo_uri, es_uri=args.es_uri)