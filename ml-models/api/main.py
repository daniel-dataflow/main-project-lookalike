from fastapi import FastAPI, UploadFile, File, Form
from typing import Optional
import uvicorn
import logging
import io
from elasticsearch import Elasticsearch

# ==========================================
# [ML팀 전용] 로그 및 서버 기본 설정
# ==========================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Lookalike ML Inference API", version="1.0.0")

# Elasticsearch 연결 설정 (docker-compose 환경 기준)
es_client = Elasticsearch("http://elasticsearch:9200")

# ==========================================
# [ML팀 전용] 1. AI 모델 전역 로드 (서버 켜질 때 1번만 실행)
# ==========================================
logger.info("AI 모델들을 메모리에 적재 중입니다...")

# TODO: 실제 깃허브 레포에서 작업하신 모델들을 import 하세요.
# from yolo_module import YOLOCategoryDetector
# from clip_module import FashionCLIPEncoder
# from nlp_module import TagExtractor

# yolo_detector = YOLOCategoryDetector(weight_path="weights/yolov11.pt")
# clip_encoder = FashionCLIPEncoder(model_name="patrickjohncyh/fashion-clip")
# tag_extractor = TagExtractor()

logger.info("AI 모델 적재 완료!")

@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "ML Engine is running"}

# ==========================================
# [ML팀 전용] 2. 핵심 API: 이미지/텍스트 분석 엔드포인트
# ==========================================
@app.post("/predict_vector")
async def process_search_query(
    image: Optional[UploadFile] = File(None, description="사용자가 업로드한 옷 사진"),
    text: Optional[str] = Form(None, description="사용자가 입력한 검색어 (예: '검은색 패딩')"),
):
    """
    [아키텍처 다이어그램 기반 동작 순서]
    사용자 입력 ➡️ Stage 1(YOLO 카테고리) ➡️ Stage 2(CLIP 임베딩) ➡️ 태그 추출 ➡️ 백엔드 반환
    """
    try:
        # 1. 쿼리 파싱 및 로직 처리 (Image / Text)
        image_vector = None
        text_vector = None
        detected_gender = None
        detected_category = None
        extracted_tags = []

        # ----------------------------------------------------
        # 케이스 A: 이미지가 들어온 경우
        # ----------------------------------------------------
        if image:
            image_bytes = await image.read()
            # img = Image.open(io.BytesIO(image_bytes))
            
            # [Stage 1] YOLO 분류 (임시 더미)
            detected_gender = "men" 
            detected_category = "outer" 
            
            # [Stage 2] Fashion-CLIP 이미지 임베딩 (임시 더미 512차원)
            image_vector = [-0.1437, -0.1772, 0.4444] * 170 + [-0.1437, -0.1772]
            logger.info(f"[ML] 이미지 처리 완료: 성별={detected_gender}, 카테고리={detected_category}")

        # ----------------------------------------------------
        # 케이스 B: 텍스트 검색어가 들어온 경우
        # ----------------------------------------------------
        if text:
            logger.info(f"[ML] 텍스트 처리: '{text}'")
            # [태그 추출] (임시 더미)
            extracted_tags = ["검은색", "가죽", "잠바"]
            
            # [텍스트 임베딩] (임시 더미 384차원)
            text_vector = [0.0123, -0.0456, 0.0789] * 128

        # ----------------------------------------------------
        # 2. Elasticsearch 검색 실행 (입력 케이스 3가지 분기)
        # ----------------------------------------------------
        es_query = None
        results = {} # {product_id: score}

        if image_vector and text_vector:
            # [케이스 3] 이미지 + 텍스트 복합 검색 (하이브리드 검색)
            es_query = {
                "knn": [
                    {
                        "field": "image_vector", 
                        "query_vector": image_vector, 
                        "k": 6, 
                        "num_candidates": 60, 
                        "boost": 0.7
                    },
                    {
                        "field": "text_vector", 
                        "query_vector": text_vector, 
                        "k": 6, 
                        "num_candidates": 60, 
                        "boost": 0.3
                    }
                ]
            }
        elif image_vector:
            # [케이스 1] 이미지만 입력된 경우
            es_query = {
                "knn": {
                    "field": "image_vector",
                    "query_vector": image_vector,
                    "k": 6,
                    "num_candidates": 60,
                }
            }
        elif text_vector:
            # [케이스 2] 텍스트만 입력된 경우
            es_query = {
                "knn": {
                    "field": "text_vector",
                    "query_vector": text_vector,
                    "k": 6,
                    "num_candidates": 60,
                }
            }

        if es_query:
            # 설정된 카테고리가 있다면 kNN 필터로 적용
            if detected_category:
                category_str = f"{detected_gender}_{detected_category}"
                if isinstance(es_query["knn"], list):
                    for q in es_query["knn"]:
                        q["filter"] = {"term": {"category": category_str}}
                else:
                    es_query["knn"]["filter"] = {"term": {"category": category_str}}
                
            response = es_client.search(
                index="products", 
                body=es_query, 
                size=6
            )
            
            hits = response["hits"]["hits"]
            for hit in hits:
                src = hit["_source"]
                key = src.get("product_code") or str(src.get("product_id"))
                results[key] = hit.get("_score", 0.0)
                
        # ----------------------------------------------------
        # 3. 백엔드로 결과 리턴
        # ----------------------------------------------------
        # 백엔드는 이 JSON 데이터를 받아 바로 DB에 ID값을 매칭시킵니다.
        return {
            "ml_product_scores": results,  # 검색된 상위 6개 아이템 {"id": score, ...}
            "gender": detected_gender,
            "applied_category": detected_category,
            "tags": extracted_tags
        }

    except Exception as e:
        logger.error(f"[ML] 처리 중 에러 발생: {str(e)}")
        return {"error": "ML 모델 추론 중 서버 내부 에러가 발생했습니다."}, 500

if __name__ == "__main__":
    # 백엔드 서버에서 이 주소(포트 8914)로 HTTP 통신을 걸어옵니다.
    uvicorn.run("main:app", host="0.0.0.0", port=8914, reload=True)
