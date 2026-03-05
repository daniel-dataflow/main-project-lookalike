# 📖 핵심만 짚어주는 머신러닝 검색 연동 가이드

## 1. 지금 우리의 상황과 가장 공수를 줄이는 방법
질문자님 말씀이 백번 맞습니다! 현재 **백엔드(FastAPI)** 코드 안에는 이미 엄청난 기능들이 완성되어 있습니다.
* Elasticsearch에 접속해서 코사인 유사도 점수를 계산하는 기능 (`_search_by_knn`)
* 찾아낸 상품 ID를 가지고 DB에서 실시간 네이버 최저가 5개를 붙여주는 기능 (`_hydrate_from_db`)

**[핵심 결론]**
백엔드에는 찾아낸 상품 ID를 가지고 DB에서 실시간 네이버 최저가나 상세 가격을 붙여주는 기능(`_hydrate_from_db`)만 남아있도록 합니다.
공수를 최소화하고 리소스를 분리하기 위해 **ML팀은 벡터 변환 후 Elasticsearch에 직접 검색 쿼리까지 날리고, 가장 매칭되는 상품 ID 6개와 점수를** 뽑아서 백엔드에 다시 돌려주는 새로운 아키텍처로 변경되었습니다.

---

## 2. 데이터가 흘러가는 순서 (초보자용 개념 잡기)

검색이 시작되고 끝날 때까지 데이터가 어떻게 변하면서 이동하는지, 4단계로 아주 쉽게 설명해 드립니다.

### 🍅 단계 1: "나 이 옷 찾아줘!" (Web ➡️ Backend)
* **상황**: 사용자가 웹페이지에서 본인이 찍은 **옷 사진(.jpg)** 과 **"어두운 색"** 이라는 검색어를 올립니다.
* **백엔드 위치**: `routers/search.py` 의 `search_by_image` 함수가 이 사진과 글자를 덥석 받습니다.

### 🍅 단계 2: "가장 비슷한 옷 찾아내기" (ML팀 아바타 서버 ↔️ Elasticsearch)
* **상황**: 백엔드는 사진을 분석할 능력이 없습니다. 그래서 ML팀이 띄워놓은 작은 **'아바타 서버(단순 번역 API)'** 에 사진을 넘겨주며 번역을 부탁합니다.
* **ML팀 역할**: 미니 서버 안에서 사진이나 텍스트를 모델에 통과시켜 각자의 벡터 배열을 뽑아냅니다. 그리고 **ML 서버가 직접 Elasticsearch 엔진의 문을 두드려** 방금 찾아낸 숫자 배열과 가장 비슷한 옷 6건을 검색합니다. 검색 결과로 나온 상품 ID 6개와 개별 유사도 점수를 백엔드에 쏙 돌려줍니다.

### 🍅 단계 3: "결과 합치기 (Hydration)" (Backend ➡️ Database ➡️ Web)
* **상황**: ML서버가 백엔드에게 넘겨준 건 단순한 ID 숫자(예: `153123`) 6개와 점수일 뿐입니다. 화면에는 이름과 가격을 그려야 합니다.
* **백엔드 위치**: 백엔드는 이제 이 점수 데이터(`ml_product_scores`)를 들고 `search_service.py` 내부의 로직으로 들어갑니다. 바로 `_hydrate_from_db` 함수가 출동합니다!

### 🍅 단계 4: "상품 이름과 최저가 이쁘게 포장하기" (Backend ➡️ Database ➡️ Web)
* **상황**: ES가 찾아준 건 단순한 ID 숫자(예: `153123`)일 뿐입니다. 화면에는 이름과 가격을 그려야 합니다.
* **백엔드 위치**: `search_service.py` 내부의 `_hydrate_from_db` 함수가 출동합니다!
* **동작**: ID 4개를 들고 PostgreSQL(DB)에 가서 상품 이름, 사진 주소, 그리고 **제일 싼 쇼핑몰 5개**를 싹 다 조인해서 예쁜 리스트로 포장합니다. 그리고 이걸 웹페이지로 쏴주면 사용자 화면에 검색 결과가 뜹니다!

---

## 3. 두 팀이 해야 할 일 (동일 서버 내 연동 가이드)

프로젝트 환경상 두 팀의 코드가 **같은 서버(컴퓨터)의 Docker** 안에서 돌아가야 합니다. 이를 위해 ML팀의 코드를 `docker-compose.yml` 에 하나의 컨테이너로 추가하여, 내부 네트워크로 통신하는 가장 깔끔한 방법을 소개합니다. 

### 🧑‍🍳 ML팀 To-Do: "초소형 번역 API 컨테이너 만들기"
백엔드 팀(질문자님)이 ML팀의 수고를 덜어드리기 위해 **미리 API 서버의 뼈대(Skeleton)와 Docker 환경을 모두 만들어 두었습니다!**

**[작업 위치]**
ML팀은 프로젝트 내 `ml-models/api/` 폴더 안으로 가시면 됩니다. 
* `main.py`: 서버 실행 코드
* `requirements.txt`: 필요한 파이썬 라이브러리 목록
* `Dockerfile`: 도커 빌드 설정

`ml-models/api/main.py` 파일을 열어보시면 아래와 같은 코드가 작성되어 있습니다. ML팀은 주석으로 `# TODO` 라고 적힌 부분에 본인들의 **1. YOLO 카테고리 추출**, **2. CLIP 이미지 임베딩**, **3. 자연어 태그 추출** 로직만 채워 주시면 됩니다!

```python
from fastapi import FastAPI, UploadFile, File, Form
from typing import Optional
import uvicorn
import logging
import io

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Lookalike ML Inference API", version="1.0.0")

# TODO: 실제 깃허브 레포에서 작업하신 모델들을 import 하세요.
# from yolo_module import YOLOCategoryDetector
# from clip_module import FashionCLIPEncoder
# from nlp_module import TagExtractor

# yolo_detector = YOLOCategoryDetector(weight_path="weights/yolov11.pt")
# clip_encoder = FashionCLIPEncoder(model_name="patrickjohncyh/fashion-clip")
# tag_extractor = TagExtractor()

@app.post("/predict_vector")
async def process_search_query(
    image: Optional[UploadFile] = File(None, description="사용자가 업로드한 옷 사진"),
    text: Optional[str] = Form(None, description="사용자가 입력한 검색어 (예: '검은색 패딩')"),
):
    """
    [아키텍처 다이어그램 기반 동작 순서]
    사용자 입력 ➡️ Stage 1(YOLO 카테고리) ➡️ Stage 2(CLIP 임베딩) ➡️ 태그 추출 ➡️ 백엔드 반환
    """
    try:
        # 응답 기본 뼈대
        response_data = {
            "image_vector": None,       # 512차원 배열 (ES 검색용)
            "text_vector": None,        # 384차원 배열 (ES 검색용)
            "gender": None,             # "men" 또는 "women" (DB 필터링용)
            "applied_category": None,   # "top", "bottom", "outer" (DB 필터링용)
            "tags": []                  # ["검은색", "가죽", "겨울"] 형태 (자연어 태그 매칭용)
        }

        # [케이스 A: 이미지가 들어온 경우]
        if image:
            image_bytes = await image.read()
            # img = Image.open(io.BytesIO(image_bytes))
            
            # Stage 1: YOLO 모델로 부위 검출 및 성별/카테고리 분류
            # gender, cat = yolo_detector.predict(img) 
            response_data["gender"] = "men" # (임시 더미)
            response_data["applied_category"] = "outer" # (임시 더미)
            
            # Stage 2: Fashion-CLIP으로 이미지 벡터화 (512차원)
            # response_data["image_vector"] = clip_encoder.encode_image(img) 
            response_data["image_vector"] = [-0.1437, -0.1772, 0.4444] * 170 + [-0.1437, -0.1772]

        # [케이스 B: 텍스트 검색어가 들어온 경우]
        if text:
            # KoNLPy / KeyBERT 등으로 형태소 분석 및 태그 추출
            # response_data["tags"] = tag_extractor.extract(text)
            response_data["tags"] = ["검은색", "가죽", "잠바"] # (임시 더미)
            
            # 텍스트 벡터화 (384차원)
            # response_data["text_vector"] = clip_encoder.encode_text(text)
            response_data["text_vector"] = [0.0123, -0.0456, 0.0789] * 128
            
        return response_data

    except Exception as e:
        logger.error(f"[ML] 처리 중 에러 발생: {str(e)}")
        return {"error": "ML 모델 추론 중 서버 내부 에러가 발생했습니다."}, 500

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8914, reload=True)
```
👉 **수정이 완료되면, 서버 인프라에 이미 세팅된 명령어(`docker-compose up -d ml-engine`)를 통해 백엔드와 완벽하게 연결된 상태로 켜지게 됩니다!**

---

### 🏃 백엔드 팀 (질문자님) To-Do: "내부 주소로 접속해서 데이터 받아오기"
질문자님은 업로드해주신 사진의 드래그된 부분 (100번~107번 줄)을 아래 코드로 **통째로 교체(Replace)** 하시면 됩니다. (성별과 상세 카테고리를 합쳐서 기존 DB 검색에 맞추는 로직이 들어있습니다)

**[기존 코드 (삭제할 부분)]**
```python
# 3. 검색 서비스 (전략 1: ML 검색 결과, 전략 2: 텍스트 검색, 전략 3: DB fallback)
ml_results = await search_products(
    query_text=search_text,
    ml_product_scores=None,   # TODO: ML 서버 연동 후 통신 로직 추가
    category=category,
    limit=6,
)
```

**[새로운 코드 (위 자리에 붙여넣을 부분)]**
```python
# 3-1. ML 서버로 데이터 보내서 결과(Product IDs) 받아오기
import httpx
ml_scores = None
ml_category = None # ML이 추론한 "men_outer" 형태의 문자열

if image:
    # 이미지가 업로드 된 경우에만 ML 서버 통신
    image_file_data = await image.read()
    image.file.seek(0) # 다른 곳에서 다시 읽을 수 있도록 커서를 처음으로 되돌림

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "http://ml-engine:8914/predict_vector", 
                files={"image": image_file_data}
            )
            response.raise_for_status() # 200 OK가 아니면 에러 발생
            ml_data = response.json()
            
            # ML팀이 리턴해준 JSON에서 데이터 추출
            ml_scores = ml_data.get("ml_product_scores") 
            gender = ml_data.get("gender") # 예: "men"
            applied_cat = ml_data.get("applied_category") # 예: "outer"
            
            # 기존 search_products 함수 구조에 맞게 "성별_카테고리" 문자열로 합치기
            if gender and applied_cat:
                ml_category = f"{gender}_{applied_cat}"
                
    except Exception as e:
        logger.error(f"ML 서버 통신 실패: {e}")
        # 오류 발생 시 ml_scores는 None으로 유지되어 자동 DB fallback 모드 작동!

# 3-2. 검색 서비스 (받아온 결과 점수 dict를 넣어 Hydration 실행!)
# 사용자가 프론트엔드에서 직접 카테고리를 선택했다면(category) 우선순위를 두고, 
# 선택하지 않았다면 ML이 추론한 카테고리(ml_category)를 사용합니다.
final_category = category if category else ml_category

ml_results = await search_products(
    query_text=search_text,
    ml_product_scores=ml_scores, # <-- 통신 성공 시 ML결과 ID/점수 dict가 들어가고, 실패/이미지 없으면 None 유지
    category=final_category,     # <-- "men_top", "women_outer" 형태 유지!
    limit=6,
)
```

### 💡 요약
* **물리적 환경**: 1대의 서버, 1개의 Docker Compose
* **ML팀**: 모델을 FastAPI로 감싸서 `ml-engine` 이라는 도커 컨테이너로 띄워주기 (빈 포트인 8914 사용)
* **백엔드팀**: 위 코드처럼 100~107번 줄을 통째로 덮어쓰기 완료하면 끝!
