from __future__ import annotations

import os
import re
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any

import psycopg2
from airflow.decorators import task


# 입력 이미지 파일명 패턴:
# brand_gender_category_product_code(_extra...).jpg
NAME_RE = re.compile(
    r"^(?P<brand>[^_]+)_(?P<gender>Men|Women)_(?P<category>Top|Bottom|Outer)_(?P<product_code>[^_]+)(?:_.*)?$"
)

# YOLO class name
CATEGORY_MAP = {
    "top": "Top",
    "상의": "Top",
    "bottom": "Bottom",
    "하의": "Bottom",
    "outer": "Outer",
    "아우터": "Outer",
}

# YOLO 모델 로딩(1회만 - 동일 프로세스에서는 재로딩 안함)
@lru_cache(maxsize=1)
def _load_model(model_path: str):
    from ultralytics import YOLO

    return YOLO(model_path)


# YOLO가 예측한 class name을 서비스에서 사용하는 표준 cate로 변환
def _to_category(cls_name: str) -> str | None:
    return CATEGORY_MAP.get(str(cls_name).strip().lower())


# Postgres 에 업서트
def _upsert_postgres(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return

    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "postgresql"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB", "datadb"),
        user=os.getenv("POSTGRES_USER", "datauser"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
    )

    sql = """
    INSERT INTO "Products"
    (product_id, brand_name, gender, category, product_code, img_hdfs_path, crop_local_path, image_filename)
    VALUES (%(product_id)s, %(brand)s, %(gender)s, %(category)s, %(product_code)s, %(origin_hdfs_path)s, %(crop_local_path)s, %(image_filename)s)
    ON CONFLICT (product_id) DO UPDATE SET
      brand_name=EXCLUDED.brand_name,
      gender=EXCLUDED.gender,
      category=EXCLUDED.category,
      product_code=EXCLUDED.product_code,
      img_hdfs_path=EXCLUDED.img_hdfs_path,
      crop_local_path=EXCLUDED.crop_local_path,
      image_filename=EXCLUDED.image_filename;
    """

    with conn:
        with conn.cursor() as cur:
            for row in rows:
                cur.execute(sql, row)
    conn.close()


# YOLO 객체탐지 + 크롭
def _run_yolo_detect(
    records: list[dict[str, Any]],
    brand: str,
    model_path: str,
    crop_tmp_dir: str,
    imgsz: int,
    score_thresh: float,
    iou: float,
) -> list[dict[str, Any]]:
    """
    1) 이미지 1장씩 YOLO inference
    2) detection 결과를 crop 이미지로 저장
    3) detection 메타데이터 반환
    """
    from PIL import Image
    import gc

    if not records:
        return []

    # 임시 crop 저장 루트 : crop_tmp_dir/brand/
    crop_root = Path(crop_tmp_dir) / brand.lower()
    crop_root.mkdir(parents=True, exist_ok=True)

    model = _load_model(model_path)
    detections: list[dict[str, Any]] = []

    for record in records:
        input_path = record["local_path"]
        p = Path(input_path)
        if not p.exists():
            continue

        # batch가 아닌 1장씩 추론 (메모리 안정성 때문에)
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
        temp_id = p.stem  # crop 파일명에 사용

        for det_idx, box in enumerate(result.boxes):
            cls_id = int(box.cls.item())
            cls_name = names.get(cls_id, str(cls_id))
            conf_score = float(box.conf.item())
            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]

            # bbox boundary clamp
            x1 = max(0, min(x1, w - 1))
            x2 = max(0, min(x2, w))
            y1 = max(0, min(y1, h - 1))
            y2 = max(0, min(y2, h))
            if x2 <= x1 or y2 <= y1:
                continue
            
            # class 기준 임시 디렉토리
            class_dir = crop_root / str(cls_name)
            class_dir.mkdir(parents=True, exist_ok=True)
            crop_path = class_dir / f"{temp_id}_{det_idx}.jpg"

            # BGR -> RGB 변환 후 저장
            crop_img = image_bgr[y1:y2, x1:x2]
            crop_rgb = crop_img[:, :, ::-1]
            Image.fromarray(crop_rgb).save(str(crop_path), format="JPEG", quality=95)

            detections.append(
                {
                    "input_path": input_path,
                    "class_id": cls_id,
                    "class_name": cls_name,
                    "confidence": conf_score,
                    "bbox_xyxy": [x1, y1, x2, y2],
                    "crop_path": str(crop_path),
                }
            )

        # YOLO 결과 메모리 정리
        del results, result, image_bgr
        gc.collect()

    return detections

# ───────────────────────────────
# Airflow Task
# ───────────────────────────────
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
    """
    수행하는 일:
    1) YOLO detect + crop (임시 디렉토리)
    2) 파일명에서 brand/gender/product_code 파싱
    3) product_id 기준 중복 제거 (confidence 최고 1장만 유지)
    4) brand/gender/category 폴더 구조로 재배치
    5) 최종 메타데이터 반환 (+ DB upsert 가능)
    """

    # YOLO detect
    detections = _run_yolo_detect(
        records=records,
        brand=brand,
        model_path=model_path,
        crop_tmp_dir=crop_tmp_dir,
        imgsz=imgsz,
        score_thresh=score_thresh,
        iou=iou,
    )

    # local_path -> hdfs_path 매핑
    hdfs_by_local = {r["local_path"]: r.get("hdfs_path") for r in records}
    
    # product_id별 최고 confidence detection만 유지
    best_by_product_id: dict[str, dict[str, Any]] = {}

    for det in detections:
        input_name = Path(det["input_path"]).name
        match = NAME_RE.match(Path(input_name).stem)
        if not match:
            continue

        brand_name = match.group("brand")
        gender = match.group("gender")
        product_code = match.group("product_code")
        product_id = f"{brand_name}_{product_code}"

        category = _to_category(det.get("class_name", ""))
        if category is None:
            continue

        cand = {
            "product_id": product_id,
            "brand": brand_name,
            "gender": gender,
            "category": category,
            "product_code": product_code,
            "origin_hdfs_path": hdfs_by_local.get(det["input_path"]),
            "crop_path": det["crop_path"],
            "confidence": float(det.get("confidence", 0.0)),
        }

        prev = best_by_product_id.get(product_id)
        if prev is None or cand["confidence"] > prev["confidence"]:
            best_by_product_id[product_id] = cand

    # 최종 디렉토리 구조로 이동
    out_rows: list[dict[str, Any]] = []
    final_root = Path(crop_final_dir)

    for row in best_by_product_id.values():
        dst_dir = (
            final_root / row["brand"] / row["gender"].lower() / row["category"].lower()
        )
        dst_dir.mkdir(parents=True, exist_ok=True)

        image_filename = (
            f'{row["brand"]}_{row["gender"]}_{row["category"]}_{row["product_code"]}.jpg'
        )
        final_path = dst_dir / image_filename

        if final_path.exists():
            final_path.unlink()
        shutil.move(row["crop_path"], final_path)

        out_rows.append(
            {
                "product_id": row["product_id"],
                "brand": row["brand"],
                "gender": row["gender"],
                "category": row["category"],
                "product_code": row["product_code"],
                "origin_hdfs_path": row["origin_hdfs_path"],
                "crop_local_path": str(final_path),
                "image_filename": image_filename,
            }
        )

    # Products 테이블이 없으니 생기면 활성화
    # _upsert_postgres(out_rows)
    
    return out_rows
