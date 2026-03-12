from airflow.decorators import task
from functions.text_embed_funcs import process_text_embedding

@task(task_id="embed_text_vectors")
def embed_text_vectors(
    json_paths: list[str], 
    model_name: str
) -> list[str]:
    return process_text_embedding(
        json_paths=json_paths,
        model_name=model_name
    )
