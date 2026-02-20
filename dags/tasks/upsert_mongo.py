from __future__ import annotations
import json
from pathlib import Path
from airflow.decorators import task
# from pymongo import MongoClient, UpdateOne


# ───────────────────────────────
# Airflow Task
# ───────────────────────────────
@task
def upsert_mongo(
    json_paths: list[str],
    mongo_uri: str,
    db_name: str = "fashion",
    collection: str = "products",
) -> int:
    """
    수행하는 일:
    - embed_to_json 단계에서 생성된 JSON 파일들을 읽어서
    - MongoDB에 bulk upsert 한다.

    upsert의 핵심:
    - product_id를 MongoDB _id로 사용
    - 같은 product_id가 들어오면 문서를 업데이트($set)
    - 없으면 새로 생성(upsert=True)

    Args:
        json_paths: 임베딩 JSON 파일 경로 리스트
        mongo_uri: MongoDB 접속 URI
        db_name: DB 이름 (기본: fashion)
        collection: 컬렉션 이름 (기본: products)

    Returns:
        upsert 요청한 문서 수
    """

    from pymongo import MongoClient, UpdateOne

    # 입력 없으면 종료
    if not json_paths:
        return 0
    
    # MongoDB 연결
    client = MongoClient(mongo_uri)
    col = client[db_name][collection]

    # bulk_write를 위한 update operations 리스트
    ops = []

    for p in json_paths:
        # json 파일 로드
        doc = json.loads(Path(p).read_text(encoding="utf-8"))
        
        # product_id를 PK로 사용
        pid = doc["product_id"]

        # MongoDB의 기본 PK 필드인 _id에 product_id 할당
        # → 중복 적재 방지 + 같은 상품은 항상 같은 문서로 업데이트됨
        doc["_id"] = pid

        # UpdateOne:
        # - 필터: {"_id": pid} (해당 상품 문서)
        # - 업데이트: {"$set": doc} (doc 필드로 덮어씀)
        # - upsert=True: 없으면 insert
        ops.append(UpdateOne({"_id": pid}, {"$set": doc}, upsert=True))
    
    # bulk_write 수행
    # ordered=False:
    # - 병렬/최적화 형태로 처리되어 더 빠름
    # - 중간에 일부 실패가 나도 나머지는 계속 진행 (운영에 유리)
    if ops:
        col.bulk_write(ops, ordered=False)
        
    return len(ops)
