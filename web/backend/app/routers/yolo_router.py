import logging
import os
import httpx
from fastapi import APIRouter, File, HTTPException, UploadFile, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/yolo", tags=["YOLO Detection Proxy"])

@router.post("/detect")
async def detect_apparel(
    request: Request,
    image: UploadFile = File(..., description="의류를 탐지할 원본 이미지")
):
    """
    ML 엔진의 YOLO 객체 탐지 API로 이미지를 단순 프록시 처리합니다.
    """
    YOLO_ENGINE_URL = os.getenv("YOLO_ENGINE_URL", "http://ml-engine:8914/yolo/detect")
    try:
        data = await image.read()
        files = {"image": (image.filename or "detect.jpg", data, image.content_type or "image/jpeg")}
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(YOLO_ENGINE_URL, files=files)
            resp.raise_for_status()
            return resp.json()
            
    except Exception as e:
        logger.error(f"YOLO 탐지 프록시 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="객체 탐지 서버 통신 오류가 발생했습니다.")
