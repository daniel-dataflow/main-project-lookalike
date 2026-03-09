from airflow.decorators import task
from typing import Any

from functions.embed_funcs import process_fashion_clip_embedding

@task(task_id="embed_to_json")
def embed_to_json(rows: list[dict[str, Any]], out_root: str, batch_size: int = 1) -> list[str]:
    return process_fashion_clip_embedding(
        rows=rows, 
        out_root=out_root, 
        batch_size=batch_size
    )
