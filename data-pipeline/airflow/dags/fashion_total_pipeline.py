# fashion_total_pipeline.py

from airflow import DAG
import os
from airflow.operators.bash import BashOperator
#from airflow.operators.python import PythonOperator
from datetime import datetime, timedelta
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator

# 실제 컨테이너 내부 경로
CRAWLER_DIR = "/opt/airflow/data-pipeline/crawlers/web_crawlers"
SPARK_DIR = "/opt/airflow/data-pipeline/spark/jobs"
#CRAWLER_DIR = "/home/lookalike/main-project-lookalike/data-pipeline/crawlers/web_crawlers"
#SPARK_DIR = "/home/lookalike/main-project-lookalike/data-pipeline/spark/jobs"

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
    schedule_interval=None,  # 수동 실행용
    #schedule='0 2 * * *',
    catchup=False,
    tags=['fashion', 'full_logic'],
    max_active_tasks=1, 
) as dag:

    brands = {
        'topten': 'TT',
        #'8seconds': '8S',
        #'zara': 'ZR',
        #'uniqlo': 'UQ',
        #'musinsa': 'MS'
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

        # spark_task = SparkSubmitOperator(
        #     task_id=f'spark_process_{crawler_key}',
        #     application=f"{SPARK_DIR}/fashion_batch_job_{spark_key}.py",
        #     conn_id="spark_default",
        #     master="spark://spark-master-main:7077",
        #     spark_binary="/home/airflow/.local/bin/spark-submit",
        #     application_args=["{{ ds_nodash }}"],
        #     conf={
        #         "spark.driver.memory": "1g",
        #         "spark.executor.memory": "1g",
        #         "spark.jars.packages": "org.postgresql:postgresql:42.6.0,org.mongodb.spark:mongo-spark-connector_2.12:10.1.1"
        #     }
        # )

        from airflow.operators.bash import BashOperator

# ... 기존 코드 생략 ...

        spark_task = BashOperator(
            task_id="spark_process",
            bash_command=(
                # 1. 환경 변수 설정: pyspark가 설치된 실제 경로를 지정합니다.
                "export SPARK_HOME=/home/airflow/.local/lib/python3.11/site-packages/pyspark && "
                "export PATH=$SPARK_HOME/bin:$PATH && "
                "export PYSPARK_PYTHON=python3 && "
                
                # 2. 이제 에어플로우 내부의 spark-submit을 직접 실행합니다.
                "spark-submit "
                "--master local[*] "
                "--packages org.postgresql:postgresql:42.6.0,"
                "org.mongodb.spark:mongo-spark-connector_2.12:10.1.1 "
                
                # 3. 실행할 파이썬 파일의 에어플로우 컨테이너 내 경로
                "/opt/airflow/data-pipeline/spark/jobs/fashion_batch_job_TT.py {{ ds_nodash }}"
            )
        )



        #crawl_task 
        spark_task
        #crawl_task >> spark_task
