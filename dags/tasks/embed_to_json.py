from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from airflow.decorators import task
# from PIL import Image
# from fashion_clip.fashion_clip import FashionCLIP


# ───────────────────────────────
# Airflow Task
# ───────────────────────────────
@task
def embed_to_json(rows: list[dict[str, Any]], out_root: str, batch_size: int = 16) -> list[str]:
    """
    수행하는 일:
        1. crop 이미지들을 FashionCLIP으로 임베딩
        2. 배치 단위로 처리하여 메모리 사용량 제어
        3. 임베딩 결과를 JSON 파일로 저장
        4. 생성된 JSON 경로 리스트 반환

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

        # batch 초기화
        batch_imgs.clear()
        batch_rows.clear()


    # ─────────────────────────
    # 메인 루프
    # ─────────────────────────
    for r in rows:
        # crop 이미지 로드 (RGB로 통일)
        img = Image.open(r["crop_local_path"]).convert("RGB")
        batch_imgs.append(img)
        batch_rows.append(r)

        # batch_size 도달 시 즉시 처리
        if len(batch_imgs) >= batch_size:
            flush()

    # 남은 이미지 처리
    flush()
    
    return out_paths
