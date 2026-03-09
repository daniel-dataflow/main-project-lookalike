import os
from airflow.operators.bash import BashOperator

# 경로 및 상수
CRAWLER_DIR = "/opt/airflow/data-pipeline/crawlers/web_crawlers"
SPARK_DIR = "/opt/airflow/data-pipeline/spark/jobs"

def get_crawl_task(crawler_key, today_str):
    if crawler_key == 'zara':
        crawl_cmd = f'PYTHONPATH={CRAWLER_DIR} xvfb-run -a python3 -u {CRAWLER_DIR}/scraper_{crawler_key}.py {today_str}'
    else:
        crawl_cmd = f'PYTHONPATH={CRAWLER_DIR} python3 -u {CRAWLER_DIR}/scraper_{crawler_key}.py {today_str}'

    return BashOperator(
        task_id=f'crawl_{crawler_key}',
        bash_command=crawl_cmd
    )

def get_spark_task(crawler_key, spark_key, today_str):
    return BashOperator(
        task_id=f"spark_process_{crawler_key}",
        bash_command=(
            "export SPARK_HOME=/home/airflow/.local/lib/python3.11/site-packages/pyspark && "
            "export PATH=$SPARK_HOME/bin:$PATH && "
            "spark-submit --master local[*] "
            "--packages org.postgresql:postgresql:42.6.0,org.mongodb.spark:mongo-spark-connector_2.12:10.3.0 " 
            f"{SPARK_DIR}/fashion_batch_job_{spark_key}.py {today_str}"
        )
    )

def get_naver_api_task(crawler_key):
    return BashOperator(
        task_id=f'naver_api_{crawler_key}',
        bash_command=f'python3 -u {SPARK_DIR}/fashion_batch_job_NV.py',
        env={
            'DB_HOST': 'postgresql',
            'NAVER_CLIENT_ID': os.getenv('NAVER_CLIENT_ID', ""),
            'NAVER_CLIENT_SECRET': os.getenv('NAVER_CLIENT_SECRET', ""),
        },
        append_env=True 
    )

def get_vlm_task(crawler_key):
    return BashOperator(
        task_id=f'vlm_analyze_images_{crawler_key}',
        bash_command=f'python3 -u /opt/airflow/dags/scripts/vlm.py --brand_name {crawler_key}'
    )

def get_sync_es_task(crawler_key):
    return BashOperator(
        task_id=f'sync_mongo_to_es_{crawler_key}',
        bash_command=f'python3 -u /opt/airflow/dags/scripts/sync_to_es.py --brand_name {crawler_key}'
    )
