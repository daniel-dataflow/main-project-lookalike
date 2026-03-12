from airflow.decorators import task
from typing import Any

from functions.yolo_funcs import process_yolo_images

@task(task_id="yolo_reorganize_dedup_upsert")
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
    
    return process_yolo_images(
        records=records,
        brand=brand,
        crop_tmp_dir=crop_tmp_dir,
        crop_final_dir=crop_final_dir,
        model_path=model_path,
        imgsz=imgsz,
        score_thresh=score_thresh,
        iou=iou
    )
