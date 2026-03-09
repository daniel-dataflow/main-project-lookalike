from airflow.decorators import task
from functions.mongo_text_funcs import process_text_mongo_upsert

@task(task_id="upsert_mongo_text_data")
def upsert_mongo_text_data(
    json_paths: list[str],
    mongo_uri: str
) -> int:
    return process_text_mongo_upsert(
        json_paths=json_paths,
        mongo_uri=mongo_uri
    )
