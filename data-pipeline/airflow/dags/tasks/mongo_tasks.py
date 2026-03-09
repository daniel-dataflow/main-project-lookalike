from airflow.decorators import task
from functions.mongo_funcs import process_mongo_upsert

@task(task_id="upsert_mongo")
def upsert_mongo(
    json_paths: list[str],
    mongo_uri: str,
    db_name: str = "datadb",
    collection: str = "analyzed_metadata",
) -> int:
    return process_mongo_upsert(
        json_paths=json_paths,
        mongo_uri=mongo_uri,
        db_name=db_name,
        collection=collection
    )
