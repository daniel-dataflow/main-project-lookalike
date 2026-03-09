from airflow.decorators import task
from functions.sync_es_funcs import process_sync_mongo_to_es

@task(task_id="sync_mongo_to_es")
def sync_mongo_to_es(brand_name: str, mongo_uri: str, es_uri: str) -> dict:
    return process_sync_mongo_to_es(
        brand_name=brand_name,
        mongo_uri=mongo_uri,
        es_uri=es_uri
    )
