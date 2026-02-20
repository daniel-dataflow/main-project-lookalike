from __future__ import annotations

import os
from typing import Any, Dict, Iterable

import numpy as np
import pandas as pd
from elasticsearch import Elasticsearch, helpers


def _create_index_if_needed(es: Elasticsearch, index: str, dims: int) -> None:
    if es.indices.exists(index=index):
        return

    mapping = {
        "mappings": {
            "properties": {
                "id": {"type": "keyword"},
                "product_id": {"type": "keyword"},
                "brand": {"type": "keyword"},
                "class_id": {"type": "integer"},
                "class_name": {"type": "keyword"},
                "confidence": {"type": "float"},
                "crop_path": {"type": "keyword"},
                "embedding": {
                    "type": "dense_vector",
                    "dims": dims,
                    "index": True,
                    "similarity": "cosine",
                },
            }
        }
    }
    es.indices.create(index=index, **mapping)


def _build_actions(index: str, embeddings: np.ndarray, meta: pd.DataFrame) -> Iterable[Dict[str, Any]]:
    for i, row in meta.iterrows():
        crop_path = str(row.get("crop_path", ""))

        doc = {
            "id": str(row["id"]),
            "product_id": str(row["product_id"]),  # int() 제거
            "brand": row.get("brand"),
            "class_id": int(row["class_id"]) if pd.notna(row.get("class_id")) else None,
            "class_name": row.get("class_name"),
            "confidence": float(row["confidence"]) if pd.notna(row.get("confidence")) else None,
            "crop_path": crop_path,
            "embedding": embeddings[i].tolist(),
        }

        yield {
            "_op_type": "update",
            "_index": index,
            "_id": str(row["id"]),
            "doc": doc,
            "doc_as_upsert": True,
        }


def upsert_embeddings_to_es(
    embedding_result: dict[str, Any],
    index: str | None = None,
    es_url: str | None = None,
    chunk_size: int = 500,
) -> dict[str, Any]:
    emb_path = embedding_result.get("embeddings_path") or embedding_result.get("embedding_path")
    meta_path = embedding_result.get("metadata_path")
    if not emb_path or not meta_path:
        raise ValueError("embedding_result must contain embeddings_path(or embedding_path) and metadata_path")

    index = index or os.getenv("ES_INDEX", "fashion_clip_v1")
    es_url = es_url or os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")

    embeddings = np.load(emb_path)
    meta = pd.read_csv(meta_path)

    if len(meta) != embeddings.shape[0]:
        raise ValueError(f"Row mismatch: metadata={len(meta)} embeddings={embeddings.shape[0]}")

    es = Elasticsearch(es_url)
    _create_index_if_needed(es=es, index=index, dims=embeddings.shape[1])

    actions = _build_actions(index=index, embeddings=embeddings, meta=meta)
    success, _ = helpers.bulk(es, actions, chunk_size=chunk_size, request_timeout=120, raise_on_error=False)
    es.indices.refresh(index=index)

    return {
        "index": index,
        "es_url": es_url,
        "upserted": int(success),
        "rows": int(len(meta)),
    }
