from airflow.decorators import task
from functions.vlm_funcs import process_vlm_extraction

@task(task_id="extract_text_vlm")
def extract_text_vlm(
    brand_name: str, 
    hdfs_url: str, 
    ollama_host: str, 
    model_name: str, 
    out_dir: str
) -> list[str]:
    return process_vlm_extraction(
        brand_name=brand_name,
        hdfs_url=hdfs_url,
        ollama_host=ollama_host,
        model_name=model_name,
        out_dir=out_dir
    )
