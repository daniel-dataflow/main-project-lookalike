"""
이미지 처리 서비스
- 파일 검증, 메모리에서 썸네일 생성, WebHDFS 업로드
- 원본 이미지는 저장하지 않음 (용량 절약)
- 로컬 파일 시스템에는 아무것도 저장하지 않음
"""
import uuid
import logging
from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image
from fastapi import UploadFile, HTTPException
import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)


def validate_image_file(file: UploadFile) -> None:
    """이미지 파일 검증 (확장자, MIME 타입)"""
    allowed_extensions = {".jpg", ".jpeg", ".png"}
    file_ext = Path(file.filename or "unknown.jpg").suffix.lower()

    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail="지원하지 않는 파일 형식입니다. jpg, jpeg, png만 가능합니다.",
        )

    if file.content_type and not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="이미지 파일이 아닙니다.")


async def process_and_upload_thumbnail(file: UploadFile, user_id: str) -> dict:
    """
    이미지를 메모리에서 처리하고 썸네일만 HDFS에 업로드.
    원본은 저장하지 않음.

    Returns:
        dict: image_id, hdfs_thumb_path, file_size, width, height
    """
    settings = get_settings()
    image_id = str(uuid.uuid4())

    # 파일 읽기 (메모리)
    content = await file.read()

    # 파일 크기 검증
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"파일 크기는 {settings.MAX_UPLOAD_SIZE_MB}MB 이하여야 합니다.",
        )

    # 메타데이터 추출 + 썸네일 생성 (모두 메모리에서)
    metadata = _get_image_metadata(content)
    thumb_bytes = _create_thumbnail_bytes(
        content, settings.THUMBNAIL_SIZE, settings.THUMBNAIL_QUALITY
    )

    # HDFS 경로 구성
    date_path = datetime.now().strftime("%Y/%m/%d")
    hdfs_thumb_path = f"/images/thumb/{date_path}/{user_id}/{image_id}_thumb.jpg"

    # HDFS 업로드
    hdfs_ok = await _upload_bytes_to_hdfs(thumb_bytes, hdfs_thumb_path)

    if not hdfs_ok:
        logger.warning(f"HDFS 업로드 실패: {hdfs_thumb_path}")

    return {
        "image_id": image_id,
        "hdfs_thumb_path": hdfs_thumb_path if hdfs_ok else None,
        "file_size": len(content),
        "thumb_size": len(thumb_bytes),
        "hdfs_uploaded": hdfs_ok,
        **metadata,
    }


def _create_thumbnail_bytes(
    image_bytes: bytes, size: int = 150, quality: int = 85
) -> bytes:
    """메모리에서 썸네일 생성, bytes 반환 (디스크 쓰기 없음)"""
    with Image.open(BytesIO(image_bytes)) as img:
        if img.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "RGBA":
                background.paste(img, mask=img.split()[3])
            else:
                img_rgb = img.convert("RGBA")
                background.paste(img_rgb, mask=img_rgb.split()[3])
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        img.thumbnail((size, size), Image.Resampling.LANCZOS)

        buffer = BytesIO()
        img.save(buffer, "JPEG", quality=quality, optimize=True)
        return buffer.getvalue()


def _get_image_metadata(image_bytes: bytes) -> dict:
    """이미지 메타데이터 추출 (width, height)"""
    try:
        with Image.open(BytesIO(image_bytes)) as img:
            return {"width": img.width, "height": img.height}
    except Exception:
        return {"width": 0, "height": 0}


async def _upload_bytes_to_hdfs(data: bytes, hdfs_path: str) -> bool:
    """
    WebHDFS REST API로 bytes 데이터를 HDFS에 업로드.
    2-step: MKDIRS → CREATE(307) → PUT data.
    """
    settings = get_settings()
    namenode_url = (
        f"http://{settings.HADOOP_NAMENODE_HOST}:{settings.HDFS_WEBHDFS_PORT}"
    )

    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=30.0) as client:
            # 디렉토리 생성
            parent_dir = str(Path(hdfs_path).parent)
            await client.put(
                f"{namenode_url}/webhdfs/v1{parent_dir}?op=MKDIRS&user.name=root"
            )

            # CREATE 요청 → 307 리다이렉트
            create_url = (
                f"{namenode_url}/webhdfs/v1{hdfs_path}"
                f"?op=CREATE&overwrite=true&user.name=root"
            )
            resp = await client.put(create_url)

            if resp.status_code not in (307, 201):
                logger.warning(f"HDFS CREATE 실패: status={resp.status_code}")
                return False

            redirect_url = resp.headers.get("Location")
            if not redirect_url:
                logger.warning("HDFS 리다이렉트 URL 없음")
                return False

            # 데이터 PUT
            data_resp = await client.put(
                redirect_url,
                content=data,
                headers={"Content-Type": "application/octet-stream"},
            )

            if data_resp.status_code in (200, 201):
                logger.info(f"✅ HDFS 업로드 완료: {hdfs_path}")
                return True
            else:
                logger.warning(f"HDFS PUT 실패: status={data_resp.status_code}")
                return False

    except Exception as e:
        logger.warning(f"HDFS 업로드 실패: {e}")
        return False


async def read_thumbnail_from_hdfs(hdfs_path: str) -> bytes | None:
    """HDFS에서 썸네일 이미지를 읽어 bytes로 반환"""
    settings = get_settings()
    namenode_url = (
        f"http://{settings.HADOOP_NAMENODE_HOST}:{settings.HDFS_WEBHDFS_PORT}"
    )

    try:
        open_url = (
            f"{namenode_url}/webhdfs/v1{hdfs_path}?op=OPEN&user.name=root"
        )
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            resp = await client.get(open_url)
            if resp.status_code == 200:
                return resp.content
    except Exception as e:
        logger.warning(f"HDFS 읽기 실패: {e}")

    return None
