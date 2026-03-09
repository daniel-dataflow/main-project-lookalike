def process_sync_mongo_to_es(brand_name: str, mongo_uri: str, es_uri: str) -> dict:
    """
    순수 파이썬 로직: MongoDB의 분석 완료 데이터를 ElasticSearch로 동기화합니다.
    """
    from pymongo import MongoClient
    from elasticsearch import Elasticsearch, helpers

    print(f"🔄 [{brand_name.lower()}] MongoDB -> ElasticSearch 동기화 시작...")

    # 1. 연결 설정
    mongo_client = MongoClient(mongo_uri.strip('"\' '))
    db = mongo_client["datadb"] 
    collection = db["analyzed_metadata"]

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

    # 3. 데이터 이관 (제너레이터)
    def generate_es_docs():
        cursor = collection.find({"basic_info.brand": brand_name.lower()})
        
        for doc in cursor:
            has_text_vec = "text_vector" in doc
            has_image_vec = "image_vector" in doc

            if not has_image_vec and not has_text_vec:
                continue

            basic_info = doc.get("basic_info", {})
            analysis = doc.get("analysis", {})

            es_doc = {
                "_index": index_name,
                "_id": str(doc["_id"]),
                "_source": {
                    "filename": doc.get("filename"),
                    "brand": basic_info.get("brand", brand_name.lower()),
                    "category": basic_info.get("original_category"),
                    "description": analysis.get("description", ""),
                    "image_url": doc.get("filename") 
                }
            }

            if has_image_vec:
                es_doc["_source"]["vec_image"] = doc["image_vector"]
            
            if has_text_vec:
                es_doc["_source"]["vec_text"] = doc["text_vector"]

            yield es_doc

    # 4. 벌크 인서트 실행
    try:
        success, failed = helpers.bulk(es, generate_es_docs())
        
        failed_count = len(failed) if isinstance(failed, list) else failed
        
        print(f"🚀 [{brand_name.lower()}] 동기화 완료: 성공 {success}건, 실패 {failed_count}건")
        if failed_count > 0:
            print("⚠️ 일부 데이터가 들어가지 않았습니다. 데이터 형식을 확인해주세요.")
            
        return {"success": success, "failed": failed_count}
        
    except Exception as e:
        print(f"❌ 에러 발생: {e}")
        return {"success": 0, "failed": -1, "error": str(e)}
