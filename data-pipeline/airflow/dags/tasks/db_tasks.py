from typing import Any
from airflow.decorators import task
from functions.db_funcs import HDFSManager, MongoDBManager, ElasticSearchManager, PostgresManager


#HDFS 이미지 다운로드 실행
@task(task_id="fetch_from_hdfs")
def fetch_from_hdfs(brand: str, hdfs_root: str, incoming_dir: str) -> list[dict[str, Any]]:
    hdfs_manager = HDFSManager()
    return hdfs_manager.process_downloads(brand, hdfs_root, incoming_dir)

@task
def refine_prices_task():
    """
    모든 브랜드의 네이버 가격 수집이 끝난 후, 
    한 번만 실행되어 이상치를 정제하고 View를 덮어씌우는 글로벌 태스크
    """
    logging.info("Postgres 가격 데이터 정제(Global) 태스크 시작...")
    pg_manager = PostgresManager()
    pg_manager.execute_price_refinement()
    logging.info("Postgres 가격 데이터 정제 태스크 완료.")

# Airflow Task: 이미지 메타데이터 MongoDB 적재
@task(task_id="upsert_mongo")
def upsert_mongo(
    json_paths: list[str],
    mongo_uri: str,
    db_name: str = "datadb",
    collection: str = "analyzed_metadata",
) -> int:
    mongo_manager = MongoDBManager(mongo_uri=mongo_uri, db_name=db_name, collection=collection)
    return mongo_manager.upsert_image_metadata(json_paths)

 # VLM 분석 결과 MongoDB 적재
@task(task_id="upsert_mongo_text_data")
def upsert_mongo_text_data(
    json_paths: list[str],
    mongo_uri: str,
    db_name: str = "datadb",
    collection: str = "analyzed_metadata"
) -> int:
    mongo_manager = MongoDBManager(mongo_uri=mongo_uri, db_name=db_name, collection=collection)
    return mongo_manager.upsert_text_metadata(json_paths)

#ElasticSearch 인덱스로 동기화
@task(task_id="sync_mongo_to_es")
def sync_mongo_to_es(brand_name: str, mongo_uri: str, es_uri: str) -> dict:
    es_manager = ElasticSearchManager(es_uri=es_uri, mongo_uri=mongo_uri)
    return es_manager.sync_mongo_to_es(brand_name)