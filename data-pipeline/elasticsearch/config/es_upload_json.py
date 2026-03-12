from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Sequence

from elasticsearch import Elasticsearch, helpers


# .json파일들의 경로를 리스트로 반환
def _resolve_json_files(input_path: str) -> List[Path]:
    # --input 값이 파일/디렉터리/글롭패턴 중 무엇이든 받을 수 있게 처리한다.
    p = Path(input_path)
    if p.exists():
        if p.is_file():
            return [p]
        if p.is_dir():
            # 디렉터리면 하위의 모든 .json 파일을 재귀 탐색한다.
            return sorted(x for x in p.rglob("*.json") if x.is_file())

    # 경로가 실제로 존재하지 않으면 글롭 패턴으로 해석한다.
    matches = sorted(Path(x) for x in glob.glob(input_path, recursive=True))
    return [m for m in matches if m.is_file() and m.suffix.lower() == ".json"]


def _iter_docs_from_file(path: Path) -> Iterator[Dict[str, Any]]:
    # 한 파일이 "문서{} 1개(dict)" 또는 "문서 배열(list[dict])" 두 형식을 모두 허용한다.
    raw = json.loads(path.read_text(encoding="utf-8"))

    # dict 하나
    if isinstance(raw, dict):
        yield raw  # 내보내고 멈춤, 리턴x
        return
    # dict들의 리스트
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError(f"{path}: list item is not an object")
            yield item
        return
    # 둘 다 아니면 형식 오류
    raise ValueError(f"{path}: json root must be object or array")


def _iter_all_docs(files: Sequence[Path]) -> Iterator[Dict[str, Any]]:
    # 입력 파일들을 순회하며 문서{}를 스트리밍 형태로 전달한다.
    # (모든 문서를 메모리에 한 번에 올리지 않기 위함)
    for f in files:
        yield from _iter_docs_from_file(f) # 모든 문서를 이어서 yield


# 파싱 실패로 기록된 문서는 적재 대상에서 제외할 때 사용
def _is_error_doc(doc: Dict[str, Any]) -> bool:
    return "error" in doc and doc.get("error") is not None


# 파일명에서 상품코드 추출
def _extract_id_from_filename(filename: str) -> str:
    # 예: zara_Men_Bottom_00108309_0.jpg -> "00108309"
    stem = Path(filename).stem
    parts = stem.split("_")
    
    # 길이가 길면, 숫자가 포함된 메인 코드가 대개 끝에서 두 번째에 위치 (예: ...00108309_0.jpg)
    if len(parts) >= 2:
        # '_0' 같은 접미사가 붙었다면 그 앞부분이 본품 코드
        code_str = parts[-2] if parts[-1].isdigit() and len(parts[-1]) <= 2 else parts[-1]
    else:
        code_str = stem

    return code_str


# product_id는 브랜드명과 상품코드 조합으로 만든다. 브랜드명이 없으면 상품코드만 사용.
def _build_product_id(brand: Any, code: str) -> str:
    if isinstance(brand, str) and brand.strip():
        return f"{brand.strip()}_{code}"
    return code


# ES에 올라갈 문서의 필드명과 구조 정규화
def _normalize_doc(
    doc: Dict[str, Any],
    id_field: str,
    derive_id_from_filename: bool,
    filename_field: str,
) -> Dict[str, Any]:
    
    out = dict(doc)
    
    # Elasticsearch의 예약어 메타데이터 필드 `_id`가 문서 내부에 포함되면 파싱 에러(illegal_argument_exception) 발생하므로 제거
    out.pop("_id", None)

    # nested basic_info({}안에 {})를 평탄화해서 검색/필터 필드로 통일한다.
    basic_info = out.get("basic_info")
    if isinstance(basic_info, dict):
        if "brand" not in out and basic_info.get("brand") is not None:
            out["brand"] = basic_info.get("brand")
        if "gender" not in out and basic_info.get("gender") is not None:
            out["gender"] = basic_info.get("gender")
        if "category" not in out and basic_info.get("original_category") is not None:
            out["category"] = basic_info.get("original_category")
            
        # ES 매핑 충돌(object 타입과 keyword 타입 혼재) 방지를 위해 dict 형식이면 문자열로 직렬화
        for k_prevent in ["origin_hdfs_path", "image_path"]:
            val = basic_info.get(k_prevent)
            if isinstance(val, dict):
                basic_info[k_prevent] = json.dumps(val, ensure_ascii=False)

    # 파일명 기반 product_code/product_id 추출
    filename = out.get(filename_field)
    if isinstance(filename, str) and filename.strip():
        code = _extract_id_from_filename(filename.strip())
        out.setdefault("product_code", code)
        if derive_id_from_filename and id_field not in out:
            out[id_field] = _build_product_id(out.get("brand"), code)
        out.setdefault("image_filename", filename.strip())

    # product_code가 이미 있으면 id로 승격 가능
    if derive_id_from_filename and id_field not in out and out.get("product_code") is not None:
        out[id_field] = _build_product_id(out.get("brand"), str(out["product_code"]))

    return out


# id_field 기준으로 문서들을 읽어와 딕셔너리로 반환 (id_field 값이 키가 됨)
def _load_docs_by_id(
    files: Sequence[Path],
    id_field: str,
    label: str,
    derive_id_from_filename: bool,
    filename_field: str,
    skip_error_docs: bool,
) -> Dict[str, Dict[str, Any]]:
    docs_by_id: Dict[str, Dict[str, Any]] = {}
    for doc in _iter_all_docs(files):
        if skip_error_docs and _is_error_doc(doc):
            continue
        doc = _normalize_doc(
            doc,
            id_field=id_field,
            derive_id_from_filename=derive_id_from_filename,
            filename_field=filename_field,
        )
        if id_field not in doc:
            raise ValueError(f"{label} doc missing id field '{id_field}': {doc}")
        doc_id = str(doc[id_field])
        docs_by_id[doc_id] = doc
    return docs_by_id


#
def _merge_docs_by_id(
    image_docs: Dict[str, Dict[str, Any]],
    text_docs: Dict[str, Dict[str, Any]],
    merge_mode: str,
) -> List[Dict[str, Any]]:
    if merge_mode == "inner":
        keys = image_docs.keys() & text_docs.keys()
    elif merge_mode == "left":
        keys = image_docs.keys()
    elif merge_mode == "right":
        keys = text_docs.keys()
    else:  # outer
        keys = image_docs.keys() | text_docs.keys()

    merged: List[Dict[str, Any]] = []
    for k in keys:
        base = dict(image_docs.get(k, {}))
        base.update(text_docs.get(k, {}))
        merged.append(base)
    return merged


def _is_number_list(v: Any) -> bool:
    # dense_vector 검증용: 비어있지 않은 숫자 리스트인지 확인
    return isinstance(v, list) and v and all(isinstance(x, (int, float)) for x in v)


def _detect_vector_fields(sample_doc: Dict[str, Any], user_fields: List[str] | None) -> List[str]:
    # 사용자가 --vector-fields를 명시하면 그 값을 우선 사용한다.
    if user_fields:
        return user_fields

    # 미지정 시 *_vector 패턴을 자동 감지한다.
    detected = [k for k, v in sample_doc.items() if k.endswith("_vector") and _is_number_list(v)]
    if detected:
        return detected

    # 기존 코드 호환을 위해 embedding 단일 필드도 지원한다.
    if "embedding" in sample_doc and _is_number_list(sample_doc["embedding"]):
        return ["embedding"]

    raise ValueError(
        "Vector field not found. Use --vector-fields image_vector,text_vector or include '*_vector' field."
    )


def _build_mappings(
    sample_doc: Dict[str, Any],
    id_field: str,
    vector_fields: Sequence[str],
) -> Dict[str, Any]:
    # 문서 ID로 사용할 필드는 keyword로 고정한다.
    properties: Dict[str, Any] = {
        id_field: {"type": "keyword"},
    }

    for vf in vector_fields:
        # 샘플 문서에서 벡터 차원을 읽어 dense_vector 매핑을 생성한다.
        vec = sample_doc.get(vf)
        if not _is_number_list(vec):
            raise ValueError(f"sample doc missing valid vector field: {vf}")
        properties[vf] = {
            "type": "dense_vector",
            "dims": len(vec),
            "index": True,
            "similarity": "cosine",
        }

    # 자주 쓰는 메타데이터는 keyword로 매핑해 필터링/집계를 쉽게 한다.
    for k in [
        "brand",
        "gender",
        "category",
        "product_code",
        "image_filename",
        "image_path",
        "origin_hdfs_path",
    ]:
        if k in sample_doc:
            properties[k] = {"type": "keyword"}

    # 날짜 필드는 range query/정렬을 위해 date 타입으로 둔다.
    if "create_dt" in sample_doc:
        properties["create_dt"] = {"type": "date"}

    return {"properties": properties}


def _ensure_index(
    es: Elasticsearch,
    index_name: str,
    sample_doc: Dict[str, Any],
    id_field: str,
    vector_fields: Sequence[str],
) -> None:
    # 인덱스가 이미 있으면 재생성하지 않는다.
    if es.indices.exists(index=index_name):
        return

    # 없을 때만 샘플 문서 기반 매핑으로 생성한다.
    # 단일 노드인 로컬 환경 특성상 replica 0 으로 설정하여 Green 상태 유지
    mappings = _build_mappings(sample_doc, id_field=id_field, vector_fields=vector_fields)
    es.indices.create(
        index=index_name,
        mappings=mappings,
        settings={"number_of_shards": 1, "number_of_replicas": 0}
    )


def _build_actions(
    docs: Iterable[Dict[str, Any]],
    index_name: str,
    id_field: str,
    vector_fields: Sequence[str],
) -> Iterator[Dict[str, Any]]:
    # 벡터 필드별로 첫 문서의 차원을 기준값으로 저장해,
    # 이후 문서에서 차원 불일치를 즉시 검출한다.
    expected_dims: Dict[str, int] = {}

    for doc in docs:
        if id_field not in doc:
            raise ValueError(f"doc missing id field '{id_field}': {doc}")

        for vf in vector_fields:
            if vf not in doc:
                continue
            
            vec = doc.get(vf)
            if not _is_number_list(vec):
                raise ValueError(f"doc id={doc.get(id_field)} missing/invalid vector field '{vf}'")

            dim = len(vec)
            if vf not in expected_dims:
                expected_dims[vf] = dim
            elif expected_dims[vf] != dim:
                raise ValueError(
                    f"dimension mismatch on '{vf}' for id={doc.get(id_field)}: "
                    f"expected {expected_dims[vf]}, got {dim}"
                )

        yield {
            # 부분 업데이트(UPSERT) 방식으로 기존 문서의 다른 필드(예: 유저 태그, 추가 메타데이터)를 보호한다.
            "_op_type": "update",
            "_index": index_name,
            "_id": str(doc[id_field]),
            "doc": doc,
            "doc_as_upsert": True,
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload embedding JSON files into Elasticsearch (no Airflow required)."
    )
    parser.add_argument(
        "--input",
        default=None,
        help="JSON file, directory, or glob pattern (e.g. data/embeds/*.json)",
    )
    parser.add_argument(
        "--image-input",
        default=None,
        help="Image embedding JSON file/dir/glob (to merge by id with --text-input)",
    )
    parser.add_argument(
        "--text-input",
        default=None,
        help="Text embedding JSON file/dir/glob (to merge by id with --image-input)",
    )
    parser.add_argument(
        "--merge-mode",
        default="inner",
        choices=["inner", "left", "right", "outer"],
        help="How to merge image/text docs by id when using --image-input and --text-input",
    )
    parser.add_argument("--index", default="products", help="Target Elasticsearch index (default: products)")
    parser.add_argument("--es", default="http://localhost:9200", help="Elasticsearch URL")
    parser.add_argument("--id-field", default="product_id", help="Document id field")
    parser.add_argument(
        "--derive-id-from-filename",
        action="store_true",
        help="If id field is missing, derive product_code and product_id from filename (brand_code).",
    )
    parser.add_argument(
        "--filename-field",
        default="filename",
        help="Filename field name used when deriving product code/id (default: filename).",
    )
    parser.add_argument(
        "--skip-error-docs",
        action="store_true",
        help="Skip JSON docs that contain an 'error' field (recommended for text parse-fail outputs).",
    )
    parser.add_argument(
        "--vector-fields",
        default=None,
        help="Comma-separated vector field names. If omitted, auto-detect '*_vector' or 'embedding'.",
    )
    parser.add_argument("--chunk-size", type=int, default=200)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Refresh index after bulk (useful for immediate search tests)",
    )
    args = parser.parse_args()

    merged_mode = bool(args.image_input or args.text_input)
    if merged_mode and not (args.image_input and args.text_input):
        raise ValueError("When using split inputs, both --image-input and --text-input are required.")

    merged_docs: List[Dict[str, Any]] | None = None
    files: List[Path] = []
    if merged_mode:
        image_files = _resolve_json_files(args.image_input)
        text_files = _resolve_json_files(args.text_input)
        if not image_files:
            raise FileNotFoundError(f"No json files found from --image-input {args.image_input}")
        if not text_files:
            raise FileNotFoundError(f"No json files found from --text-input {args.text_input}")

        image_docs = _load_docs_by_id(
            image_files,
            id_field=args.id_field,
            label="image",
            derive_id_from_filename=args.derive_id_from_filename,
            filename_field=args.filename_field,
            skip_error_docs=args.skip_error_docs,
        )
        text_docs = _load_docs_by_id(
            text_files,
            id_field=args.id_field,
            label="text",
            derive_id_from_filename=args.derive_id_from_filename,
            filename_field=args.filename_field,
            skip_error_docs=args.skip_error_docs,
        )
        merged_docs = _merge_docs_by_id(image_docs, text_docs, merge_mode=args.merge_mode)
        if not merged_docs:
            raise RuntimeError("No merged JSON documents found.")
        sample_doc = _normalize_doc(
            merged_docs[0],
            id_field=args.id_field,
            derive_id_from_filename=args.derive_id_from_filename,
            filename_field=args.filename_field,
        )
        merged_docs = [
            _normalize_doc(
                d,
                id_field=args.id_field,
                derive_id_from_filename=args.derive_id_from_filename,
                filename_field=args.filename_field,
            )
            for d in merged_docs
        ]
    else:
        if not args.input:
            raise ValueError("Use --input OR use both --image-input and --text-input.")
        files = _resolve_json_files(args.input)
        if not files:
            raise FileNotFoundError(f"No json files found from --input {args.input}")

        # 첫 문서로 벡터 필드/차원을 판별해 인덱스 매핑 생성에 사용한다.
        docs_for_sample = _iter_all_docs(files)
        try:
            sample_doc = None
            for d in docs_for_sample:
                if args.skip_error_docs and _is_error_doc(d):
                    continue
                sample_doc = _normalize_doc(
                    d,
                    id_field=args.id_field,
                    derive_id_from_filename=args.derive_id_from_filename,
                    filename_field=args.filename_field,
                )
                break
            if sample_doc is None:
                raise RuntimeError("No JSON documents found after filtering.")
        except StopIteration as exc:
            raise RuntimeError("No JSON documents found.") from exc

    user_vector_fields = None
    if args.vector_fields:
        user_vector_fields = [x.strip() for x in args.vector_fields.split(",") if x.strip()]
    vector_fields = _detect_vector_fields(sample_doc, user_vector_fields)

    es = Elasticsearch(args.es)
    _ensure_index(
        es,
        index_name=args.index,
        sample_doc=sample_doc,
        id_field=args.id_field,
        vector_fields=vector_fields,
    )

    # 샘플 추출로 한 번 소비했으므로, 전체 문서 iterator를 다시 만든다.
    if merged_docs is not None:
        all_docs = iter(merged_docs)
    else:
        all_docs = (
            _normalize_doc(
                d,
                id_field=args.id_field,
                derive_id_from_filename=args.derive_id_from_filename,
                filename_field=args.filename_field,
            )
            for d in _iter_all_docs(files)
            if not (args.skip_error_docs and _is_error_doc(d))
        )
    actions = _build_actions(
        all_docs,
        index_name=args.index,
        id_field=args.id_field,
        vector_fields=vector_fields,
    )

    # streaming_bulk는 대용량에서도 메모리 사용량을 안정적으로 유지한다.
    success = 0
    for ok, item in helpers.streaming_bulk(es, actions, chunk_size=args.chunk_size, raise_on_error=False):
        if not ok:
            op_type = list(item.keys())[0] if item else "unknown"
            error_info = item.get(op_type, {}).get("error")
            if not error_info:
                error_info = item
            import json
            Path("/tmp/es_bulk_error.json").write_text(json.dumps(error_info, ensure_ascii=False, indent=2))
            raise RuntimeError(f"bulk error! Check /tmp/es_bulk_error.json for details.")
        success += 1

    # 테스트 직후 검색 확인이 필요하면 refresh를 수행한다.
    if args.refresh:
        es.indices.refresh(index=args.index)

    input_desc = (
        f"merged(image={args.image_input}, text={args.text_input}, mode={args.merge_mode})"
        if merged_docs is not None
        else args.input
    )
    print(
        f"Indexed {success} docs into '{args.index}' ({args.es}) "
        f"from {input_desc} with vectors={','.join(vector_fields)}"
    )


if __name__ == "__main__":
    main()
