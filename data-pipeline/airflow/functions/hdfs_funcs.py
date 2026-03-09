import os
from pathlib import Path
from typing import Any
import requests

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


# 기존 @task 안에 있던 핵심 로직을 일반 함수로 만듭니다.
def process_hdfs_downloads(brand: str, hdfs_root: str, incoming_dir: str) -> list[dict[str, Any]]:
    files = _list_files(hdfs_root)
    incoming = Path(incoming_dir)
    incoming.mkdir(parents=True, exist_ok=True)

    out: list[dict[str, Any]] = []

    for p in files:
        local = incoming / Path(p).name
        if not local.exists():
            _download(p, local)
        
        out.append({
            "brand": brand,
            "hdfs_path": p,
            "local_path": str(local),
            "image_filename": local.name,
        })
    return out
