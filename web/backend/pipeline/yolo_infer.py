# 타입힌트 모듈
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

# YOLO
# import cv2
# from ultralytics import YOLO

# 함수 결과를 메모리에 캐시하는 데코레이터(속도/메모리 안정화위해)
# LRU: 가장 오래 안 쓴 캐시 제거, 모델 하나만 캐싱
@lru_cache(maxsize=1)
def _load_model(model_path: str):
    from ultralytics import YOLO
    return YOLO(model_path)


def run_yolo_detect_classify(
        # 입력 데이터
        records: list[dict[str, Any]],
        brand: str | None = None,
        # yolo 가중치 파일 경로 or .pt 파일
        # model_path: str = "/opt/models/yolo11/best.pt",  # 컨테이너 기준 경로로 맞추는 걸 권장
        model_path: str | None = None,
        # 탐지 후 크롭 이미지 저장 경로
        # crop_root_dir: str = "/opt/pipeline/data/crops_tmp",
        crop_root_dir: str | None = None,
        # 입력 이미지 크기 리사이즈(클수록 정확도 ↑ / 속도 ↓)
        imgsz: int = 640,
        # 탐지 신뢰도
        conf: float = 0.25,
        # 겹치는 박스 제거 기준
        iou: float = 0.7,
) -> list[dict[str, Any]]:
    """
    입력 records 예시:
    [
      {"product_id": 101, "hdfs_path": "...", "local_path": "/.../img.jpg"},
      ...
    ]

    반환 detections 예시: 수정 필요할지?
    [
      {
        "product_id": 101,
        "input_path": "/.../img.jpg",
        "class_id": 2,
        "class_name": "shirt",
        "confidence": 0.91,
        "bbox_xyxy": [x1, y1, x2, y2],
        "crop_path": "/.../crops/shirt/101_0.jpg"
      },
      ...
    ]
    """
    import cv2

    if not records:
        return []

    model_path = model_path or os.getenv("MODEL_PATH", "/opt/airflow/model/best.pt")
    crop_root_dir = crop_root_dir or os.getenv("CROP_DIR", "/opt/airflow/data/crops")

    brand_name = (brand or "all").lower()  # 브랜드명 표준화
    crop_root = Path(crop_root_dir) / brand_name  # 브랜드별 폴더
    crop_root.mkdir(parents=True, exist_ok=True)

    # yolo에 넣을 입력 이미지 경로 목록
    source_paths: list[str] = []
    for r in records:
        p = Path(r["local_path"])
        if not p.exists():
            raise FileNotFoundError(f"Input image not found: {p}")
        source_paths.append(str(p))


    model = _load_model(model_path)


    # 설정값 적용
    results = model.predict(
        source=source_paths,
        imgsz=imgsz,
        conf=conf,
        iou=iou,
        save=False,  # 파이프라인에선 원본 결과 이미지 저장은 보통 끔
        save_crop=False,  # crop 경로를 우리가 직접 관리
        # save_conf=True,
        stream=False,
        verbose=False,
        # exist_ok=True,
        # retina_masks=True,
    )

    detections: list[dict[str, Any]] = []

    # predict 결과 순서는 source_paths와 동일하다고 가정
    for rec, result in zip(records, results):
        product_id = rec["product_id"]
        input_path = rec["local_path"]
        rec_brand = rec.get("brand", brand_name)  # 레코드별 brand 유지

        if result.boxes is None or len(result.boxes) == 0:
            continue

        # Ultralytics names 매핑 (class_id -> class_name)
        names = result.names
        image_bgr = result.orig_img  # 원본 이미지
        h, w = image_bgr.shape[:2]

        for det_idx, box in enumerate(result.boxes):
            cls_id = int(box.cls.item())
            cls_name = names.get(cls_id, str(cls_id))
            conf_score = float(box.conf.item())
            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]

            # 좌표 보정
            x1 = max(0, min(x1, w - 1))
            x2 = max(0, min(x2, w))
            y1 = max(0, min(y1, h - 1))
            y2 = max(0, min(y2, h))
            if x2 <= x1 or y2 <= y1:
                continue

            class_dir = crop_root / cls_name
            class_dir.mkdir(parents=True, exist_ok=True)
            # 크롭 이미지 저장 경로 설정 - postgres로
            crop_path = class_dir / f"{product_id}_{det_idx}.jpg"

            crop_img = image_bgr[y1:y2, x1:x2]
            cv2.imwrite(str(crop_path), crop_img)

            detections.append(
                {
                    "product_id": product_id,
                    "brand": rec_brand,
                    "input_path": input_path,
                    "class_id": cls_id,
                    "class_name": cls_name,
                    "confidence": conf_score,
                    "bbox_xyxy": [x1, y1, x2, y2],
                    "crop_path": str(crop_path),  # 임시 로컬 경로
                }
            )

    return detections
