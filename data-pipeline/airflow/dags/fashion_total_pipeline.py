from airflow import DAG
from airflow.operators.bash import BashOperator
from datetime import datetime, timedelta
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator
import pytz

# 컨테이너 내부 경로 매핑
CRAWLER_DIR = "/opt/airflow/dags/crawlers"
SPARK_DIR = "/opt/airflow/data-pipeline/spark/jobs"

TODAY_STR = "{{ ds_nodash }}"

default_args = {
    'owner': 'lookalike',
    'depends_on_past': False,
    'start_date': datetime(2026, 2, 16),
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

with DAG(
    dag_id='fashion_total_pipeline',
    default_args=default_args,
    description='Scrapy(JSON 생성) -> Spark(DB적재 & 이미지저장) 파이프라인',
    schedule_interval=None,
    catchup=False,
    tags=['fashion', 'full_logic'],
    max_active_tasks=1, 
) as dag:

    brands = {
        '8seconds': '8S',
    }

    for crawler_key, spark_key in brands.items():
        
        # 크롤러 실행 태스크
        crawl_task = BashOperator(
            task_id=f'crawl_{crawler_key}',
            bash_command=f'python3 {CRAWLER_DIR}/scraper_{crawler_key}.py {{ ds_nodash }}'
        )

        # 스파크 처리 태스크 
        spark_task = SparkSubmitOperator(
            task_id=f'spark_process_{crawler_key}',
            application=f'/opt/airflow/data-pipeline/spark/jobs/fashion_batch_job_{spark_key}.py',
            conn_id='spark_default',
            packages='org.postgresql:postgresql:42.6.0,org.mongodb.spark:mongo-spark-connector_2.12:10.4.0',
            application_args=[TODAY_STR],
            conf={
                "spark.driver.memory": "1g",
                "spark.executor.memory": "1g",
                "spark.pyspark.python": "python3",
                "spark.pyspark.driver.python": "python3",
                "spark.pyspark.ignorePythonVersionMismatch": "true"
            }
        )
        # vlm task
        vlm_analyze_task = BashOperator(
            task_id=f'vlm_analyze_images_{crawler_key}',
            bash_command=f'python /opt/airflow/dags/scripts/vlm.py' 
        )
        # 텍스트 임베딩 Task
        mongo_connection_uri = "mongodb://datauser:DataPass2026!@mongo-main:27017/?authSource=admin"

        text_embed_task = BashOperator(
            task_id=f'generate_text_vectors_{crawler_key}',
            bash_command=f'python /opt/airflow/dags/scripts/text_embedder.py --mongo_uri "{mongo_connection_uri}" --brand_name {crawler_key} '
        )
        # ES 동기화 Task
        sync_es_task = BashOperator(
            task_id=f'sync_mongo_to_es_{crawler_key}',
            bash_command=f'python /opt/airflow/dags/scripts/sync_to_es.py --brand_name {crawler_key} ',
            dag=dag
        )

        # 파이프라인 흐름
        crawl_task >> spark_task >> vlm_analyze_task >> text_embed_task >> sync_es_task