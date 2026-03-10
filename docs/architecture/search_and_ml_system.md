# 검색 및 머신러닝(ML) 추론 시스템 아키텍처 명세서

> **문서 목적**: 데이터 파이프라인 및 실시간 웹 검색망에 연동되는 YOLO 기반 객체 탐지(Pre-Cropping) 모델, 시각/언어 벡터 임베딩 추출망, 그리고 이를 활용한 하이브리드 검색 아키텍처(kNN Vector Search 기반)의 통합 시스템 구조를 명세 서술함. 작성 톤 앤 매너는 관리자 열람용 공식 매뉴얼 형태를 엄수함.

---

## 1. 하이브리드 검색 및 ML 추론 시스템 아키텍처 개요

본 시스템은 크게 머신러닝 연산 전담 데몬(ML-API)과 웹 백엔드의 하이브리드 3단계 검색 라우팅 엔진으로 분리 결합 구동됨. 사용자가 업로드한 이미지는 반드시 YOLO 전처리 과정을 거쳐 노이즈가 제거된 상태로만 벡터화되며, 가장 강력하고 정확한 Elasticsearch kNN 검색 엔진의 실탄으로 사용됨.

```mermaid
flowchart TD
    subgraph 1. Model Storage (ML 모델 런타임 저장소)
        YOLO[(YOLOv8 Weights\nbest.pt)]
        CNN[(Embedding Model\nResNet/CLIP)]
        VLM[(Vision-Language\nModel)]
    end

    subgraph 2. ML Inference Engine (실시간/배치 추론망)
        UserUpload([웹 사용자 업로드 이미지]) --> API_ML[FastAPI ML Router\n/api/search/by-image]
        API_ML --> YOLO_S[1. YOLO Pre-Cropping\n객체 탐지 전처리]
        YOLO_S --> YOLO
        YOLO_S --> CNN_S[2. Vector Embedding\n고차원 특징 추출기]
        CNN_S --> CNN
        CNN_S --> ML_Result[응답 텐서 벡터\n예: 0.42, -0.11, ...]
    end

    subgraph 3. Hybrid Search Routing (웹 백엔드 3단계 검색망)
        ML_Result ==> Router{검색 전략 라우팅 엔진\nsearch_service.py}
        
        Router ==>|벡터(임베딩) 텐서값 존재 시\n최상위 유사도 매칭 수행| S1[전략 1단계. ES kNN 밀집 벡터 검색\n의류 이미지 정밀 매칭]
        Router ==>|벡터 변환 실패 및 텍스트만 존재 시| S2[전략 2단계. ES 텍스트 풀 스캔 검색\n형태소 키워드 매칭]
        Router ==>|ES 인프라 다운폴 시 폴백 안전망| S3[전략 3단계 안전망. 메인 DB Fallback\nPostgreSQL 속성 필터링]
        
        S1 & S2 --> ES[(Elasticsearch 클러스터\nproducts 인덱스)]
        S3 --> DB[(PostgreSQL 코어 DB\nproducts 물리 테이블)]
        
        ES & DB --> Result[최종 검색 결과셋 취합 정렬]
        Result --> User([검색 결과 사용자 응답])
    end
```

---

## 2. 핵심 ML 컴포넌트 명세 및 전처리 프레이어 (Pre-Cropping)

### 2.1 실시간 ML 서빙 API (`ml-models/api/main.py`)
머신러닝 팀이 독자적으로 컨테이너화하여 데몬 기동하는 추론 전용 FastAPI 워커 노드망.
- **주요 라우터**: `yolo_router.py` 및 `search_logic.py`로 모듈화 설계됨.
- **포커싱**: 웹 라우터와의 통신 결합성을 낮추기 위해, 철저히 인풋(이미지 버퍼) 대비 아웃풋(수학 배열 좌표) 연산만을 전담하는 비연결성(Stateless) 아키텍처를 지향함.

### 2.2 [핵심] YOLO 객체 판독 전처리 추론기 (`yolo_service.py`)
- **엔진 모듈**: `Ultralytics` 라이브러리 엔진 및 `best.pt` 파인튜닝 의류 전용 가중치 체크포인트.
- **태스크 목적 (Pre-Cropping)**: 무작위 환경거리(Street)에서 찍힌 사진 전체를 그대로 임베딩하지 않음. **YOLO 모듈을 통해 불필요한 노이즈 배경 픽셀을 도려내고, 핵심 타겟팅된 옷 객체(BBox)만 타이트하게 크롭(Crop) 전처리 추출 연산함.** 이 'Pre-Cropping' 단계를 거쳐야만 이어지는 임베딩 벡터의 품질 노이즈가 기하급수적으로 소거되어 kNN 검색의 본질적 정확도가 100% 보장 확정됨.

### 2.3 고차원 임베딩 생성기 (`search_logic.py`)
- **엔진 모듈**: PyTorch 기반 텐서 딥러닝 망.
- **태스크 역할**: 앞선 YOLO 통과 망으로 컷아웃(크롭)된 순수 의류 픽셀 박스만을 인입받아, 고차원 부동소수점 배열(예: 512차원 `[0.124, 0.053, -0.422, ...]`) 포맷으로 직렬 변환 압축 생성함.

---

## 3. 웹 백엔드 3단계 하이브리드 검색망 전략 패턴

웹 백엔드 라우터(search_service.py)는 에러 내성 방어 및 검색 퀄리티 사수를 위해 3단계 Fallback 체제를 가동함.

### 3.1 1단계 핵심망: Elasticsearch kNN 벡터 검색 (본 시스템의 최종 타겟)
- **트리거 발동 조건**: 브라우저 사용자의 이미지가 ML 파이프라인(YOLO+CNN)을 관통하여 **밀집 임베딩 벡터값** 배열로 무사 변환 컴파일되어(`query_embedding` 객체 활성) 파라미터로 안착되었을 때 무조건 0순위 발동.
- **내부 동작 방식**: Elasticsearch 스토리지 내 `dense_vector` 필드에 적재된 수백만 건의 상품 임베딩 중, 사용자 인입 벡터 좌표와 "가장 구조적 수학 거리가 좁은(유사 추론 스코어가 극도로 높은)" 최상단 의류 노드를 k-Nearest Neighbors 알고리즘이 탐색 적중 도출함.

### 3.2 2단계 서브망: Elasticsearch 텍스트 쿼리 검색 (텍스트 매칭 풀)
- **트리거 발동 조건**: 이미지 벡터 텐서 객체가 미존재하거나, ML 변환 연산이 실패 타임아웃 났지만 **텍스트 평문 검색어(`query_text`)**가 존재할 경우 차순위로 발동.
- **내부 동작 방식**: 상품 타이틀, 브랜드, 그리고 **VLM(비전 언어 모델)이 사전 분석 적재해 둔 옷 형태 생김새 묘사 설명글(`detected_desc`)** 볼륨 안에서 루씬 `multi_match` 형태소 키워드 쿼리 엔진을 가동하여 텍스트 정확도 매칭을 고속 스캔함.

### 3.3 3단계 예비망: Main DB Fallback 폴백 전략 (최후 무중단 생존망)
- **트리거 발동 조건**: 검색어 덤프도 전무하거나, ES 컨테이너 노드가 치명적으로 뻗어 시스템 인프라 핑 다운 마비 장애가 발발 포착되었을 때 발동됨.
- **내부 동작 방식**: HTTP 500 마비 에러 화면 노출 사태를 원천 봉쇄 대처하기 위해, 기본 RDB(PostgreSQL) 볼륨 레이어에서 성별과 카테고리만으로 억제 필터링하여 난수(랜덤) 연산 반환함으로써 방어 UI 렌더링 무마 생존을 영위함.

---

## 4. 실시간 웹 인퍼런스(Inference) 연동 API 파이프라인 통신 룰

실시간 검색 런타임 구동을 위한 백엔드 간 통신 규약 시퀀스 릴레이:

1. **`웹 백엔드` ➡ `ML 서버` (HTTP POST 핑 타전)**: 
   - 사용자의 이미지 파일 이진법 버퍼를 `multipart/form-data` 포맷으로 포워딩 전송. (예: `POST http://ml-server:8000/embed`)
2. **`ML 서버 파이프라인 내부 쾌속 연산`**: 
   - YOLO 크롭 연산 $\rightarrow$ 특징 임베딩 벡터 추출 연산의 2단계 록킹 체인을 고속 컴파일 통과.
3. **`ML 서버` ➡ `웹 백엔드` (가중치 JSON 응답 반환)**: 
   - 추출 생성된 실수형 벡터 1차원 숫자 배열 객체를 JSON 포맷 페이로드로 응답 리턴.
4. **`웹 백엔드 검색 라우터 컴파일` ➡ `Elasticsearch 타격`**: 
   - 획득 전달받은 살아있는 임베딩 실 벡터를 `search.py` 라우터의 `query_embedding` 덤프에 스와핑 캡슐 장착하고, 즉각 1단계 ES kNN 유사도 매칭 스캐닝 함수로 트리거 발포 지시 연계.

---

### 작성자 : 한대성
> 최종 업데이트: 2026-03-07
