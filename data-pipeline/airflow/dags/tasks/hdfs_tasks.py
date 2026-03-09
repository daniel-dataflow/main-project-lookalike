from airflow.decorators import task
from typing import Any

from functions.hdfs_funcs import process_hdfs_downloads

@task
def fetch_from_hdfs(brand: str, hdfs_root: str, incoming_dir: str) -> list[dict[str, Any]]:
    """
    Airflow Task: HDFS 이미지 다운로드 실행
    """
    return process_hdfs_downloads(brand, hdfs_root, incoming_dir)
