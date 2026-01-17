# main-project-snap-match
AI 패션 이미지 검색 기반 듀프 쇼핑 최저가 비교 서비스 | YOLO + CLIP 활용

snap-match/
│
├── README.md
├── .gitignore
├── .env.example
├── docker-compose.data.yml        # DE 전담
├── docker-compose.ml.yml          # DS 전담
├── docker-compose.web.yml         # 공통
├── docker-compose.override.yml    # 로컬 개발용 (Git 제외)
│
├── docs/                          # 📚 문서
│   ├── README.md
│   ├── SETUP.md
│   ├── API_SPEC.md
│   ├── DB_SCHEMA.md
│   ├── MODEL_GUIDE.md
│   └── DEPLOYMENT.md
│
├── scripts/                       # 🛠️ 공통 스크립트
│   ├── setup_server.sh
│   ├── start_all.sh               # 전체 서비스 시작
│   ├── stop_all.sh
│   ├── monitor_resources.sh
│   └── backup_all.sh
│
├── config/                        # ⚙️ 공통 설정
│   ├── .env.data                  # DE 전용 환경변수
│   ├── .env.ml                    # DS 전용 환경변수
│   ├── .env.web                   # 웹 환경변수
│   └── nginx/
│       └── nginx.conf
│
│
├── data-pipeline/                 # 🔧 DE 전담 영역 (DE1 + DE2)
│   │
│   ├── README.md                  # DE 가이드
│   ├── requirements.txt           # DE 전용 패키지
│   │
│   ├── airflow/                   # DE1: 스케줄링
│   │   ├── dags/
│   │   │   ├── __init__.py
│   │   │   ├── daily_pipeline.py
│   │   │   ├── crawling/
│   │   │   │   ├── uniqlo_dag.py
│   │   │   │   ├── zara_dag.py
│   │   │   │   ├── hm_dag.py
│   │   │   │   ├── topten_dag.py
│   │   │   │   └── eightseconds_dag.py
│   │   │   ├── naver_api_dag.py
│   │   │   └── embedding_dag.py
│   │   ├── plugins/
│   │   │   └── custom_operators.py
│   │   ├── config/
│   │   │   ├── airflow.cfg
│   │   │   └── pools.py
│   │   └── logs/                  # (Git 제외)
│   │
│   ├── crawlers/                  # DE1: 크롤링
│   │   ├── scrapy.cfg
│   │   ├── spiders/
│   │   │   ├── __init__.py
│   │   │   ├── base_spider.py
│   │   │   ├── uniqlo_spider.py
│   │   │   ├── zara_spider.py
│   │   │   ├── hm_spider.py
│   │   │   ├── topten_spider.py
│   │   │   └── eightseconds_spider.py
│   │   ├── items.py
│   │   ├── pipelines.py
│   │   ├── middlewares.py
│   │   ├── settings.py
│   │   ├── ocr/
│   │   │   ├── tesseract_ocr.py   # 탑텐 이미지 → 텍스트
│   │   │   └── text_cleaner.py
│   │   └── validators.py
│   │
│   ├── kafka/                     # DE1: 스트리밍
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── producers/
│   │   │   ├── crawl_producer.py
│   │   │   └── user_action_producer.py
│   │   └── consumers/
│   │       ├── crawl_consumer.py
│   │       └── log_consumer.py
│   │
│   ├── spark/                     # DE2: 배치 처리
│   │   ├── __init__.py
│   │   ├── jobs/
│   │   │   ├── preprocess_crawled.py
│   │   │   ├── merge_api_data.py
│   │   │   ├── feature_engineering.py
│   │   │   ├── price_aggregation.py
│   │   │   └── embedding_batch.py
│   │   ├── configs/
│   │   │   ├── spark_config.py
│   │   │   └── spark-defaults.conf
│   │   └── utils/
│   │       ├── mongo_connector.py
│   │       └── postgres_connector.py
│   │
│   ├── api/                       # DE2: 외부 API
│   │   ├── __init__.py
│   │   ├── naver_shopping.py
│   │   ├── api_cache.py
│   │   ├── rate_limiter.py
│   │   └── retry_handler.py
│   │
│   ├── database/                  # DE2: DB 스키마
│   │   ├── migrations/
│   │   │   ├── postgresql/
│   │   │   │   ├── V001__create_products.sql
│   │   │   │   ├── V002__create_embeddings.sql
│   │   │   │   ├── V003__add_pgvector.sql
│   │   │   │   ├── V004__create_price_history.sql
│   │   │   │   └── V005__create_search_logs.sql
│   │   │   └── mysql/
│   │   │       ├── V001__create_users.sql
│   │   │       ├── V002__create_search_history.sql
│   │   │       └── V003__create_saved_searches.sql
│   │   ├── seeds/
│   │   │   ├── seed_brands.sql
│   │   │   └── seed_categories.sql
│   │   └── scripts/
│   │       ├── run_migrations.py
│   │       ├── backup_postgres.sh
│   │       ├── backup_mysql.sh
│   │       └── backup_mongo.sh
│   │
│   ├── elasticsearch/             # DE2: 검색 엔진
│   │   ├── __init__.py
│   │   ├── indexing/
│   │   │   ├── create_index.py
│   │   │   └── bulk_indexing.py
│   │   ├── queries/
│   │   │   ├── product_search.py
│   │   │   └── tag_search.py
│   │   └── config/
│   │       └── elasticsearch.yml
│   │
│   └── tests/                     # DE 테스트
│       ├── test_crawlers.py
│       ├── test_spark_jobs.py
│       └── test_api.py
│
│
├── ml-models/                     # 🤖 DS 전담 영역 (DS1 + DS2)
│   │
│   ├── README.md                  # DS 가이드
│   ├── requirements.txt           # DS 전용 패키지
│   │
│   ├── yolo/                      # DS1: 객체 탐지
│   │   ├── __init__.py
│   │   ├── train/
│   │   │   ├── train_yolo.py
│   │   │   ├── yolo_config.yaml
│   │   │   └── data_loader.py
│   │   ├── inference/
│   │   │   ├── detect_clothing.py
│   │   │   └── batch_inference.py
│   │   ├── models/
│   │   │   └── best.pt            # (Git 제외)
│   │   ├── evaluation/
│   │   │   ├── eval_yolo.py
│   │   │   └── metrics.py
│   │   └── utils/
│   │       ├── preprocessing.py
│   │       └── visualization.py
│   │
│   ├── clip/                      # DS1: 이미지-텍스트 매칭
│   │   ├── __init__.py
│   │   ├── train/
│   │   │   ├── finetune_clip.py
│   │   │   ├── fashion_clip_config.yaml
│   │   │   └── dataset.py
│   │   ├── inference/
│   │   │   ├── embedding_generator.py
│   │   │   ├── similarity_search.py
│   │   │   └── batch_embedding.py
│   │   ├── models/
│   │   │   └── fashion_clip.pt    # (Git 제외)
│   │   ├── evaluation/
│   │   │   ├── eval_clip.py
│   │   │   └── retrieval_metrics.py
│   │   └── utils/
│   │       ├── text_encoder.py
│   │       └── image_encoder.py
│   │
│   ├── dfine/                     # DS1: D-Fine (선택)
│   │   ├── train/
│   │   ├── inference/
│   │   └── config.yaml
│   │
│   ├── nlp/                       # DS2: 자연어 처리
│   │   ├── __init__.py
│   │   ├── keybert/
│   │   │   ├── keyword_extractor.py
│   │   │   └── tag_generator.py
│   │   ├── konlpy/
│   │   │   ├── morpheme_parser.py
│   │   │   └── query_parser.py
│   │   ├── ner/                   # Phase 2
│   │   │   ├── train_ner.py
│   │   │   └── extract_entities.py
│   │   ├── dictionaries/
│   │   │   ├── synonym_dict.json
│   │   │   └── stopwords.txt
│   │   └── utils/
│   │       └── text_cleaner.py
│   │
│   ├── embeddings/                # DS1 + DS2: 임베딩 생성
│   │   ├── __init__.py
│   │   ├── image_embeddings.py    # DS1: CLIP 사용
│   │   ├── text_embeddings.py     # DS2: NLP 사용
│   │   ├── batch_processor.py
│   │   └── pgvector_uploader.py   # PostgreSQL 적재
│   │
│   ├── datasets/                  # 공통: 데이터셋 관리
│   │   ├── __init__.py
│   │   ├── deepfashion/           # (Git 제외)
│   │   ├── kfashion/              # (Git 제외)
│   │   ├── fashion_dataset.py
│   │   ├── data_loader.py
│   │   └── augmentation.py
│   │
│   ├── notebooks/                 # Jupyter 실험
│   │   ├── EDA.ipynb
│   │   ├── model_comparison.ipynb
│   │   └── embedding_visualization.ipynb
│   │
│   └── tests/                     # DS 테스트
│       ├── test_yolo.py
│       ├── test_clip.py
│       └── test_nlp.py
│
│
├── web-service/                   # 🌐 공통 작업 영역 (DE + DS)
│   │
│   ├── README.md
│   │
│   ├── backend/                   # 백엔드
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── app/
│   │   │   ├── __init__.py
│   │   │   ├── main.py            # FastAPI 엔트리포인트
│   │   │   ├── config.py
│   │   │   ├── database.py
│   │   │   ├── dependencies.py
│   │   │   │
│   │   │   ├── models/            # SQLAlchemy 모델
│   │   │   │   ├── __init__.py
│   │   │   │   ├── product.py
│   │   │   │   ├── user.py
│   │   │   │   ├── search.py
│   │   │   │   └── embedding.py
│   │   │   │
│   │   │   ├── schemas/           # Pydantic 스키마
│   │   │   │   ├── __init__.py
│   │   │   │   ├── product_schema.py
│   │   │   │   ├── user_schema.py
│   │   │   │   ├── search_schema.py
│   │   │   │   └── response_schema.py
│   │   │   │
│   │   │   ├── api/               # API 라우터
│   │   │   │   ├── __init__.py
│   │   │   │   └── v1/
│   │   │   │       ├── __init__.py
│   │   │   │       ├── search.py           # POST /api/v1/search (이미지)
│   │   │   │       ├── nlp_search.py       # POST /api/v1/nlp-search (텍스트)
│   │   │   │       ├── products.py         # GET /api/v1/products
│   │   │   │       ├── users.py            # 인증
│   │   │   │       └── history.py          # 검색 히스토리
│   │   │   │
│   │   │   ├── services/          # 비즈니스 로직
│   │   │   │   ├── __init__.py
│   │   │   │   ├── image_search_service.py     # DS1 모델 호출
│   │   │   │   ├── nlp_search_service.py       # DS2 모델 호출
│   │   │   │   ├── embedding_service.py        # PGVector 검색
│   │   │   │   ├── price_service.py            # 최저가 계산
│   │   │   │   └── user_service.py
│   │   │   │
│   │   │   ├── core/              # 핵심 유틸
│   │   │   │   ├── __init__.py
│   │   │   │   ├── security.py
│   │   │   │   ├── storage.py     # 이미지 저장
│   │   │   │   ├── cache.py       # Redis
│   │   │   │   └── logger.py
│   │   │   │
│   │   │   └── middleware/
│   │   │       ├── __init__.py
│   │   │       ├── logging.py
│   │   │       ├── cors.py
│   │   │       └── error_handler.py
│   │   │
│   │   └── tests/
│   │       ├── test_api.py
│   │       ├── test_search.py
│   │       └── test_auth.py
│   │
│   └── frontend/                  # 프론트엔드
│       ├── index.html
│       ├── search.html
│       ├── mypage.html
│       ├── login.html
│       ├── signup.html
│       │
│       ├── css/
│       │   ├── main.css
│       │   ├── search.css
│       │   ├── components.css
│       │   └── bootstrap-custom.css
│       │
│       ├── js/
│       │   ├── main.js
│       │   ├── config.js
│       │   ├── api.js             # API 호출 래퍼
│       │   ├── auth.js
│       │   ├── image-uploader.js
│       │   ├── nlp-search.js
│       │   ├── search-handler.js
│       │   ├── filter.js
│       │   └── utils.js
│       │
│       ├── assets/
│       │   ├── images/
│       │   │   ├── logo.png
│       │   │   └── placeholder.jpg
│       │   └── icons/
│       │
│       └── components/            # 재사용 컴포넌트
│           ├── product-card.html
│           ├── filter-panel.html
│           └── loading-spinner.html
│
│
├── shared/                        # 🔗 공통 유틸리티 (모두 사용)
│   ├── __init__.py
│   ├── constants.py               # 상수 (카테고리, 브랜드 등)
│   ├── logging_config.py
│   └── utils.py
│
├── monitoring/                    # 📊 모니터링 (선택)
│   ├── prometheus/
│   │   └── prometheus.yml
│   └── grafana/
│       └── dashboards/
│
├── tests/                         # 🧪 통합 테스트
│   ├── integration/
│   │   ├── test_full_pipeline.py
│   │   └── test_search_flow.py
│   └── fixtures/
│       └── sample_images/
│
└── .github/                       # 🤖 CI/CD (선택)
    └── workflows/
        ├── test_data_pipeline.yml
        ├── test_ml_models.yml
        └── test_web_service.yml