from __future__ import annotations
import os
from pathlib import Path
from typing import Any
import requests
from airflow.decorators import task

# 다운할 이미지 대상으로 허용할 이미지 확장자
VALID_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _list_files(hdfs_root: str) -> list[str]:
    """
    webHDFS API를 사용해 HDFS 디렉토리를 재귀적으로 순회하면서
    이미지 파일 경로만 수집.

    Args:
        hdfs_root: HDFS 상의 시작 디렉토리 경로

    Returns:
        이미지 파일들의 HDFS 전체 경로 리스트
    """

    # 환경변수에서 namenode 정보 로드
    nn_host = os.getenv("HDFS_NAMENODE_HOST", "namenode")
    nn_port = os.getenv("HDFS_NAMENODE_WEBUI_PORT", "9870")
    hdfs_user = os.getenv("HDFS_USER", "root")

    # DFS 탐색을 위한 스택
    stack = [hdfs_root]
    out: list[str] = []

    while stack:
        cur = stack.pop()

        # WebHDFS LISTSTATUS 호출
        url = f"http://{nn_host}:{nn_port}/webhdfs/v1{cur}?op=LISTSTATUS&user.name={hdfs_user}"
        r = requests.get(url, timeout=30)
        r.raise_for_status()

        # 현재 디렉토리 하위 파일/디렉토리 목록 순회
        for fs in r.json()["FileStatuses"]["FileStatus"]:
            p = f"{cur.rstrip('/')}/{fs['pathSuffix']}"

            if fs["type"] == "DIRECTORY":
                # 디렉토리면 스택에 추가해서 계속 탐색
                stack.append(p)

            elif Path(p).suffix.lower() in VALID_EXTS:
                # 허용된 확장자의 파일만 수집
                out.append(p)

    # 결과를 항상 동일한 순서로 반환
    return sorted(out)


def _download(hdfs_path: str, local_path: Path) -> None:
    """
    WebHDFS OPEN API를 이용해 단일 파일을 로컬로 다운로드.

    Args:
        hdfs_path: HDFS 상의 파일 경로
        local_path: 로컬에 저장할 파일 경로
    """

    nn_host = os.getenv("HDFS_NAMENODE_HOST", "namenode")
    nn_port = os.getenv("HDFS_NAMENODE_WEBUI_PORT", "9870")
    hdfs_user = os.getenv("HDFS_USER", "root")

    # WebHDFS OPEN 요청 (실제 데이터 노드 URL은 redirect로 내려옴)
    open_url = f"http://{nn_host}:{nn_port}/webhdfs/v1{hdfs_path}?op=OPEN&user.name={hdfs_user}"
    r1 = requests.get(open_url, allow_redirects=False, timeout=30)

    # Location 헤더에 실제 datanode 다운로드 url이 들어있음
    loc = r1.headers.get("Location")
    if not loc:
        raise RuntimeError(f"WebHDFS OPEN failed: {hdfs_path}")
    
    # 로컬 디렉토리 생성
    local_path.parent.mkdir(parents=True, exist_ok=True)

    # 스트리밍 방식으로 파일 다운로드 (대용량 대비)
    with requests.get(loc, stream=True, timeout=120) as r2:
        r2.raise_for_status()
        with open(local_path, "wb") as f:
            for chunk in r2.iter_content(1024 * 1024):
                if chunk:
                    f.write(chunk)


# ───────────────────────────────────
# Airflow Task
# ───────────────────────────────────
@task
def fetch_from_hdfs(
    brand: str,
    hdfs_root: str,
    incoming_dir: str,
    limit: int = 300,
) -> list[dict[str, Any]]:
    """
    Airflow Task:
    - HDFS에서 이미지 파일 목록 조회
    - 최대 limit 개수만 로컬 incoming 디렉토리로 다운로드
    - 후속 태스크에서 사용할 메타데이터 리스트 반환

    Args:
        brand: 브랜드명
        hdfs_root: HDFS 이미지 루트 경로
        incoming_dir: 로컬 저장 디렉토리
        limit: 최대 다운로드 파일 수

    Returns:
        이미지 메탇이터 dict 리스트
    """
    
    # HDFS에서 이미지 파일 경로 수집 후 limit 적용
    files = _list_files(hdfs_root)[:limit]

    incoming = Path(incoming_dir)
    incoming.mkdir(parents=True, exist_ok=True)

    out: list[dict[str, Any]] = []

    for p in files:
        # 로컬 파일명은 HDFS 파일명만 사용
        local = incoming / Path(p).name

        # 이미 다운로드된 파일은 재다운로드하지 않음
        if not local.exists():
            _download(p, local)
        
        # Airflow XCom으로 넘길 메타데이터 구성
        out.append(
            {
                "brand": brand,
                "hdfs_path": p,
                "local_path": str(local),
                "image_filename": local.name,
            }
        )

    return out
