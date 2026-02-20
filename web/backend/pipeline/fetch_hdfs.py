from __future__ import annotations

import os
import requests
import subprocess
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor


# 유틸 함수
def build_product_id(row: dict[str, Any], brand_arg: str) -> str:
    # DB product_id 우선
    pid = row.get("product_id")
    if pid:
        return str(pid)

    # 없으면 brand + product_code로 생성
    product_code = row.get("product_code")
    if not product_code:
        raise ValueError(f"Missing product_code for row: {row}")

    brand_val = (row.get("brand_name") or brand_arg).strip()
    return f"{brand_val}_{product_code}"


def fetch_images_from_hdfs(
        # 한 번에 최대 몇 개의 이미지 경로를 가져올지
        limit: int = 100,
        # 브랜드 파라미터
        brand: str | None = None,
        # 로컬 입력 폴더 경로 지정
        incoming_dir: str | None = None,
) -> list[dict[str, Any]]: # 반환 타입 힌트
    """
    products 테이블의 img_hdfs_path를 조회해서 HDFS -> 로컬 다운로드.
    반환: [{"product_id": ..., "hdfs_path": ..., "local_path": ...}, ...]
    """

    # 1. 로컬 입력 폴더 보장
    incoming_dir = incoming_dir or os.getenv("INCOMING_DIR", "/opt/airflow/data/incoming")
    incoming = Path(incoming_dir)
    incoming.mkdir(parents=True, exist_ok=True)

    if not brand:
        raise ValueError("brand is required")

    # 2. DB 접속 정보 (환경변수 우선)
    db_host = os.getenv("POSTGRES_HOST", "postgresql")
    db_port = int(os.getenv("POSTGRES_PORT", "5432"))
    db_name = os.getenv("POSTGRES_DB", "datadb")
    db_user = os.getenv("POSTGRES_USER", "datauser")
    db_password = os.getenv("POSTGRES_PASSWORD", "")

    # products.product_id, products.img_hdfs_path
    query = """
        SELECT product_id, img_hdfs_path
        FROM "Products"
        WHERE img_hdfs_path IS NOT NULL
          AND LOWER(brand_name) = LOWER(%s)
        ORDER BY product_id
        LIMIT %s
    """

    # 3. Postgres 조회
    with psycopg2.connect(
        host=db_host,
        port=db_port,
        dbname=db_name,
        user=db_user,
        password=db_password,
    ) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (brand, limit))
            rows = cur.fetchall()

    downloaded: list[dict[str, Any]] = []

    # 4. HDFS -> 로컬 다운로드
    for row in rows:
        product_id = build_product_id(row, brand)
        hdfs_path = row["img_hdfs_path"]

        suffix = Path(hdfs_path).suffix or ".jpg"
        local_name = f"{product_id}{suffix}"
        local_path = incoming / local_name

        # 재실행 시 중복 다운로드 방지
        if not local_path.exists():
            nn_host = os.getenv("HDFS_NAMENODE_HOST", "namenode")
            nn_web_port = os.getenv("HDFS_NAMENODE_WEBUI_PORT", "9870")
            hdfs_user = os.getenv("HDFS_USER", "root")

            open_url = (
                f"http://{nn_host}:{nn_web_port}/webhdfs/v1{hdfs_path}"
                f"?op=OPEN&user.name={hdfs_user}"
            )

            r1 = requests.get(open_url, allow_redirects=False, timeout=30)
            if r1.status_code not in (307, 308) or "Location" not in r1.headers:
                raise RuntimeError(
                    f"WebHDFS OPEN failed: product_id={product_id}, "
                    f"path={hdfs_path}, status={r1.status_code}, body={r1.text[:200]}"
                )

            with requests.get(r1.headers["Location"], stream=True, timeout=120) as r2:
                r2.raise_for_status()
                with open(local_path, "wb") as f:
                    for chunk in r2.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)


        downloaded.append(
            {
                "product_id": product_id,
                "product_code": row.get("product_code"),
                "brand": brand,
                "hdfs_path": hdfs_path,
                "local_path": str(local_path),
            }
        )


    return downloaded

