import json
from pathlib import Path

def process_text_mongo_upsert(json_paths: list[str], mongo_uri: str) -> int:
    from pymongo import MongoClient, UpdateOne
    if not json_paths: return 0

    client = MongoClient(mongo_uri.strip('"\' '))
    col = client['datadb']['analyzed_metadata']
    ops = []

    for p in json_paths:
        doc = json.loads(Path(p).read_text(encoding="utf-8"))
        pid = doc["product_id"]
        
        update_query = {
            "$set": {
                "vlm_basic_info": doc.get("basic_info"),
                "vlm_analysis": doc.get("analysis"),
                "text_vector": doc.get("text_vector"),
                "vlm_analyzed_at": doc.get("analyzed_at")
            }
        }
        ops.append(UpdateOne({"product_id": pid}, update_query, upsert=True))

    if ops: col.bulk_write(ops, ordered=False)
    return len(ops)
