from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator
from datetime import datetime, timedelta

# ---------------------------------------------------------
# 1. TaskFlow API 함수 임포트 (scripts 경로로 수정 완료)
# ---------------------------------------------------------
# Airflow는 기본적으로 dags 폴더를 인식하므로 sys.path.append 없이 바로 scripts로 불러옵니다.
from scripts.fetch_from_hdfs import fetch_from_hdfs
from scripts.yolo_pipeline import yolo_reorganize_dedup_upsert
from scripts.embed_to_json import embed_to_json
from scripts.upsert_mongo import upsert_mongo
from scripts.upsert_es import upsert_es

# 설정값
CRAWLER_DIR = "/opt/airflow/dags/crawlers"
SPARK_DIR = "/opt/airflow/data-pipeline/spark/jobs"
TODAY_STR = "{{ ds_nodash }}"
MONGO_CONNECTION_URI = "mongodb://datauser:DataPass2026!@mongo-main:27017/?authSource=admin"

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
    description='Scrapy -> Spark -> YOLO/Embed -> VLM -> Text Embed -> ES',
    schedule_interval=None,
    catchup=False,
    tags=['fashion', 'full_logic', 'yolo'],
    max_active_tasks=1, 
) as dag:

    brands = {
        '8seconds': '8S',
    }

    for crawler_key, spark_key in brands.items():
        
        # =========================================================
        # [A] 기존 앞단 로직 (크롤링 -> 스파크 -> 네이버 가격)
        # =========================================================
        crawl_task = BashOperator(
            task_id=f'crawl_{crawler_key}',
            # 🚨 FIX: f-string 내부의 {{ ds_nodash }} 괄호 깨짐 현상을 TODAY_STR 변수로 해결
            bash_command=f'python3 {CRAWLER_DIR}/scraper_{crawler_key}.py {TODAY_STR}'
        )

        spark_task = SparkSubmitOperator(
            task_id=f'spark_process_{crawler_key}',
            application=f'{SPARK_DIR}/fashion_batch_job_{spark_key}.py',
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

        naver_price_task = BashOperator(
            task_id=f'fetch_naver_price_{crawler_key}',
            bash_command=f'pip install pymongo requests && python /opt/airflow/dags/scripts/naver_price_fetcher.py --brand_name {crawler_key}'
        )

        # =========================================================
        # [B] 새로 추가된 YOLO & 이미지 임베딩 로직 (TaskFlow API)
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
            batch_size=4,
        )

        mongo_done = upsert_mongo.override(task_id=f"upsert_mongo_{crawler_key}")(
            json_paths=json_paths,
            mongo_uri="{{ var.value.MONGO_URI }}", 
            db_name="fashion",
            collection="products",
        )

        es_done = upsert_es.override(task_id=f"upsert_es_{crawler_key}")(
            json_paths=json_paths,
            es_url="{{ var.value.ES_URL }}",
            index_name=f"vector_idx_{crawler_key}",
        )

        # =========================================================
        # [C] 기존 뒷단 로직 (VLM -> 텍스트 임베딩 -> ES 동기화)
        # =========================================================
        vlm_analyze_task = BashOperator(
            task_id=f'vlm_analyze_images_{crawler_key}',
            bash_command=f'python /opt/airflow/dags/scripts/vlm.py' 
        )

        text_embed_task = BashOperator(
            task_id=f'generate_text_vectors_{crawler_key}',
            bash_command=f'python /opt/airflow/dags/scripts/text_embedder.py --mongo_uri "{MONGO_CONNECTION_URI}" --brand_name {crawler_key} '
        )

        sync_es_task = BashOperator(
            task_id=f'sync_mongo_to_es_{crawler_key}',
            bash_command=f'python /opt/airflow/dags/scripts/sync_to_es.py --brand_name {crawler_key} ',
            dag=dag
        )

        # =========================================================
        # 🔗 [핵심] 파이프라인 흐름(의존성) 연결
        # =========================================================
        crawl_task >> spark_task >> naver_price_task >> fetched

        mongo_done >> vlm_analyze_task

        vlm_analyze_task >> text_embed_task >> sync_es_task
