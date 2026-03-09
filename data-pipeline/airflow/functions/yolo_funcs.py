import os
import re
import gc
import shutil
import logging
from pathlib import Path
from functools import lru_cache
from typing import Any
from PIL import Image
from ultralytics import YOLO

NAME_RE = re.compile(
    r"^(?P<brand>[^_]+)_(?P<gender>Men|Women)_(?P<category>Top|Bottom|Outer)_(?P<product_code>[^_]+)(?:_.*)?$",
    re.IGNORECASE,
)

CATEGORY_MAP = {
    "top": "Top", "상의": "Top",
    "bottom": "Bottom", "하의": "Bottom",
    "outer": "Outer", "아우터": "Outer",
}

def _to_category(cls_name: str) -> str | None:
    return CATEGORY_MAP.get(str(cls_name).strip().lower())

@lru_cache(maxsize=1)
def _load_model(model_path: str):
    return YOLO(model_path)

def process_yolo_images(
    records: list[dict[str, Any]],
    brand: str,
    crop_tmp_dir: str,
    crop_final_dir: str,
    model_path: str,
    imgsz: int = 640,
    score_thresh: float = 0.25,
    iou: float = 0.7,
) -> list[dict[str, Any]]:
    
    if not records:
        return []

    # ─────────────────────────────
    # 1. YOLO detect + crop 실행
    # ─────────────────────────────
    crop_root = Path(crop_tmp_dir) / brand.lower()
    crop_root.mkdir(parents=True, exist_ok=True)
    
    model = _load_model(model_path)
    detections = []

    for record in records:
        input_path = record["local_path"]
        p = Path(input_path)
        if not p.exists(): continue

        results = model.predict(
            source=str(p), imgsz=imgsz, conf=score_thresh,
            iou=iou, save=False, verbose=False,
        )

        result = results[0] if results else None
        if result is None or result.boxes is None: continue

        names = result.names
        image_bgr = result.orig_img
        h, w = image_bgr.shape[:2]

        for idx, box in enumerate(result.boxes):
            cls_id = int(box.cls.item())
            cls_name = names.get(cls_id, str(cls_id))
            conf_score = float(box.conf.item())

            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
            x1, x2 = max(0, min(x1, w - 1)), max(0, min(x2, w))
            y1, y2 = max(0, min(y1, h - 1)), max(0, min(y2, h))
            
            if x2 <= x1 or y2 <= y1: continue

            class_dir = crop_root / str(cls_name)
            class_dir.mkdir(parents=True, exist_ok=True)
            crop_path = class_dir / f"{p.stem}_{idx}.jpg"

            crop_img = image_bgr[y1:y2, x1:x2]
            crop_rgb = crop_img[:, :, ::-1]
            Image.fromarray(crop_rgb).save(str(crop_path), "JPEG", quality=95)

            detections.append({
                "input_path": input_path, "class_name": cls_name,
                "confidence": conf_score, "crop_path": str(crop_path),
            })

        del results, result, image_bgr
        gc.collect()

    # ─────────────────────────────
    # 2. 원본 상품 정리
    # ─────────────────────────────
    all_products = {}
    for r in records:
        input_name = Path(r["local_path"]).name
        match = NAME_RE.match(Path(input_name).stem)

        if match:
            brand_name, gender = match.group("brand"), match.group("gender")
            category_orig, product_code = match.group("category"), match.group("product_code")
        else:
            brand_name, gender = brand, "unknown"
            category_orig, product_code = "unknown", Path(input_name).stem.split("_")[-1]

        pid = f"{brand_name}_{product_code}"
        all_products[pid] = {
            "local_path": r["local_path"], "hdfs_path": r.get("hdfs_path"),
            "brand": brand_name, "gender": gender,
            "category": category_orig, "product_code": product_code,
        }

    # ─────────────────────────────
    # 3. 카테고리 일치 + 최고 confidence 추출
    # ─────────────────────────────
    best_by_pid = {}
    for det in detections:
        input_name = Path(det["input_path"]).name
        match = NAME_RE.match(Path(input_name).stem)
        if not match: continue

        pid = f"{match.group('brand')}_{match.group('product_code')}"
        yolo_category = _to_category(det["class_name"])
        if yolo_category is None: continue

        orig = all_products.get(pid)
        if not orig or yolo_category.lower() != orig["category"].lower(): continue

        cand = {"category": yolo_category, "crop_path": det["crop_path"], "confidence": det["confidence"]}
        prev = best_by_pid.get(pid)
        if prev is None or cand["confidence"] > prev["confidence"]:
            best_by_pid[pid] = cand

    # ─────────────────────────────
    # 4. 최종 이미지 구성 (이동/복사)
    # ─────────────────────────────
    out_rows, fallback_filenames = [], []
    final_root = Path(crop_final_dir)

    for pid, orig in all_products.items():
        det = best_by_pid.get(pid)
        safe_brand, safe_gender = str(orig["brand"] or "unknown"), str(orig["gender"] or "unknown")
        safe_category, safe_code = str(orig["category"] or "unknown"), str(orig["product_code"] or "unknown")

        image_source = det["crop_path"] if det else orig["local_path"]
        is_cropped = bool(det)
        if not is_cropped: fallback_filenames.append(Path(image_source).name)

        dst_dir = final_root / safe_brand / safe_gender.lower() / safe_category.lower()
        dst_dir.mkdir(parents=True, exist_ok=True)
        
        image_filename = f"{safe_brand}_{safe_gender}_{safe_category}_{safe_code}.jpg"
        final_path = dst_dir / image_filename
        if final_path.exists(): final_path.unlink()

        shutil.move(image_source, final_path) if is_cropped else shutil.copy(image_source, final_path)

        out_rows.append({
            "product_id": pid, "brand": safe_brand, "gender": safe_gender,
            "category": safe_category, "product_code": safe_code,
            "origin_hdfs_path": orig["hdfs_path"], "crop_local_path": str(final_path),
            "image_filename": image_filename,
        })

    # ─────────────────────────────
    # 5. 로깅
    # ─────────────────────────────
    logger = logging.getLogger(__name__)
    logger.info("========== YOLO PROCESS SUMMARY ==========")
    logger.info(f"총 입력 이미지 수    : {len(records)}")
    logger.info(f"YOLO raw detection 수    : {len(detections)}")
    logger.info(f"카테고리 일치 성공 수   : {len(best_by_pid)}")
    logger.info(f"Fallback (원본 사용) 수  : {len(records) - len(best_by_pid)}")
    logger.info(f"최종 저장 이미지 수      : {len(out_rows)}")
    logger.info("==========================================")
    
    return out_rows
