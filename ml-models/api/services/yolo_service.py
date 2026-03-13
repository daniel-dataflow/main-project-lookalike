import logging
import os
from PIL import Image

logger = logging.getLogger(__name__)

# 학습 라벨과 한글 별칭을 UI에서 쓰는 카테고리 이름으로 정규화한다.
CATEGORY_MAP = {
    "top": "Top",
    "상의": "Top",
    "bottom": "Bottom",
    "하의": "Bottom",
    "outer": "Outer",
    "아우터": "Outer",
}

class YoloDetector:
    """
    YOLO 기반 의류 탐지 서비스.
    임베딩 검색 파이프라인과 분리된 채로 모델 로딩과 바운딩 박스 추론을 담당한다.
    """
    def __init__(self, model_name: str | None = None):
        # 환경변수로 가중치 경로를 바꿀 수 있다.
        self.model_name = model_name or os.getenv("YOLO_WEIGHTS_PATH", "../weights/best.pt")
        self.model = None

    def load(self):
        """앱 시작 시 YOLO 가중치를 한 번만 로드한다."""
        try:
            # 모듈 import 시점 실패를 피하기 위해
            # 모델 의존성은 startup 시점에 지연 import한다.
            from ultralytics import YOLO

            base_dir = os.path.dirname(__file__)
            primary_path = os.path.abspath(os.path.join(base_dir, self.model_name))

            weight_path = primary_path
            if not os.path.exists(weight_path):
                logger.error(f"YOLO 가중치 파일을 찾을 수 없습니다: {primary_path}")
                return

            logger.info(f"Custom YOLO 모델({weight_path}) 로드 중...")
                
            self.model = YOLO(weight_path)
            logger.info("Custom YOLO 모델 로드 완료.")
        except Exception as e:
            logger.error(f"YOLO 모델 로드 실패: {e}")

    def detect_boxes(self, pil_img: Image.Image) -> list:
        """
        의류 영역을 탐지하고 정규화된 바운딩 박스를 반환한다.
        """
        if self.model is None:
            logger.warning("YOLO 모델이 로드되지 않았습니다. 빈 결과를 반환합니다.")
            return []

        w, h = pil_img.size
        # 학습/예외처리 스크립트와 동일한 기준으로 threshold를 맞춘다.
        results = self.model(pil_img, conf=0.38, iou=0.7)

        boxes_out = []
        for r in results:
            if r.boxes is None or len(r.boxes) == 0:
                continue

            for box in r.boxes:
                x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
                conf = float(box.conf[0].item())
                cls_id = int(box.cls[0].item())

                # 후속 크롭 처리에서 이미지 범위를 벗어나지 않도록 좌표를 보정한다.
                x1 = max(0, min(x1, w - 1))
                x2 = max(0, min(x2, w))
                y1 = max(0, min(y1, h - 1))
                y2 = max(0, min(y2, h))
                if x2 <= x1 or y2 <= y1:
                    continue

                raw_label_name = r.names[cls_id] if r.names else str(cls_id)
                label_name = CATEGORY_MAP.get(str(raw_label_name).strip().lower(), raw_label_name)

                boxes_out.append({
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2,
                    "w": x2 - x1,
                    "h": y2 - y1,
                    "conf": conf,
                    "label": label_name
                })

        # UI에서 가장 신뢰도 높은 박스부터 쓰기 쉽도록 정렬한다.
        boxes_out.sort(key=lambda x: x["conf"], reverse=True)
        return boxes_out

yolo_detector = YoloDetector()
