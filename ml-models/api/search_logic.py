from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

from elasticsearch import Elasticsearch


# 인코더 함수 시그니처
# - clip_image_encoder(image) -> list[float] (dim=512)
# - sbert_text_encoder(text) -> list[float] (dim=384)
EncoderImage = Callable[[Any], List[float]]
EncoderText = Callable[[str], List[float]]
RewriteText = Callable[[str], str]


@dataclass
class SearchConfig:
    index_name: str = "products"
    es_url: str = "http://elasticsearch:9200"
    clip_field: str = "image_vector"
    sbert_field: str = "text_vector"
    # FastAPI 백엔드 DB(products.model_code)와 직접 조인되도록 product_code를 기본 키로 사용
    id_field: str = "product_code"

    # 결과 후보 수와 최종 반환 수
    # - k_clip: 이미지 메인 검색의 1차 후보 수(2-stage에서 Top N)
    k_clip: int = 300
    k_sbert: int = 500
    final_k: int = 6

    # Late Fusion 가중치
    w_clip: float = 0.7
    w_sbert: float = 0.3

    # "rrf"(권장) 또는 "minmax"
    fusion_method: str = "rrf"

    # RRF 상수. 보통 60 근처를 많이 사용
    rrf_k: int = 60

    # ES knn 후보 수
    num_candidates: int = 1000


class SearchService:
    """
    Image-main + Optional Text(SBERT) + Category Filter + Late Fusion 검색 서비스.

    핵심 정책(최종)
    - image + text: 2-stage
      1) CLIP으로 Top N 후보 추출
      2) 해당 후보 ID 집합에 한정해 SBERT 재검색 후 late fusion
    - image only: CLIP 단독 검색
    - text only: SBERT 단독 검색
    - 둘 다 없음: 사용자 입력 요청 예외 발생
    """

    def __init__(
        self,
        config: SearchConfig,
        clip_image_encoder: EncoderImage,
        sbert_text_encoder: EncoderText,
        # Optional hook: 검색 전 텍스트 정규화/리라이트를 수행한다.
        query_rewriter: Optional[RewriteText] = None,
    ) -> None:
        self.cfg = config
        self.es = Elasticsearch(config.es_url)
        self.clip_image_encoder = clip_image_encoder
        self.sbert_text_encoder = sbert_text_encoder
        self.query_rewriter = query_rewriter

    def search(
        self,
        image: Any = None,
        text: Optional[str] = None,
        category: Optional[str | Sequence[str]] = None,
        gender: Optional[str | Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        # 0) non-vector 필터 구성
        filters = self._build_filters(category, gender)

        # 1) query vector 생성
        q_clip: Optional[List[float]] = None
        q_sbert: Optional[List[float]] = None

        if image is not None:
            q_clip = self._l2_normalize(self.clip_image_encoder(image))

        if text is not None and text.strip():
            text_for_embedding = text.strip()
            if self.query_rewriter is not None:
                try:
                    # rewrite 결과가 비어 있지 않을 때만 임베딩 입력으로 사용한다.
                    rewritten = self.query_rewriter(text_for_embedding).strip()
                    if rewritten:
                        text_for_embedding = rewritten
                except Exception:
                    # rewrite 실패 시 검색 중단 없이 원문 임베딩으로 fallback
                    pass
            q_sbert = self._l2_normalize(self.sbert_text_encoder(text_for_embedding))

        # 쿼리가 비어 있으면 프론트에서 경고창을 띄울 수 있도록 명시적 예외를 준다.
        if q_clip is None and q_sbert is None:
            raise ValueError("검색 이미지나 텍스트를 입력해주세요.")

        # Case A) image only -> CLIP 단독 top-6
        if q_clip is not None and q_sbert is None:
            clip_hits = self._knn_search(
                field=self.cfg.clip_field,
                query_vector=q_clip,
                k=self.cfg.k_clip,
                filters=filters,
            )
            return clip_hits[: self.cfg.final_k]

        # Case B) text only -> SBERT 단독 top-6
        if q_clip is None and q_sbert is not None:
            sbert_hits = self._knn_search(
                field=self.cfg.sbert_field,
                query_vector=q_sbert,
                k=self.cfg.k_sbert,
                filters=filters,
            )
            return sbert_hits[: self.cfg.final_k]

        # Case C) image + text -> 2-stage
        clip_hits = self._knn_search(
            field=self.cfg.clip_field,
            query_vector=q_clip,  # type: ignore[arg-type]
            k=self.cfg.k_clip,
            filters=filters,
        )
        if not clip_hits:
            return []

        # 1차 후보 ID에 대해서만 SBERT 재검색(후보 제한)
        candidate_ids = [h["id"] for h in clip_hits if h.get("id")]
        candidate_k = min(len(candidate_ids), self.cfg.k_clip)
        sbert_hits = self._knn_search(
            field=self.cfg.sbert_field,
            query_vector=q_sbert,  # type: ignore[arg-type]
            k=candidate_k,
            filters=filters,
            candidate_ids=candidate_ids,
        )

        # 2단계 결과 결합
        if self.cfg.fusion_method == "minmax":
            fused = self._late_fusion_minmax(clip_hits, sbert_hits)
        else:
            # 기본값은 안정적인 RRF 사용
            fused = self._late_fusion_rrf(clip_hits, sbert_hits)

        return fused[: self.cfg.final_k]

    def _build_filters(
        self, category: Optional[str | Sequence[str]], gender: Optional[str | Sequence[str]] = None
    ) -> List[Dict[str, Any]]:
        filters: List[Dict[str, Any]] = []

        if category is not None:
            if isinstance(category, str):
                filters.append({"term": {"category": category}})
            else:
                values = [c for c in category if c]
                if values:
                    filters.append({"terms": {"category": values}})
                    
        if gender is not None:
            if isinstance(gender, str):
                filters.append({"term": {"gender": gender.lower()}})
            else:
                values = [g.lower() for g in gender if g]
                if values:
                    filters.append({"terms": {"gender": values}})

        return filters

    def _knn_search(
        self,
        field: str,
        query_vector: List[float],
        k: int,
        filters: List[Dict[str, Any]],
        candidate_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        # ES 8.x knn 쿼리 + filter 적용
        knn: Dict[str, Any] = {
            "field": field,
            "query_vector": query_vector,
            "k": k,
            "num_candidates": self.cfg.num_candidates,
        }

        all_filters = list(filters)
        if candidate_ids:
            # 2-stage에서 1차 후보 ID 집합으로 2차 검색 범위를 제한한다.
            all_filters.append({"terms": {self.cfg.id_field: candidate_ids}})

        if all_filters:
            knn["filter"] = {"bool": {"filter": all_filters}}

        body = {
            "knn": knn,
            # 반환 필드는 필요한 최소로 제한
            "_source": [
                self.cfg.id_field,
                "brand",
                "category",
                "product_code",
                "image_path",
                "image_filename",
            ],
        }

        resp = self.es.search(index=self.cfg.index_name, body=body)
        hits = resp.get("hits", {}).get("hits", [])

        out: List[Dict[str, Any]] = []
        for rank, h in enumerate(hits, start=1):
            src = h.get("_source", {})
            out.append(
                {
                    "id": src.get(self.cfg.id_field) or h.get("_id"),
                    "score": float(h.get("_score", 0.0)),
                    "rank": rank,
                    "doc": src,
                }
            )

        return out

    # 혹시 late fusion 정규화 방식 바뀔 경우 대비용
    def _late_fusion_minmax(
        self,
        clip_hits: List[Dict[str, Any]],
        sbert_hits: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        # min-max는 직관적이지만 outlier에 민감함.
        # 그래도 필요할 때 비교 실험용으로 유지한다.
        by_id: Dict[str, Dict[str, Any]] = {}

        for h in clip_hits:
            by_id.setdefault(h["id"], {})["clip_score"] = h["score"]
            by_id[h["id"]]["doc"] = h.get("doc")

        for h in sbert_hits:
            by_id.setdefault(h["id"], {})["sbert_score"] = h["score"]
            by_id[h["id"]]["doc"] = h.get("doc")

        clip_scores = [h["score"] for h in clip_hits]
        sbert_scores = [h["score"] for h in sbert_hits]

        def _minmax(x: float, mn: float, mx: float) -> float:
            if mx - mn < 1e-12:
                return 0.0
            return (x - mn) / (mx - mn)

        clip_has = len(clip_scores) > 0
        sbert_has = len(sbert_scores) > 0
        clip_min = min(clip_scores) if clip_has else 0.0
        clip_max = max(clip_scores) if clip_has else 1.0
        sbert_min = min(sbert_scores) if sbert_has else 0.0
        sbert_max = max(sbert_scores) if sbert_has else 1.0

        fused: List[Dict[str, Any]] = []
        for pid, v in by_id.items():
            c = v.get("clip_score")
            s = v.get("sbert_score")
            c_norm = _minmax(c, clip_min, clip_max) if c is not None and clip_has else 0.0
            s_norm = _minmax(s, sbert_min, sbert_max) if s is not None and sbert_has else 0.0
            final_score = self.cfg.w_clip * c_norm + self.cfg.w_sbert * s_norm
            fused.append({"id": pid, "score": final_score, "doc": v.get("doc")})

        fused.sort(key=lambda x: x["score"], reverse=True)
        return fused

    def _late_fusion_rrf(
        self,
        clip_hits: List[Dict[str, Any]],
        sbert_hits: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        # RRF는 점수 분포가 다른 모델들을 합칠 때 더 안정적이다.
        # score 대신 rank 기반으로 결합: 1/(k + rank)
        by_id: Dict[str, Dict[str, Any]] = {}

        for h in clip_hits:
            pid = h["id"]
            by_id.setdefault(pid, {})["clip_rank"] = h["rank"]
            by_id[pid]["doc"] = h.get("doc")

        for h in sbert_hits:
            pid = h["id"]
            by_id.setdefault(pid, {})["sbert_rank"] = h["rank"]
            by_id[pid]["doc"] = h.get("doc")

        fused: List[Dict[str, Any]] = []
        rrf_k = self.cfg.rrf_k

        for pid, v in by_id.items():
            clip_rank = v.get("clip_rank")
            sbert_rank = v.get("sbert_rank")

            clip_rrf = 1.0 / (rrf_k + clip_rank) if clip_rank is not None else 0.0
            sbert_rrf = 1.0 / (rrf_k + sbert_rank) if sbert_rank is not None else 0.0

            final_score = self.cfg.w_clip * clip_rrf + self.cfg.w_sbert * sbert_rrf
            fused.append({"id": pid, "score": final_score, "doc": v.get("doc")})

        fused.sort(key=lambda x: x["score"], reverse=True)
        return fused

    @staticmethod
    def _l2_normalize(vec: List[float]) -> List[float]:
        # cosine 기반 검색 시 query vector 정규화를 명시적으로 수행한다.
        # (모델 출력이 이미 정규화라면 영향은 거의 없고, 미정규화라면 안정화에 도움)
        s = 0.0
        for x in vec:
            s += x * x
        if s <= 0.0:
            return vec
        norm = s ** 0.5
        return [x / norm for x in vec]


if __name__ == "__main__":
    # 이 파일은 라이브러리 형태로 쓰는 것을 기본으로 한다.
    # 아래는 최소 사용 예시이다.
    def _dummy_clip_encoder(_: Any) -> List[float]:
        raise NotImplementedError("clip_image_encoder를 실제 모델 함수로 연결하세요.")

    def _dummy_sbert_encoder(_: str) -> List[float]:
        raise NotImplementedError("sbert_text_encoder를 실제 모델 함수로 연결하세요.")

    cfg = SearchConfig(final_k=5, w_clip=0.7, w_sbert=0.3, fusion_method="rrf")
    service = SearchService(cfg, _dummy_clip_encoder, _dummy_sbert_encoder)
    print("SearchService initialized. Import and call service.search(...)")
