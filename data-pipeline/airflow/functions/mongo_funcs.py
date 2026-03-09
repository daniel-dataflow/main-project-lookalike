import json
from pathlib import Path

def process_mongo_upsert(
    json_paths: list[str],
    mongo_uri: str,
    db_name: str = "datadb",
    collection: str = "analyzed_metadata",
) -> int:
    from pymongo import MongoClient, UpdateOne

    if not json_paths:
        return 0

    print(f"🚀 [DEBUG] 현재 들어온 몽고 URI 값은?: -->{mongo_uri}<--")

    clean_uri = mongo_uri.strip('"\' ')
    client = MongoClient(clean_uri)
    col = client[db_name][collection]

    ops = []
    for p in json_paths:
        doc = json.loads(Path(p).read_text(encoding="utf-8"))
        pid = doc["product_id"]
        doc["_id"] = pid
        
        ops.append(UpdateOne({"_id": pid}, {"$set": doc}, upsert=True))

    if ops:
        col.bulk_write(ops, ordered=False)
        
    return len(ops)
