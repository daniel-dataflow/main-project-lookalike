# fashion_total_pipeline_NV.py
import os
from airflow import DAG
from airflow.operators.bash import BashOperator
from datetime import datetime, timedelta
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator
from dotenv import load_dotenv

# 컨테이너 내부 경로 설정
CRAWLER_DIR = "/opt/airflow/data-pipeline/crawlers/web_crawlers"
SPARK_DIR = "/opt/airflow/data-pipeline/spark/jobs"

default_args = {
    'owner': 'lookalike',
    'depends_on_past': False,
    'start_date': datetime(2026, 2, 16),
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}


with DAG(
    dag_id='fashion_total_pipeline_NV',
    default_args=default_args,
    description='Crawl -> Spark -> Naver API 통합 파이프라인',
    schedule_interval=None,
    #schedule='0 2 * * *',
    catchup=False,
    tags=['fashion', 'full_logic'],
    max_active_tasks=5, 
) as dag:

    # 1. 브랜드 정의 (크롤러키: 스파크파일키)
    brands = {
        #'topten': 'TT',
        #'8seconds': '8S',
        'zara': 'ZR',
        'uniqlo': 'UQ',
        #'musinsa': 'MS'
    }

    for crawler_key, spark_key in brands.items():

        # [STEP 1] 크롤러 실행 (Scrapy 등)
        crawl_task = BashOperator(
            task_id=f'crawl_{crawler_key}',
            bash_command=(
                f'PYTHONPATH={CRAWLER_DIR} '
                f'python3 {CRAWLER_DIR}/scraper_{crawler_key}.py {{ ds_nodash }}'
            )
        )

        #[STEP 2] Spark 처리 (DB 적재 및 이미지 저장)-jemini
        spark_task = BashOperator(
            task_id=f"spark_process_{crawler_key}",
            bash_command=(
                "export SPARK_HOME=/home/airflow/.local/lib/python3.11/site-packages/pyspark && "
                "export PATH=$SPARK_HOME/bin:$PATH && "
                "spark-submit --master local[*] "
                "--packages org.postgresql:postgresql:42.6.0,"
                # 👇 버전을 10.1.1에서 10.3.0으로 변경합니다.
                "org.mongodb.spark:mongo-spark-connector_2.12:10.3.0 " 
                f"/opt/airflow/data-pipeline/spark/jobs/fashion_batch_job_{spark_key}.py "
                "{{ ds_nodash }}"
            )
        )

        #[STEP 2]  naver_api_8seconds 태스크 설정 부분
        naver_api_task = BashOperator(
            task_id=f'naver_api_{crawler_key}',
            bash_command=f'python3 {SPARK_DIR}/fashion_batch_job_NV.py',
            env={
                'DB_HOST': 'postgresql',
                # 뒤에 , "" 를 붙여서 None이 들어오는 것을 방지합니다.
                'NAVER_CLIENT_ID': os.getenv('NAVER_CLIENT_ID', ""),
                'NAVER_CLIENT_SECRET': os.getenv('NAVER_CLIENT_SECRET', ""),
            },
            # 중요: 시스템의 다른 기본 환경변수(PATH 등)를 누락시키지 않기 위해 추가
            append_env=True 
        )

        # 의존성 연결: 크롤링 -> 스파크 -> 네이버API
        crawl_task >> spark_task >> naver_api_task
