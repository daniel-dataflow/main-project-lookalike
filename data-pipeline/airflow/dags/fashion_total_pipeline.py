import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from dotenv import load_dotenv

# ---------------------------------------------------------
# [TaskFlow API 함수 임포트] - (뒷단 로직용)
# ---------------------------------------------------------
from scripts.fetch_from_hdfs import fetch_from_hdfs
from scripts.yolo_pipeline import yolo_reorganize_dedup_upsert
from scripts.embed_to_json import embed_to_json
from scripts.upsert_mongo import upsert_mongo
from scripts.upsert_es import upsert_es

# .env 환경변수 로드 (네이버 API 키 등)
load_dotenv()

# ---------------------------------------------------------
# [경로 및 설정값 정의] - (경로 변경 절대 없음)
# ---------------------------------------------------------
CRAWLER_DIR = "/opt/airflow/data-pipeline/crawlers/web_crawlers"
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
    dag_id='fashion_total_pipeline_integrated',
    default_args=default_args,
    description='Crawl -> Spark -> Naver API -> YOLO -> VLM -> Text Embed -> ES 통합 파이프라인',
    schedule_interval=None,
    catchup=False,
    tags=['fashion', 'full_logic', 'integrated'],
    max_active_tasks=2, 
) as dag:

    # 테스트를 위해 uniqlo와 8seconds를 열어두었습니다. 필요에 따라 주석 해제하세요.
    brands = {
        # 'topten': 'TT',
        # '8seconds': '8S',
        # 'zara': 'ZR',
        'uniqlo': 'UQ',
        # 'musinsa': 'MS'
    }

    for crawler_key, spark_key in brands.items():
        
        # =========================================================
        # [STEP 1~3] 앞단 로직: 크롤링 -> 스파크 -> 네이버 최저가 API
        # =========================================================
        
        # 1. 크롤링 (Scrapy)
        crawl_task = BashOperator(
            task_id=f'crawl_{crawler_key}',
            bash_command=(
                f'PYTHONPATH={CRAWLER_DIR} '
                f'python3 {CRAWLER_DIR}/scraper_{crawler_key}.py {TODAY_STR}'
            )
        )

        # 2. Spark 처리 (DB 적재 및 HDFS 이미지 저장)
        spark_task = BashOperator(
            task_id=f"spark_process_{crawler_key}",
            bash_command=(
                "export SPARK_HOME=/home/airflow/.local/lib/python3.11/site-packages/pyspark && "
                "export PATH=$SPARK_HOME/bin:$PATH && "
                "spark-submit --master local[*] "
                "--packages org.postgresql:postgresql:42.6.0,"
                "org.mongodb.spark:mongo-spark-connector_2.12:10.3.0 " 
                f"{SPARK_DIR}/fashion_batch_job_{spark_key}.py "
                f"{TODAY_STR}"
            )
        )

        # 3. 네이버 최저가 API 수집
        naver_api_task = BashOperator(
            task_id=f'naver_api_{crawler_key}',
            bash_command=f'python3 {SPARK_DIR}/fashion_batch_job_NV.py',
            env={
                'DB_HOST': 'postgresql',
                'NAVER_CLIENT_ID': os.getenv('NAVER_CLIENT_ID', ""),
                'NAVER_CLIENT_SECRET': os.getenv('NAVER_CLIENT_SECRET', ""),
            },
            append_env=True 
        )

        # =========================================================
        # [STEP 4~7] 중간 로직: YOLO 객체 인식 & 이미지 임베딩 (TaskFlow)
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
        # [STEP 8~10] 뒷단 로직: VLM 텍스트 추출 -> 텍스트 임베딩 -> ES 동기화
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
            bash_command=f'python /opt/airflow/dags/scripts/sync_to_es.py --brand_name {crawler_key} '
        )

        # =========================================================
        # 🔗 [핵심] 파이프라인 전체 흐름(의존성) 연결
        # =========================================================
        # 1. 앞단 순차 실행 후 하둡에서 이미지 가져오기(fetched) 시작
        crawl_task >> spark_task >> naver_api_task >> fetched

        # (fetched -> final_rows -> json_paths -> mongo_done/es_done 는 TaskFlow API가 자동 연결함)

        # 2. Mongo와 ES 적재가 모두 끝나면 VLM 분석 시작
        [mongo_done, es_done] >> vlm_analyze_task

        # 3. VLM 텍스트 추출 -> 텍스트 임베딩 -> ES에 최종 업데이트
        vlm_analyze_task >> text_embed_task >> sync_es_task