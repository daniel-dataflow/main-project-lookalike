import json
import gc
from datetime import datetime
from pathlib import Path

def process_text_embedding(json_paths: list[str], model_name: str) -> list[str]:
    from sentence_transformers import SentenceTransformer
    
    if not json_paths: return []

    print(f"⏳ 임베딩 모델 로딩 중: {model_name}...")
    embedding_model = SentenceTransformer(model_name)
    
    docs_to_embed = []
    updated_paths = []

    # 문서 읽기 및 준비
    for p in json_paths:
        path_obj = Path(p)
        doc = json.loads(path_obj.read_text(encoding="utf-8"))
        
        if "text_vector" in doc:
            updated_paths.append(p)
            continue
            
        analysis = doc.get("analysis", {})
        basic_info = doc.get("basic_info", {})
        search_text = analysis.get("search_text", "")
        occasion_list = analysis.get("occasion", [])
        occasions = ", ".join(occasion_list) if isinstance(occasion_list, list) else str(occasion_list)
        
        text_input = f"브랜드: {basic_info.get('brand')}, 성별: {basic_info.get('gender')}, 카테고리: {basic_info.get('category_code')}, 상황: {occasions}, 설명: {search_text}"
        
        doc["_raw_text_input"] = text_input
        docs_to_embed.append((path_obj, doc))

    if not docs_to_embed: return updated_paths

    print(f"📦 총 {len(docs_to_embed)}건의 텍스트 배치(Batch) 임베딩 시작...")
    
    texts = [item[1]["_raw_text_input"] for item in docs_to_embed]
    vectors = embedding_model.encode(texts).tolist()

    # 추출된 벡터를 문서에 덮어쓰고 다시 JSON 저장
    for (path_obj, doc), vector in zip(docs_to_embed, vectors):
        doc["text_vector"] = vector
        doc["analyzed_at"] = datetime.now().isoformat()
        del doc["_raw_text_input"]
        
        path_obj.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        updated_paths.append(str(path_obj))
        
    gc.collect()
    print(f"🎉 텍스트 임베딩 완료! (처리: {len(vectors)}건)")
    
    return updated_paths
