import os
from datetime import datetime, timedelta

from airflow import DAG
from dotenv import load_dotenv

# ---------------------------------------------------------
# [1] Task 모듈 임포트
# ---------------------------------------------------------

from tasks.operator_tasks import get_crawl_task, get_spark_task, get_naver_api_task
from tasks.yolo_tasks import yolo_reorganize_dedup_upsert
from tasks.embed_tasks import embed_to_json
from tasks.vlm_tasks import extract_text_vlm
from tasks.text_embed_tasks import embed_text_vectors

from tasks.db_tasks import (
    fetch_from_hdfs,
    upsert_mongo,
    upsert_mongo_text_data,
    sync_mongo_to_es,
    refine_prices_task
)
# .env 환경변수 로드
load_dotenv()

# ---------------------------------------------------------
# [2] DAG 설정
# ---------------------------------------------------------
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
    description='Crawl -> Spark -> Naver API -> YOLO -> Image Embed -> Mongo -> VLM(Text Embed) -> ES 통합 파이프라인',
    schedule_interval=None,
    catchup=False,
    tags=['fashion', 'full_logic', 'integrated'],
    max_active_tasks=2, 
) as dag:

    brands = {
        '8seconds': '8S',
        'topten': 'TT',
        'zara': 'ZR',
        'uniqlo': 'UQ',
        'musinsa': 'MS'
    }

    global_price_refine = refine_prices_task.override(task_id="global_price_refinement")()
    
    # 의존성 묶기
    naver_api_tasks = []
    fetch_tasks = []

    for crawler_key, spark_key in brands.items():
        
        # =========================================================
        # [STEP 1~3] 데이터 수집 및 기본 전처리 (BashOperator)
        # =========================================================
        crawl_task = get_crawl_task(crawler_key, TODAY_STR)
        spark_task = get_spark_task(crawler_key, spark_key, TODAY_STR)
        naver_api_task = get_naver_api_task(crawler_key)

        
        # 1차 파이프라인 연결: 크롤링 -> 스파크 -> 네이버 API
        crawl_task >> spark_task >> naver_api_task

        naver_api_tasks.append(naver_api_task)

        # =========================================================
        # [STEP 4~7] 이미지 분석 및 몽고DB 적재 (TaskFlow API)
        # =========================================================
        
        fetched = fetch_from_hdfs.override(task_id=f"fetch_from_hdfs_{crawler_key}")(
            brand=crawler_key,
            hdfs_root=f"/raw/{crawler_key}/image",
            incoming_dir=f"/opt/airflow/data/incoming/{crawler_key}",
        )

        fetch_tasks.append(fetched)

        final_rows = yolo_reorganize_dedup_upsert.override(task_id=f"yolo_{crawler_key}")(
            records=fetched,
            brand=crawler_key,
            crop_tmp_dir="/opt/airflow/data-pipeline/database/yolo-output/crops_tmp",
            crop_final_dir="/opt/airflow/data-pipeline/database/yolo-output/crops_final",
            model_path="/opt/airflow/model/best.pt",
        )

        image_json_paths = embed_to_json.override(task_id=f"embed_image_{crawler_key}")(
            rows=final_rows,
            out_root="/opt/airflow/data/embeddings_json",
        )

        mongo_done = upsert_mongo.override(task_id=f"upsert_image_mongo_{crawler_key}")(
            json_paths=image_json_paths,
            mongo_uri="{{ var.value.MONGO_URI }}", 
            db_name="datadb",
            collection="analyzed_metadata",
        )

        # =========================================================
        # [STEP 8~11] VLM 텍스트 추출 및 임베딩 -> ElasticSearch 동기화
        # =========================================================
        vlm_json_paths = extract_text_vlm.override(task_id=f"extract_vlm_{crawler_key}")(
            brand_name=crawler_key,
            hdfs_url="http://namenode:9870",
            ollama_host="http://172.31.11.240:11434",
            model_name="gemma3:4b",
            out_dir="/opt/airflow/data/vlm_checkpoints"
        )

        embedded_json_paths = embed_text_vectors.override(task_id=f"embed_text_{crawler_key}")(
            json_paths=vlm_json_paths,
            model_name="jhgan/ko-sroberta-sts"
        )

        mongo_text_done = upsert_mongo_text_data.override(task_id=f"upsert_text_mongo_{crawler_key}")(
            json_paths=embedded_json_paths,
            mongo_uri="{{ var.value.MONGO_URI }}"
        )

        es_sync_done = sync_mongo_to_es.override(task_id=f"sync_es_{crawler_key}")(
            brand_name=crawler_key,
            mongo_uri="{{ var.value.MONGO_URI }}",
            es_uri="http://elasticsearch-main:9200"
        )
        # 2. 이미지 적재 완료 후 -> VLM 텍스트 분석 시작
        mongo_done >> vlm_json_paths

        # 3. 텍스트 적재 완료 후 -> ElasticSearch 최종 동기화 시작
        mongo_text_done >> es_sync_done

        naver_api_tasks >> global_price_refine >> fetch_tasks

        # (참고: fetched -> final_rows -> image_json_paths -> mongo_done 과
        # vlm_json_paths -> embedded_json_paths -> mongo_text_done 은
        # TaskFlow API가 함수의 리턴값을 인자로 받아가며 자동으로 연결합니다.)
