from __future__ import annotations

"""검색 관련 HTTP 엔드포인트 모음"""

from io import BytesIO
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from PIL import Image

from services.bootstrap import SearchRuntime
from utils.parsers import parse_category

# 검색 엔드포인트를 묶는 라우터
router = APIRouter(tags=["Search"])


def _get_runtime(request: Request) -> SearchRuntime:
    """앱 시작 시 초기화한 공용 런타임을 가져온다."""
    runtime = getattr(request.app.state, "search_runtime", None)
    if runtime is None:
        raise HTTPException(status_code=500, detail="Search runtime is not initialized.")
    return runtime


@router.get("/health")
def health_check() -> dict:
    """메인 백엔드와 컨테이너 헬스체크에서 사용하는 상태 확인 엔드포인트."""
    return {"status": "ok", "message": "ML Engine is running"}


@router.post("/search")
async def search(
    request: Request,
    image: Optional[UploadFile] = File(default=None),
    text: Optional[str] = Form(default=None),
    category: Optional[str] = Form(default=None),
    gender: Optional[str] = Form(default=None),
) -> dict:
    """이미지/텍스트 입력으로 ML 검색을 수행하고 후보 결과를 반환한다."""
    runtime = _get_runtime(request)

    pil_img: Optional[Image.Image] = None
    if image is not None:
        data = await image.read()
        if not data:
            raise HTTPException(status_code=400, detail="업로드된 이미지가 비어 있습니다.")
        try:
            pil_img = Image.open(BytesIO(data)).convert("RGB")
        except Exception as exc:
            raise HTTPException(status_code=400, detail="유효한 이미지 파일이 아닙니다.") from exc

    category_value = parse_category(category)
    gender_value = parse_category(gender)
    text_value = text
    if text_value is not None:
        text_value = runtime.rewriter.rewrite_if_needed(
            text=text_value,
            has_image=pil_img is not None,
            category=category_value,
        )
    try:
        results = runtime.service.search(
            image=pil_img,
            text=text_value,
            category=category_value,
            gender=gender_value,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"검색 중 오류가 발생했습니다: {exc}") from exc

    return {"count": len(results), "results": results}


@router.post("/predict_vector")
async def predict_vector(
    request: Request,
    image: Optional[UploadFile] = File(default=None),
    text: Optional[str] = Form(default=None),
    category: Optional[str] = Form(default=None),
    gender: Optional[str] = Form(default=None),
) -> dict:
    """메인 백엔드가 사용하는 기존 점수 맵 포맷으로 응답한다."""
    payload = await search(
        request=request,
        image=image,
        text=text,
        category=category,
        gender=gender,
    )

    ml_product_scores = {
        str(item.get("id")): float(item.get("score", 0.0))
        for item in payload.get("results", [])
        if item.get("id") is not None
    }

    return {
        "ml_product_scores": ml_product_scores,
        "gender": None,
        "applied_category": None,
        "tags": [],
    }
