import os
import json
import gc
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

def process_fashion_clip_embedding(rows: list[dict[str, Any]], out_root: str, batch_size: int = 1) -> list[str]:
    from PIL import Image
    from fashion_clip.fashion_clip import FashionCLIP

    if not rows:
        return []

    print("[시작] FashionCLIP 모델을 로드합니다...")
    model = FashionCLIP(model_name="fashion-clip")
    
    out_paths: list[str] = []
    print(f"총 {len(rows)}개의 데이터에 대해 개별 임베딩 처리를 시작합니다.")

    for idx, r in enumerate(rows, 1):
        image_path = r["crop_local_path"]

        out_dir = Path(out_root) / str(r["brand"]) / str(r["gender"]).lower() / str(r["category"]).lower()
        out_path = out_dir / Path(r["image_filename"]).with_suffix(".json")

        if out_path.exists():
            print(f"[{idx}/{len(rows)}] [건너뜀] 이미 처리된 파일입니다: {out_path.name}")
            out_paths.append(str(out_path))
            continue

        if not os.path.exists(image_path):
            print(f"[{idx}/{len(rows)}] [건너뜀] 원본 이미지를 찾을 수 없습니다: {image_path}")
            continue

        file_size = os.path.getsize(image_path)
        if file_size < 5120:  
            print(f"[{idx}/{len(rows)}] [건너뜀] 1KB 더미 이미지 의심: {image_path}")
            continue

        img = None
        try:
            img = Image.open(image_path).convert("RGB")

            if img.width < 10 or img.height < 10:
                print(f"[{idx}/{len(rows)}] [건너뜀] 이미지가 너무 작습니다: {image_path}")
                img.close()
                continue

            vecs = model.encode_images(images=[img], batch_size=1)
            v = vecs[0] 

            out_dir.mkdir(parents=True, exist_ok=True)

            doc = {
                "product_id": r["product_id"],
                "brand": r["brand"],
                "gender": r["gender"],
                "category": r["category"],
                "product_code": r["product_code"],
                "image_filename": r["image_filename"],
                "image_path": r["crop_local_path"],
                "origin_hdfs_path": r["origin_hdfs_path"],
                "image_vector": [float(x) for x in v],
                "create_dt": datetime.now(timezone.utc).isoformat(),
            }

            out_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            out_paths.append(str(out_path))

            if idx % 50 == 0:
                print(f"진행 중: {idx}/{len(rows)}장 완료...")

        except Exception as e:
            print(f"[{idx}/{len(rows)}] [에러 발생 스킵] {image_path} - {e}")
            continue

        finally:
            if img is not None:
                img.close()
            gc.collect()

    print(f"[종료] 총 {len(out_paths)}개의 임베딩 JSON 처리가 완료되었습니다.")
    return out_paths
