import logging
import os
from io import BytesIO
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# YOLO class name mapping (from exceptional handling script)
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
    YOLOv11 의류 객체 탐지를 위한 완전 독립적인 클래스.
    기존 EncoderHub나 ML 모델들과 자원을 공유하지 않습니다.
    """
    def __init__(self, model_name: str | None = None):
        # 기본 경로는 새 규칙(../weights/best.pt)을 사용한다.
        # env(YOLO_WEIGHTS_PATH)로 런타임에서 즉시 변경 가능하다.
        self.model_name = model_name or os.getenv("YOLO_WEIGHTS_PATH", "../weights/best.pt")
        self.model = None

    def load(self):
        try:
            from ultralytics import YOLO
            
            # 절대 경로나 상대 경로 기반으로 가중치 파일 로딩
            base_dir = os.path.dirname(__file__)
            primary_path = os.path.abspath(os.path.join(base_dir, self.model_name))
            legacy_path = os.path.abspath(os.path.join(base_dir, "../yolo/yolo_weights.pt"))

            weight_path = primary_path
            if not os.path.exists(weight_path):
                if os.path.exists(legacy_path):
                    logger.warning(
                        f"YOLO 가중치 기본 경로를 찾지 못해 legacy 경로로 fallback 합니다: {legacy_path}"
                    )
                    weight_path = legacy_path
                else:
                    logger.error(f"YOLO 가중치 파일을 찾을 수 없습니다: {primary_path}")
                    return

            logger.info(f"Custom YOLO 모델({weight_path}) 로드 중...")
                
            self.model = YOLO(weight_path)
            logger.info("Custom YOLO 모델 로드 완료.")
        except Exception as e:
            logger.error(f"YOLO 모델 로드 실패: {e}")

    def detect_boxes(self, pil_img: Image.Image) -> list:
        """
        이미지를 받아서 패션/의류 관련 바운딩 박스를 추출해 리턴합니다.
        (기본 COCO 모델 사용 시 person, backpack, umbrella, tie, suitcase 등이 있지만,
         가능하다면 의류 전용 모델 가중치를 사용하는 것이 좋습니다. 
         여기서는 기본 yolo11s.pt를 활용하되, 프론트에서 크롭 확인하도록 범용 BBox를 던집니다.)
        """
        if self.model is None:
            logger.warning("YOLO 모델이 로드되지 않았습니다. 빈 결과를 반환합니다.")
            return []

        w, h = pil_img.size
        # conf=0.25 (스코어 미만 제외) 및 iou=0.7 적용하여 파인튜닝 스크립트(yolo_exceptional_handling.py) 예외처리 조건 완벽 동기화
        # 빈공간 상의로 잡는 문제 해결을 위해 conf threshold 수정
        results = self.model(pil_img, conf=0.38, iou=0.7)
        
        boxes_out = []
        for r in results:
            if r.boxes is None or len(r.boxes) == 0:
                continue
                
            for box in r.boxes:
                x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
                conf = float(box.conf[0].item())
                cls_id = int(box.cls[0].item())
                
                # boundary clamp (예외 처리 로직 추가)
                x1 = max(0, min(x1, w - 1))
                x2 = max(0, min(x2, w))
                y1 = max(0, min(y1, h - 1))
                y2 = max(0, min(y2, h))
                if x2 <= x1 or y2 <= y1:
                    continue
                
                # 커스텀 학습된 라벨 이름 (예: top, bottom, outer 등)
                raw_label_name = r.names[cls_id] if r.names else str(cls_id)
                
                # 카테고리 맵핑 (예외 처리 로직 추가)
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
        
        # 신뢰도(conf) 순으로 정렬
        boxes_out.sort(key=lambda x: x["conf"], reverse=True)
        return boxes_out

yolo_detector = YoloDetector()
