from __future__ import annotations
import json
from pathlib import Path
from airflow.decorators import task
# from elasticsearch import Elasticsearch, helpers


def _ensure_index(es: Elasticsearch, index_name: str, dims: int) -> None:
    """
    Elasticsearch 인덱스가 없으면 생성한다.
    - 메타데이터 필드는 keyword/date로 매핑
    - image_vector는 dense_vector로 매핑하고, cosine 유사도 기반 검색을 위해 index=True 설정

    Args:
        es: Elasticsearch client
        index_name: 생성/확인할 인덱스명
        dims: 벡터 차원 (embedding dimension)
    """
    
    # 인덱스 있으면 재생성 안함
    if es.indices.exists(index=index_name):
        return
    
    # 인덱스 생성, 매핑 정의
    es.indices.create(
        index=index_name,
        mappings={
            "properties": {
                # 상품 고유키
                "product_id": {"type": "keyword"},

                # 필터링용 메타데이터
                "brand": {"type": "keyword"},
                "gender": {"type": "keyword"},
                "category": {"type": "keyword"},
                "product_code": {"type": "keyword"},

                # 파일/경로 관련 정보
                "image_filename": {"type": "keyword"},
                "image_path": {"type": "keyword"},
                "origin_hdfs_path": {"type": "keyword"},

                # 생성 시각
                "create_dt": {"type": "date"},

                # 벡터 필드: kNN 검색을 위해 index=True
                # similarity=cosine 이므로, 검색 시 cosine 기반 점수 계산
                "image_vector": {
                    "type": "dense_vector",
                    "dims": dims,
                    "index": True,
                    "similarity": "cosine",
                },
            }
        },
    )


# ───────────────────────────────
# Airflow Task
# ───────────────────────────────
@task
def upsert_es(json_paths: list[str], es_url: str, index_name: str) -> int:
    """
    수행하는 일:
    - embed_to_json 단계에서 생성된 json 파일들을 읽어서
    - Elasticsearch index에 bulk upsert 형태로 적재한다.

    upsert의 핵심:
    - _id = product_id 로 지정 → 같은 product_id면 덮어쓰기(업데이트) 효과

    Args:
        json_paths: embedding json 파일 경로 리스트
        es_url: Elasticsearch 접속 URL (예: http://elasticsearch:9200)
        index_name: 적재할 인덱스명

    Returns:
        적재 시도한 문서 수
    """

    from elasticsearch import Elasticsearch, helpers
    
    if not json_paths:
        return 0

    # 첫 JSON을 읽어 벡터 차원(dims) 추출
    # → 인덱스 생성 시 dense_vector dims는 고정이라 반드시 필요
    first = json.loads(Path(json_paths[0]).read_text(encoding="utf-8"))
    dims = len(first["image_vector"])

    # ES 클라이언트 생성
    es = Elasticsearch(es_url)

    # 인덱스가 없으면 생성
    _ensure_index(es, index_name, dims)

    # bulk 작업을 위한 액션 리스트 구성
    actions = []
    for p in json_paths:
        # 각 json 파일 로드
        doc = json.loads(Path(p).read_text(encoding="utf-8"))

        # bulk index 액션
        # - _id를 product_id로 설정 → 동일 product_id면 upsert처럼 덮어씀
        actions.append(
            {
                "_index": index_name,
                "_id": doc["product_id"],
                "_source": doc,
            }
        )

    # bulk 적재
    # chunk_size는 한번에 전송할 문서 묶음 크기 (네트워크/메모리 트레이드오프)
    helpers.bulk(es, actions, chunk_size=200)
    
    return len(actions)
