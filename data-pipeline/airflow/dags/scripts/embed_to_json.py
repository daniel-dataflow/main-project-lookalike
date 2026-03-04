from __future__ import annotations
import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from airflow.decorators import task
# from PIL import Image
# from fashion_clip.fashion_clip import FashionCLIP
import gc

# ───────────────────────────────
# Airflow Task
# ───────────────────────────────
@task
def embed_to_json(rows: list[dict[str, Any]], out_root: str, batch_size: int = 4) -> list[str]:
    """
    수행하는 일:
        1. crop 이미지들을 FashionCLIP으로 임베딩
        2. 배치 단위로 처리하여 메모리 사용량 제어
        3. 임베딩 결과를 JSON 파일로 저장
        4. 생성된 JSON 경로 리스트 반환
        5. [안전장치] 누락된 파일이나 손상된/흑백 이미지는 자동으로 건너뜀

    Args:
        rows: 이전 태스크에서 전달된 이미지 메타데이터 리스트
        out_root: 임베딩 JSON 저장 루트 디렉토리
        batch_size: 한 번에 임베딩할 이미지 개수

    Returns:
        생성된 embedding JSON 파일 경로 리스트
    """

    from PIL import Image
    from fashion_clip.fashion_clip import FashionCLIP

    # 입력이 없으면 바로 종료
    if not rows:
        return []

    # FashionCLIP 모델 로드 - 이미지 임베딩
    model = FashionCLIP(model_name="fashion-clip")

    out_paths: list[str] = []

    # 배치 단위로 이미지와 row를 함께 관리
    batch_imgs = []
    batch_rows = []

    def flush():
        """
        현재 batch에 쌓인 이미지들을 임베딩하고
        JSON 파일로 저장한 뒤 batch를 초기화
        """
        if not batch_imgs:
            return

        # 이미지 임베딩 (shape: [batch_size, embedding_dim])
        vecs = model.encode_images(images=batch_imgs, batch_size=batch_size)

        # row -> embedding 1:1 매핑
        for r, v in zip(batch_rows, vecs):
            # 브랜드/성별/카테고리 기준 디렉토리 구조 유지
            out_dir = Path(out_root) / r["brand"] / r["gender"].lower() / r["category"].lower()
            out_dir.mkdir(parents=True, exist_ok=True)

            # 이미지 파일명과 동일한 이름의 .json 생성
            out_path = out_dir / Path(r["image_filename"]).with_suffix(".json")

            # 저장할 문서 구조
            doc = {
                "product_id": r["product_id"],
                "brand": r["brand"],
                "gender": r["gender"],
                "category": r["category"],
                "product_code": r["product_code"],
                "image_filename": r["image_filename"],
                "image_path": r["crop_local_path"],
                "origin_hdfs_path": r["origin_hdfs_path"],
                # numpy / torch tensor → JSON 직렬화를 위해 float 변환
                "image_vector": [float(x) for x in v],
                # 생성 시각
                "create_dt": datetime.now(timezone.utc).isoformat(),
            }

            # JSON 파일 저장
            out_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            out_paths.append(str(out_path))
        
        for img in batch_imgs:
            img.close()
        # batch 초기화
        batch_imgs.clear()
        batch_rows.clear()

        gc.collect()


    # ─────────────────────────
    # 메인 루프 (예외 처리 추가)
    # ─────────────────────────
    for r in rows:
        image_path = r["crop_local_path"]
        
        # 1. 파일이 실제로 존재하는지 확인 (FileNotFound 방지)
        if not os.path.exists(image_path):
            print(f"⚠️ [건너뜀] 파일을 찾을 수 없습니다: {image_path}")
            continue
            
        # 2. 이미지 로드 및 RGB 변환 (흑백 이미지/손상 파일 방지)
        
        # 파일 크기가 5KB 미만(약 5120 바이트)이면 버림
        file_size = os.path.getsize(image_path)
        if file_size < 5120:  # 필요에 따라 2048(2KB) 등으로 조절 가능
            print(f"🚨 [건너뜀] 1KB 더미 이미지 의심 (크기: {file_size} bytes): {image_path}")
            continue

        try:
            img = Image.open(image_path).convert("RGB")
            
            if img.width < 10 or img.height < 10:
                print(f"🚨 [건너뜀] 이미지가 너무 작습니다 (1x1 픽셀 등 더미 의심): {image_path} - 크기: {img.size}")
                img.close()
                continue
                
        except Exception as e:
            print(f"🚨 [건너뜀] 이미지 열기 실패: {image_path} - {e}")
            continue

        # 무사히 통과한 정상 데이터만 배치에 추가
        batch_imgs.append(img)
        batch_rows.append(r)

        # batch_size 도달 시 즉시 처리
        if len(batch_imgs) >= batch_size:
            flush()

    # 남은 이미지 처리
    flush()

    return out_paths
