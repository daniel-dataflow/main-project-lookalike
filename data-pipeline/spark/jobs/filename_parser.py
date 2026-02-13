# spark/common/filename_parser.py

import os
import re
from typing import Dict


def parse_filename_loose(file_path: str) -> Dict[str, str]:
    """
    기존 방식 (유연, 실패 안 함)
    """
    filename = os.path.basename(file_path)
    name = filename.replace(".html", "")
    parts = name.split("_")

    result = {
        "brand": "UNKNOWN",
        "category_code": "UNKNOWN",
        "model_hint": None
    }

    if len(parts) < 3:
        return result

    result["brand"] = "".join(filter(str.isalpha, parts[0])).lower()
    result["category_code"] = f"{parts[1]}_{parts[2]}"

    for p in parts[3:]:
        if any(c.isdigit() for c in p):
            result["model_hint"] = p
            break

    return result


def parse_filename_strict(file_path: str) -> Dict[str, str]:
    """
    배치 파이프라인용 (형식 보장)
    """
    name = os.path.basename(file_path).replace(".html", "")

    pattern = (
        r"(?P<brand>[a-zA-Z0-9]+)"
        r"(?P<gender>Men|Women)_"
        r"(?P<category>[A-Za-z]+)_"
        r"(?P<product_code>[^_]+)_"
        r"(?P<date>\d{8})"
    )

    match = re.match(pattern, name)

    if not match:
        raise ValueError(f"파일명 형식 오류: {file_path}")

    return {
        "brand": match.group("brand").upper(),
        "gender": match.group("gender").upper(),
        "category": match.group("category").upper(),
        "product_code": match.group("product_code"),
        "crawl_date": match.group("date"),
    }
