from fastapi import FastAPI, UploadFile, File, Form
from typing import Optional
import uvicorn
import logging
import io

# ==========================================
# [ML팀 전용] 로그 및 서버 기본 설정
# ==========================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Lookalike ML Inference API", version="1.0.0")

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
        # 응답으로 내려줄 기본 뼈대
        response_data = {
            "image_vector": None,       # 512차원 배열 (ES 검색용)
            "text_vector": None,        # 384차원 배열 (ES 검색용)
            "gender": None,             # "men" 또는 "women" (DB 필터링용)
            "applied_category": None,   # "top", "bottom", "outer" (DB 필터링용)
            "tags": []                  # ["검은색", "가죽", "겨울"] 형태 (자연어 태그 매칭용)
        }

        # ----------------------------------------------------
        # 케이스 A: 이미지가 들어온 경우 (이미지 유사도 검색)
        # ----------------------------------------------------
        if image:
            image_bytes = await image.read()
            # img = Image.open(io.BytesIO(image_bytes))
            
            # [Stage 1] YOLO 모델로 이미지에서 부위(BBox) 검출 및 카테고리 분류
            # detected_gender, detected_category = yolo_detector.predict(img) # 예: "men", "outer"
            detected_gender = "men" # (임시 더미)
            detected_category = "outer" # (임시 더미)
            
            response_data["gender"] = detected_gender
            response_data["applied_category"] = detected_category
            
            # [Stage 2] Fashion-CLIP으로 이미지 벡터화 (숫자 배열 뽑기)
            # img_vector = clip_encoder.encode_image(img) 
            
            # (임시 더미 512차원 배열 생성)
            dummy_image_vector = [-0.1437, -0.1772, 0.4444] * 170 + [-0.1437, -0.1772]
            response_data["image_vector"] = dummy_image_vector
            
            logger.info(f"[ML] 이미지 처리 완료: 성별={detected_gender}, 카테고리={detected_category}")

        # ----------------------------------------------------
        # 케이스 B: 텍스트 검색어가 들어온 경우 (자연어 태그 매칭)
        # ----------------------------------------------------
        if text:
            logger.info(f"[ML] 텍스트 입력 확인: '{text}'")
            
            # [자연어 처리] VLM / KoNLPy / KeyBERT 등으로 형태소 분석 및 태그 추출
            # extracted_tags = tag_extractor.extract(text)
            
            # (임시 더미 태그 추출) 사용자가 '검은색 가죽 잠바' 라고 쳤다고 가정
            extracted_tags = ["검은색", "가죽", "잠바"]
            response_data["tags"] = extracted_tags
            
            # [텍스트 임베딩] 텍스트 자체를 공간 벡터로 변환 (백엔드 ES 텍스트 검색용)
            # txt_vector = clip_encoder.encode_text(text)
            
            # (임시 더미 384차원 배열 생성)
            dummy_text_vector = [0.0123, -0.0456, 0.0789] * 128
            response_data["text_vector"] = dummy_text_vector
            
        # ----------------------------------------------------
        # 3. 백엔드로 결과 리턴
        # ----------------------------------------------------
        # 백엔드는 이 JSON 데이터를 고스란히 받아서 ES 벡터 검색과 DB 최저가 조인에 사용합니다.
        return response_data

    except Exception as e:
        logger.error(f"[ML] 처리 중 에러 발생: {str(e)}")
        return {"error": "ML 모델 추론 중 서버 내부 에러가 발생했습니다."}, 500

if __name__ == "__main__":
    # 백엔드 서버에서 이 주소(포트 8914)로 HTTP 통신을 걸어옵니다.
    uvicorn.run("main:app", host="0.0.0.0", port=8914, reload=True)
