import os
import sys
import json
import glob
import argparse
from typing import List
from pymongo import MongoClient
from pymongo.errors import BulkWriteError

def parse_metadata_files(brand: str) -> List[dict]:
    """분석된 메타데이터 JSON 파일들을 읽어서 리스트로 반환"""
    # 몽고DB나 ES를 위해 준비된 JSON 파일들 읽어오기
    json_dir = f'data-pipeline/database/data/{brand}/mongo/analyzed_metadata/*.json'
    files = glob.glob(json_dir)
    
    if not files:
        print(f"⚠️  No JSON files found in {json_dir}. Skipping.")
        return []

    docs = []
    for filepath in files:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                doc = json.load(f)
                
                # 'product_id' 키가 없을 시 파일명에서 추출하는 로직 등을 추가할 수 있음
                # 여기서 단순 삽입용이므로 그대로 로드
                
                # Elasticsearch와 비슷하게 _id 키 충돌 방지를 위해 _id 필드를 팝 시키거나 처리
                doc.pop('_id', None) 
                
                # MongoDB에 필요한 문서 포맷이 있다면 여기서 가공
                docs.append(doc)
        except json.JSONDecodeError as e:
            print(f"❌ Error parsing {filepath}: {e}")
        except Exception as e:
            print(f"❌ Unknown error while processing {filepath}: {e}")
            
    return docs

def import_to_mongodb(brand: str, docs: List[dict]):
    """MongoDB에 데이터 벌크 삽입"""
    if not docs:
        return
        
    MONGO_HOST = os.environ.get("MONGODB_HOST", "localhost")
    MONGO_PORT = os.environ.get("MONGODB_PORT", "27017")
    MONGO_DB = os.environ.get("POSTGRES_DB", "datadb") # Currently datadb in env, fallback
    MONGO_USER = os.environ.get("MONGODB_USER", "datauser")
    MONGO_PASS = os.environ.get("MONGODB_PASSWORD", "DataPass2026!")

    # MONGODB_URL이 명시적으로 설정되어 있다면 사용 (예: "mongodb://admin:admin1234@localhost:27017/")
    mongo_url = os.environ.get("MONGODB_URL", f"mongodb://{MONGO_USER}:{MONGO_PASS}@{MONGO_HOST}:{MONGO_PORT}/")

    try:
        client = MongoClient(mongo_url)
        db = client[MONGO_DB]
        collection = db.products
        
        # 만약 기존 데이터를 삭제하고 싶다면 추가 가능
        # collection.delete_many({"brand": brand})
        
        # 벌크 인서트
        result = collection.insert_many(docs, ordered=False)
        print(f"✅ Imported {len(result.inserted_ids)} JSON documents to MongoDB for brand '{brand}'.")
        
    except BulkWriteError as bwe:
        print(f"❌ Bulk write errors occurred: {bwe.details}")
    except Exception as e:
        print(f"❌ MongoDB import error: {e}")
        sys.exit(1)
    finally:
        if 'client' in locals():
            client.close()

def main():
    parser = argparse.ArgumentParser(description="Import metadata JSON files into MongoDB.")
    parser.add_argument("brand", help="Brand name to import data for.")
    args = parser.parse_args()

    print(f"[INFO] Importing JSON documents for brand '{args.brand}' into MongoDB...")
    docs = parse_metadata_files(args.brand)
    import_to_mongodb(args.brand, docs)

if __name__ == "__main__":
    main()
