from __future__ import annotations

"""라우터에서 공통으로 쓰는 간단한 요청 파싱 함수 모음."""

from typing import Optional, Sequence


def parse_category(category: Optional[str]) -> Optional[str | Sequence[str]]:
    """콤마로 구분된 필터는 분리하고, 단일 값은 그대로 유지한다."""
    if category is None:
        return None
    trimmed = category.strip()
    if not trimmed:
        return None
    if "," in trimmed:
        parts = [x.strip() for x in trimmed.split(",") if x.strip()]
        return parts if parts else None
    return trimmed
