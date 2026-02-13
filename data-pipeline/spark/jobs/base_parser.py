# spark/parsers/base_parser.py

from abc import ABC, abstractmethod
from typing import Dict, List


class BaseParser(ABC):
    """
    모든 브랜드 HTML 파서의 인터페이스
    """

    @abstractmethod
    def parse(self, html: str) -> Dict:
        """
        HTML → 공통 product 구조로 변환
        """
        pass
