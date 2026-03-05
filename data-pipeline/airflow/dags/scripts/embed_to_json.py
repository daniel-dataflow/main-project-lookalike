from __future__ import annotations
import os
import json
import gc
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from airflow.decorators import task

# ───────────────────────────────
# Airflow Task
# ───────────────────────────────
@task
def embed_to_json(rows: list[dict[str, Any]], out_root: str, batch_size: int = 1) -> list[str]:
    """
    수행하는 일:
        1. crop 이미지들을 하나씩(1 by 1) FashionCLIP으로 임베딩
        2. 성공 시 즉시 JSON 파일로 개별 저장
        3. [스킵 기능] 이미 저장된 JSON 파일이 있다면 임베딩을 생략하고 넘어감 (이어하기 기능)
        4. [안전장치] 에러 발생 시 스킵 & 매장마다 메모리 즉시 청소 (OOM 완벽 방어)
    """

    from PIL import Image
    from fashion_clip.fashion_clip import FashionCLIP

    # 입력이 없으면 바로 종료
    if not rows:
        return []

    print("🚀 [시작] FashionCLIP 모델을 로드합니다...")
    model = FashionCLIP(model_name="fashion-clip")
    
    out_paths: list[str] = []

    print(f"📦 총 {len(rows)}개의 데이터에 대해 개별 임베딩 처리를 시작합니다.")

    # ─────────────────────────
    # 메인 루프 (1:1 개별 처리 및 스킵 로직)
    # ─────────────────────────
    for idx, r in enumerate(rows, 1):
        image_path = r["crop_local_path"]

        # 1. 🌟 [핵심] 저장될 디렉토리와 JSON 파일 경로를 가장 먼저 계산!
        out_dir = Path(out_root) / str(r["brand"]) / str(r["gender"]).lower() / str(r["category"]).lower()
        out_path = out_dir / Path(r["image_filename"]).with_suffix(".json")

        # 2. 🌟 [스킵 로직] 이미 JSON 파일이 존재하면 무거운 임베딩 작업 생략!
        if out_path.exists():
            print(f"⏭️ [{idx}/{len(rows)}] [건너뜀] 이미 처리된 파일입니다: {out_path.name}")
            out_paths.append(str(out_path))
            continue

        # 3. 원본 이미지 파일이 실제로 존재하는지 확인 (FileNotFound 방지)
        if not os.path.exists(image_path):
            print(f"⚠️ [{idx}/{len(rows)}] [건너뜀] 원본 이미지를 찾을 수 없습니다: {image_path}")
            continue

        # 4. 파일 크기가 5KB 미만(약 5120 바이트)이면 버림
        file_size = os.path.getsize(image_path)
        if file_size < 5120:  
            print(f"🚨 [{idx}/{len(rows)}] [건너뜀] 1KB 더미 이미지 의심 (크기: {file_size} bytes): {image_path}")
            continue

        img = None
        try:
            # 5. 이미지 로드 및 RGB 변환 (흑백 이미지 방지)
            img = Image.open(image_path).convert("RGB")

            # 6. 이미지 크기 확인 (1x1 픽셀 등 더미 의심)
            if img.width < 10 or img.height < 10:
                print(f"🚨 [{idx}/{len(rows)}] [건너뜀] 이미지가 너무 작습니다: {image_path} - 크기: {img.size}")
                img.close()
                continue

            # 7. 단 1장만 모델에 넣고 즉시 임베딩 추출!
            vecs = model.encode_images(images=[img], batch_size=1)
            v = vecs[0]  # 첫 번째(유일한) 결과값 가져오기

            out_dir.mkdir(parents=True, exist_ok=True)

            # 8. 저장할 문서 구조 만들기
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

            # 9. JSON 파일 즉시 저장
            out_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            out_paths.append(str(out_path))

            if idx % 50 == 0:
                print(f"✅ 진행 중: {idx}/{len(rows)}장 완료...")

        except Exception as e:
            print(f"❌ [{idx}/{len(rows)}] [에러 발생 스킵] {image_path} - {e}")
            continue

        finally:
            if img is not None:
                img.close()
            gc.collect()

    print(f"🎉 [종료] 총 {len(out_paths)}개의 임베딩 JSON 처리가 완료되었습니다.")
    return out_paths
