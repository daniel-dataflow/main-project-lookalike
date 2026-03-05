import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from dotenv import load_dotenv

# ---------------------------------------------------------
# [TaskFlow API 함수 임포트]
# ---------------------------------------------------------
from scripts.fetch_from_hdfs import fetch_from_hdfs
from scripts.yolo_pipeline import yolo_reorganize_dedup_upsert
from scripts.embed_to_json import embed_to_json
from scripts.upsert_mongo import upsert_mongo

# .env 환경변수 로드 (네이버 API 키 등)
load_dotenv()

# ---------------------------------------------------------
# [경로 및 상수 정의]
# ---------------------------------------------------------
CRAWLER_DIR = "/opt/airflow/data-pipeline/crawlers/web_crawlers"
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
    dag_id='fashion_total_pipeline_integrated',
    default_args=default_args,
    description='Crawl -> Spark -> Naver API -> YOLO -> Image Embed -> Mongo -> VLM(Text Embed) -> ES 통합 파이프라인',
    schedule_interval=None,
    catchup=False,
    tags=['fashion', 'full_logic', 'integrated'],
    max_active_tasks=2, 
) as dag:

    # 테스트를 위해 8seconds만 활성화. 필요시 주석 해제하여 병렬 확장 가능!
    brands = {
        '8seconds': '8S',
        # 'topten': 'TT',
        # 'zara': 'ZR',
        # 'uniqlo': 'UQ',
        # 'musinsa': 'MS'
    }

    for crawler_key, spark_key in brands.items():
        
        # =========================================================
        # [STEP 1~3] 데이터 수집 및 기본 전처리 (Crawler, Spark, Naver API)
        # =========================================================
        
        # 1. 크롤링 (Playwright/Selenium)
        if crawler_key == 'zara':
            crawl_cmd = f'PYTHONPATH={CRAWLER_DIR} xvfb-run -a python3 -u {CRAWLER_DIR}/scraper_{crawler_key}.py {TODAY_STR}'
        else:
            crawl_cmd = f'PYTHONPATH={CRAWLER_DIR} python3 -u {CRAWLER_DIR}/scraper_{crawler_key}.py {TODAY_STR}'

        crawl_task = BashOperator(
            task_id=f'crawl_{crawler_key}',
            bash_command=crawl_cmd
        )


        # 2. Spark DB 적재 및 HDFS 파일 저장
        spark_task = BashOperator(
            task_id=f"spark_process_{crawler_key}",
            bash_command=(
                "export SPARK_HOME=/home/airflow/.local/lib/python3.11/site-packages/pyspark && "
                "export PATH=$SPARK_HOME/bin:$PATH && "
                "spark-submit --master local[*] "
                "--packages org.postgresql:postgresql:42.6.0,org.mongodb.spark:mongo-spark-connector_2.12:10.3.0 " 
                f"{SPARK_DIR}/fashion_batch_job_{spark_key}.py {TODAY_STR}"
            )
        )

        # 3. 네이버 최저가 API 수집
        naver_api_task = BashOperator(
            task_id=f'naver_api_{crawler_key}',
            bash_command=f'python3 -u {SPARK_DIR}/fashion_batch_job_NV.py',
            env={
                'DB_HOST': 'postgresql',
                'NAVER_CLIENT_ID': os.getenv('NAVER_CLIENT_ID', ""),
                'NAVER_CLIENT_SECRET': os.getenv('NAVER_CLIENT_SECRET', ""),
            },
            append_env=True 
        )

        # =========================================================
        # [STEP 4~7] 이미지 분석 및 몽고DB 적재 (TaskFlow API)
        # =========================================================
        
        fetched = fetch_from_hdfs.override(task_id=f"fetch_from_hdfs_{crawler_key}")(
            brand=crawler_key,
            hdfs_root=f"/raw/{crawler_key}/image",
            incoming_dir=f"/opt/airflow/data/incoming/{crawler_key}",
        )

        final_rows = yolo_reorganize_dedup_upsert.override(task_id=f"yolo_reorganize_dedup_upsert_{crawler_key}")(
            records=fetched,
            brand=crawler_key,
            crop_tmp_dir="/opt/airflow/data/crops_tmp",
            crop_final_dir="/opt/airflow/data/crops_final",
            model_path="/opt/airflow/model/best.pt",
        )

        json_paths = embed_to_json.override(task_id=f"embed_to_json_{crawler_key}")(
            rows=final_rows,
            out_root="/opt/airflow/data/embeddings_json",
        )

        mongo_done = upsert_mongo.override(task_id=f"upsert_mongo_{crawler_key}")(
            json_paths=json_paths,
            mongo_uri="{{ var.value.MONGO_URI }}", 
            db_name="datadb",
            collection="analyzed_metadata",
        )

        # =========================================================
        # [STEP 8~9] VLM 텍스트 추출 및 임베딩 -> ElasticSearch 동기화
        # =========================================================
        
        vlm_analyze_task = BashOperator(
            task_id=f'vlm_analyze_images_{crawler_key}',
            bash_command=f'python3 -u /opt/airflow/dags/scripts/vlm.py --brand_name {crawler_key}'
        )
        sync_es_task = BashOperator(
            task_id=f'sync_mongo_to_es_{crawler_key}',
            bash_command=f'python3 -u /opt/airflow/dags/scripts/sync_to_es.py --brand_name {crawler_key}'
        )

        # =========================================================
        # 🔗 [핵심] 파이프라인 전체 흐름(의존성) 연결
        # =========================================================
        # 참고: TaskFlow API(fetched -> final_rows -> json_paths -> mongo_done)는
        # 함수의 인자 전달을 통해 에어플로우가 자동으로 의존성을 연결합니다.
        
        # 1. 앞단 로직 연결 (크롤링 -> 스파크 -> API -> HDFS 다운로드 시작)
        crawl_task >> spark_task >> naver_api_task >> fetched

        # 2. 뒷단 로직 연결 (몽고DB 적재 완료 -> VLM/임베딩 -> ES 동기화)
        mongo_done >> vlm_analyze_task >> sync_es_task
