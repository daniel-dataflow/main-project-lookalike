# fashion_total_pipeline.py

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator
from datetime import datetime, timedelta

# 실제 컨테이너 내부 경로
CRAWLER_DIR = "/opt/airflow/data-pipeline/crawlers/web_crawlers"
SPARK_DIR = "/opt/airflow/data-pipeline/spark/jobs"
#CRAWLER_DIR = "/home/lookalike/main-project-lookalike/data-pipeline/crawlers/web_crawlers"
#SPARK_DIR = "/home/lookalike/main-project-lookalike/data-pipeline/spark/jobs"

default_args = {
    'owner': 'lookalike',
    'depends_on_past': False,
    'start_date': datetime(2026, 2, 10),
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

with DAG(
    dag_id='fashion_total_pipeline',
    default_args=default_args,
    description='Scrapy(JSON 생성) -> Spark(DB적재 & 이미지저장) 파이프라인',
    schedule='0 2 * * *',
    catchup=False,
    tags=['fashion', 'full_logic']
) as dag:

    brands = {
        'topten': 'TT',
        '8seconds': '8S',
        'zara': 'ZR',
        'uniqlo': 'UQ',
        'musinsa': 'MS'
    }

    for crawler_key, spark_key in brands.items():

        # 크롤러 실행
        crawl_task = BashOperator(
            task_id=f'crawl_{crawler_key}',
            bash_command=(
                f'PYTHONPATH={CRAWLER_DIR} '
                f'python3 {CRAWLER_DIR}/scraper_{crawler_key}.py {{ ds_nodash }}'
            )
        )

        # Spark 배치 실행
        spark_task = BashOperator(
            task_id=f'spark_{crawler_key}',
            bash_command=(
                f'docker exec namenode-main '
                f'python3 {SPARK_DIR}/fashion_batch_job_{spark_key}.py {{ ds_nodash }}'
            )
        )

        crawl_task >> spark_task
