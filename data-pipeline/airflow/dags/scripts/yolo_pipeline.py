from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any

import psycopg2
from airflow.decorators import task
import logging

# YOLO class name → 서비스 표준 cate
CATEGORY_MAP = {
    "top": "top",
    "상의": "top",
    "bottom": "bottom",
    "하의": "bottom",
    "outer": "outer",
    "아우터": "outer",
}


@lru_cache(maxsize=1)
def _load_model(model_path: str):
    from ultralytics import YOLO
    return YOLO(model_path)


def _to_category(cls_name: str) -> str | None:
    return CATEGORY_MAP.get(str(cls_name).strip().lower())


def _run_yolo_detect(
    records: list[dict[str, Any]],
    brand: str,
    model_path: str,
    crop_tmp_dir: str,
    imgsz: int,
    score_thresh: float,
    iou: float,
) -> list[dict[str, Any]]:

    from PIL import Image
    import gc

    if not records:
        return []

    crop_root = Path(crop_tmp_dir) / brand.lower()
    crop_root.mkdir(parents=True, exist_ok=True)

    model = _load_model(model_path)
    detections: list[dict[str, Any]] = []

    for record in records:
        input_path = record["local_path"]
        p = Path(input_path)
        if not p.exists():
            continue

        results = model.predict(
            source=str(p),
            imgsz=imgsz,
            conf=score_thresh,
            iou=iou,
            save=False,
            save_crop=False,
            stream=False,
            verbose=False,
        )

        result = results[0] if results else None
        if result is None or result.boxes is None or len(result.boxes) == 0:
            continue

        names = result.names
        image_bgr = result.orig_img
        h, w = image_bgr.shape[:2]

        for det_idx, box in enumerate(result.boxes):
            cls_id = int(box.cls.item())
            cls_name = names.get(cls_id, str(cls_id))
            conf_score = float(box.conf.item())

            category = _to_category(cls_name)
            if category is None:
                continue

            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]

            x1 = max(0, min(x1, w - 1))
            x2 = max(0, min(x2, w))
            y1 = max(0, min(y1, h - 1))
            y2 = max(0, min(y2, h))

            if x2 <= x1 or y2 <= y1:
                continue

            class_dir = crop_root / category
            class_dir.mkdir(parents=True, exist_ok=True)
            crop_path = class_dir / f"{p.stem}_{det_idx}.jpg"

            crop_img = image_bgr[y1:y2, x1:x2]
            crop_rgb = crop_img[:, :, ::-1]
            Image.fromarray(crop_rgb).save(str(crop_path), format="JPEG", quality=95)

            detections.append(
                {
                    "input_path": input_path,
                    "category": category,
                    "confidence": conf_score,
                    "crop_path": str(crop_path),
                }
            )

        del results, result, image_bgr
        gc.collect()

    return detections

@task
def yolo_reorganize_dedup_upsert(
    records: list[dict[str, Any]],
    brand: str,
    crop_tmp_dir: str,
    crop_final_dir: str,
    model_path: str,
    imgsz: int = 640,
    score_thresh: float = 0.25,
    iou: float = 0.7,
) -> list[dict[str, Any]]:

    # ─────────────────────────────
    #  내부 import (Airflow 안전)
    # ─────────────────────────────
    import os
    import re
    import gc
    import shutil
    from pathlib import Path
    from functools import lru_cache
    from PIL import Image
    from ultralytics import YOLO

    # 파일명 패턴
    NAME_RE = re.compile(
        r"^(?P<brand>[^_]+)_(?P<gender>Men|Women)_(?P<category>Top|Bottom|Outer)_(?P<product_code>[^_]+)(?:_.*)?$",
        re.IGNORECASE,
    )

    CATEGORY_MAP = {
        "top": "Top",
        "상의": "Top",
        "bottom": "Bottom",
        "하의": "Bottom",
        "outer": "Outer",
        "아우터": "Outer",
    }

    def _to_category(cls_name: str) -> str | None:
        return CATEGORY_MAP.get(str(cls_name).strip().lower())

    @lru_cache(maxsize=1)
    def _load_model(model_path: str):
        return YOLO(model_path)

    # ─────────────────────────────
    # 🔹 YOLO detect + crop
    # ─────────────────────────────
    def _run_yolo_detect():
        if not records:
            return []

        crop_root = Path(crop_tmp_dir) / brand.lower()
        crop_root.mkdir(parents=True, exist_ok=True)

        model = _load_model(model_path)
        detections = []

        for record in records:
            input_path = record["local_path"]
            p = Path(input_path)
            if not p.exists():
                continue

            results = model.predict(
                source=str(p),
                imgsz=imgsz,
                conf=score_thresh,
                iou=iou,
                save=False,
                verbose=False,
            )

            result = results[0] if results else None
            if result is None or result.boxes is None:
                continue

            names = result.names
            image_bgr = result.orig_img
            h, w = image_bgr.shape[:2]

            for idx, box in enumerate(result.boxes):
                cls_id = int(box.cls.item())
                cls_name = names.get(cls_id, str(cls_id))
                conf_score = float(box.conf.item())

                x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
                x1 = max(0, min(x1, w - 1))
                x2 = max(0, min(x2, w))
                y1 = max(0, min(y1, h - 1))
                y2 = max(0, min(y2, h))
                if x2 <= x1 or y2 <= y1:
                    continue

                class_dir = crop_root / str(cls_name)
                class_dir.mkdir(parents=True, exist_ok=True)
                crop_path = class_dir / f"{p.stem}_{idx}.jpg"

                crop_img = image_bgr[y1:y2, x1:x2]
                crop_rgb = crop_img[:, :, ::-1]
                Image.fromarray(crop_rgb).save(str(crop_path), "JPEG", quality=95)

                detections.append(
                    {
                        "input_path": input_path,
                        "class_name": cls_name,
                        "confidence": conf_score,
                        "crop_path": str(crop_path),
                    }
                )

            del results, result, image_bgr
            gc.collect()

        return detections

    # ─────────────────────────────
    #  YOLO 실행
    # ─────────────────────────────
    detections = _run_yolo_detect()

    # ─────────────────────────────
    #  원본 상품 정리
    # ─────────────────────────────
    all_products = {}

    for r in records:
        input_name = Path(r["local_path"]).name
        match = NAME_RE.match(Path(input_name).stem)

        if match:
            brand_name = match.group("brand")
            gender = match.group("gender")
            category_orig = match.group("category")
            product_code = match.group("product_code")
        else:
            brand_name = brand
            gender = "unknown"
            category_orig = "unknown"
            product_code = Path(input_name).stem.split("_")[-1]

        pid = f"{brand_name}_{product_code}"

        all_products[pid] = {
            "local_path": r["local_path"],
            "hdfs_path": r.get("hdfs_path"),
            "brand": brand_name,
            "gender": gender,
            "category": category_orig,
            "product_code": product_code,
        }

    # ─────────────────────────────
    # 카테고리 일치 + 최고 confidence만 유지
    # ─────────────────────────────
    best_by_pid = {}

    for det in detections:
        input_name = Path(det["input_path"]).name
        match = NAME_RE.match(Path(input_name).stem)
        if not match:
            continue

        brand_name = match.group("brand")
        product_code = match.group("product_code")
        pid = f"{brand_name}_{product_code}"

        yolo_category = _to_category(det["class_name"])
        if yolo_category is None:
            continue

        orig = all_products.get(pid)
        if not orig:
            continue

        original_category = orig["category"]

        # 카테고리 일치
        if yolo_category.lower() != original_category.lower():
            continue

        cand = {
            "category": yolo_category,
            "crop_path": det["crop_path"],
            "confidence": det["confidence"],
        }

        prev = best_by_pid.get(pid)
        if prev is None or cand["confidence"] > prev["confidence"]:
            best_by_pid[pid] = cand

    # ─────────────────────────────
    #  최종 이미지 구성
    # ─────────────────────────────
    out_rows = []
    fallback_filenames = []
    final_root = Path(crop_final_dir)

    for pid, orig in all_products.items():
        det = best_by_pid.get(pid)

        safe_brand = str(orig["brand"] or "unknown")
        safe_gender = str(orig["gender"] or "unknown")
        safe_category = str(orig["category"] or "unknown")
        safe_code = str(orig["product_code"] or "unknown")

        if det:
            image_source = det["crop_path"]
            is_cropped = True
        else:
            image_source = orig["local_path"]
            is_cropped = False
            fallback_filenames.append(Path(image_source).name)

        dst_dir = final_root / safe_brand / safe_gender.lower() / safe_category.lower()
        dst_dir.mkdir(parents=True, exist_ok=True)

        image_filename = f"{safe_brand}_{safe_gender}_{safe_category}_{safe_code}.jpg"
        final_path = dst_dir / image_filename

        if final_path.exists():
            final_path.unlink()

        if is_cropped:
            shutil.move(image_source, final_path)
        else:
            shutil.copy(image_source, final_path)

        out_rows.append(
            {
                "product_id": pid,
                "brand": safe_brand,
                "gender": safe_gender,
                "category": safe_category,
                "product_code": safe_code,
                "origin_hdfs_path": orig["hdfs_path"],
                "crop_local_path": str(final_path),
                "image_filename": image_filename,
            }
        )
    
    logger = logging.getLogger(__name__)

    total_input = len(records)
    total_detections = len(detections)
    matched_count = len(best_by_pid)
    fallback_count = total_input - matched_count
    final_count = len(out_rows)

    logger.info("========== YOLO PROCESS SUMMARY ==========")
    logger.info(f"총 입력 이미지 수        : {total_input}")
    logger.info(f"YOLO raw detection 수    : {total_detections}")
    logger.info(f"카테고리 일치 성공 수   : {matched_count}")
    logger.info(f"Fallback (원본 사용) 수  : {fallback_count}")
    logger.info(f"최종 저장 이미지 수      : {final_count}")
    logger.info("==========================================")

    if fallback_filenames:
        logger.info(f"--- [Fallback 대상 원본 파일 목록 (총 {len(fallback_filenames)}건)] ---")
        for fname in fallback_filenames:
            logger.info(f" - {fname}")
            
    logger.info("==========================================")

    return out_rows
