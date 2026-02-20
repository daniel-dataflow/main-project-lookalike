from airflow.decorators import dag, task
from airflow.utils.dates import days_ago
from airflow.operators.python import get_current_context

from web.backend.pipeline.fetch_hdfs import fetch_images_from_hdfs
from web.backend.pipeline.yolo_infer import run_yolo_detect_classify
from web.backend.pipeline.embedder import run_embedding
from web.backend.pipeline.es_upsert import upsert_embeddings_to_es

@dag(
    dag_id="model_pipeline",
    start_date=days_ago(1),
    schedule=None,
    catchup=False,
    params={"brand": "topten", "limit": 200}, # limit: DB에서 가져올 최대 이미지 개수
    # Trigger DAG 눌러서 Config(JSON) 에 아래처럼 입력
    # {"brand":"zara","limit":200}
    # {"brand":"musinsa_standard","limit":200}
)
def model_pipeline():

    # 하둡(postgres)에서 이미지 파일 가져오기
    @task
    def fetch_from_hdfs():
        ctx = get_current_context()
        conf = (ctx.get("dag_run").conf or {}) if ctx.get("dag_run") else {}
        params = ctx["params"]
        brand = conf.get("brand", params["brand"])
        limit = int(conf.get("limit", params["limit"]))
        return fetch_images_from_hdfs(limit=limit, brand=brand)

    # 객체 탐지, 분류하기(크롭이미지 저장)
    @task
    def yolo_detect_classify(records):
        ctx = get_current_context()
        conf = (ctx.get("dag_run").conf or {}) if ctx.get("dag_run") else {}
        brand = conf.get("brand", ctx["params"]["brand"])
        return run_yolo_detect_classify(records=records, brand=brand)

    # fashion-CLIP 임베딩
    @task
    def embed(detections):
        ctx = get_current_context()
        conf = (ctx.get("dag_run").conf or {}) if ctx.get("dag_run") else {}
        brand = conf.get("brand", ctx["params"]["brand"])
        return run_embedding(detections=detections, brand=brand, batch_size=16)

    # 벡터DB에 저장
    @task
    def es_upsert(embedding_result):
        return upsert_embeddings_to_es(embedding_result)

    records = fetch_from_hdfs()
    detections = yolo_detect_classify(records)
    embedding = embed(detections)
    es_upsert(embedding)


model_pipeline()
