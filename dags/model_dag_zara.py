from __future__ import annotations
from datetime import datetime
from airflow.decorators import dag, task

import sys
# tasks 폴더 경로 추가
sys.path.append("/opt/airflow")

# 태스크 함수는 별도 파일에서 import
from tasks.fetch_from_hdfs import fetch_from_hdfs
from tasks.yolo_pipeline import yolo_reorganize_dedup_upsert
from tasks.embed_to_json import embed_to_json
from tasks.upsert_mongo import upsert_mongo
from tasks.upsert_es import upsert_es


@dag(
    dag_id="model_dag_zara",
    schedule=None,                  # 수동 실행
    start_date=datetime(2026, 2, 19),
    catchup=False,
    tags=["zara", "yolo", "embedding"],
)
def model_dag_zara():
    # 1) HDFS 원본 -> 로컬 입력 폴더
    fetched = fetch_from_hdfs.override(task_id="fetch_from_hdfs")(
        brand="zara",
        hdfs_root="/data/fashion/raw/zara",
        incoming_dir="/opt/airflow/data/incoming/zara",
        limit=5,
    )

    # 2) YOLO -> 폴더정리 -> 상품중복제거 -> Postgres upsert
    # 최종 출력: 정제된 crop 메타 리스트
    final_rows = yolo_reorganize_dedup_upsert.override(task_id="yolo_reorganize_dedup_upsert")(
        records=fetched,
        brand="zara",
        crop_tmp_dir="/opt/airflow/data/crops_tmp",
        crop_final_dir="/opt/airflow/data/crops_final",
        model_path="/opt/airflow/model/best.pt",
    )

    # 3) 임베딩 -> 상품당 1개 JSON 생성
    json_paths = embed_to_json.override(task_id="embed_to_json")(
        rows=final_rows,
        out_root="/opt/airflow/data/embeddings_json",
        batch_size=16,
    )

    # 4) JSON -> Mongo upsert (_id=product_id)
    mongo_done = upsert_mongo.override(task_id="upsert_mongo")(
        json_paths=json_paths,
        mongo_uri="{{ var.value.MONGO_URI }}",
        db_name="fashion",
        collection="products",
    )

    # 5) JSON -> ES upsert (_id=product_id, image_vector)
    es_done = upsert_es.override(task_id="upsert_es")(
        json_paths=json_paths,
        es_url="{{ var.value.ES_URL }}",
        index_name="vector_idx_zara",
    )

    # Mongo/ES는 병렬 실행
    [mongo_done, es_done]


dag = model_dag_zara()
