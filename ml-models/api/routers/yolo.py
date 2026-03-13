"""YOLO 전용 HTTP 엔드포인트 모음."""

import logging
from io import BytesIO

from fastapi import APIRouter, File, HTTPException, UploadFile
from PIL import Image

from services.yolo_service import yolo_detector

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/yolo", tags=["YOLO Detection"])

@router.post("/detect")
async def detect_apparel(image: UploadFile = File(...)):
    """
    업로드된 이미지에서 YOLO 바운딩 박스를 추출해 반환한다.
    유사상품 검색 파이프라인과는 독립적으로 동작한다.
    """
    data = await image.read()
    if not data:
        raise HTTPException(status_code=400, detail="업로드된 이미지가 비어 있습니다.")
    try:
        pil_img = Image.open(BytesIO(data)).convert("RGB")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="유효한 이미지 파일이 아닙니다.") from exc

    try:
        boxes = yolo_detector.detect_boxes(pil_img)
        return {"success": True, "boxes": boxes}
    except Exception as exc:
        logger.error(f"YOLO 탐지 중 오류: {exc}")
        raise HTTPException(status_code=500, detail="YOLO 객체 탐지 중 오류가 발생했습니다.")
