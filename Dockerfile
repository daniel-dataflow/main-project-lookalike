# Airflow 기본 이미지 사용
FROM apache/airflow:2.10.4-python3.11

# ==========================================
# 1. 루트 권한: 시스템 패키지 설치
# ==========================================
USER root

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    openjdk-17-jdk \
    build-essential \
    libgl1 \
    libglib2.0-0 libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxcb1 libxkbcommon0 libatspi2.0-0 libx11-6 \
    libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 libasound2 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Java 환경변수 설정
ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
ENV PATH=$JAVA_HOME/bin:$PATH

# ==========================================
# 2. Airflow 유저 권한: 폴더 생성 및 파이썬 패키지 설치
# ==========================================
USER airflow

# 작업 및 데이터 폴더 미리 생성
RUN mkdir -p \
    /opt/airflow/model \
    /opt/airflow/data/incoming \
    /opt/airflow/data/crops \
    /opt/airflow/data/embeddings

# 2-1. Airflow Provider 및 데이터베이스 관련 패키지 (여기는 제약조건 유지)
RUN python -m pip install --no-cache-dir \
    --constraint https://raw.githubusercontent.com/apache/airflow/constraints-2.10.4/constraints-3.11.txt \
    apache-airflow-providers-postgres==5.14.0 \
    apache-airflow-providers-apache-spark==4.11.3 \
    psycopg2-binary \
    pyspark==3.5.3

# 2-2. 크롤링, NLP 및 기타 유틸리티 라이브러리 
# 🚨 해결: --constraint 옵션을 삭제하여 충돌을 방지합니다.
RUN python -m pip install --no-cache-dir \
    playwright==1.41.2 \
    pytest-playwright \
    beautifulsoup4 \
    hdfs \
    pytz \
    pymongo \
    ollama \
    deep_translator \
    tqdm \
    sentence-transformers \
    elasticsearch \
    appdirs \
    validators \
    datasets \
    ipyplot

# Playwright 브라우저(Chromium) 설치
RUN playwright install chromium

# 2-3. ML / Runtime 의존성 고정 (YOLO, Torch 등)
RUN python -m pip install --no-cache-dir --force-reinstall \
    numpy==1.26.4 \
    packaging==24.1 \
    fsspec==2024.10.0 \
    opencv-python-headless==4.10.0.84 \
    pillow==12.1.1 \
    torch==2.10.0 \
    torchvision==0.25.0 \
    matplotlib==3.10.8 \
    scipy==1.17.0 \
    seaborn==0.13.2 \
    #ultralytics-thop==2.0.18 \
    transformers==4.46.3 \
    huggingface-hub==0.26.2 \
    tokenizers==0.20.3 \
    annoy==1.17.3 \
    requests

# 2-4. 의존성 자동 업그레이드 방지 패키지
RUN python -m pip install --no-cache-dir --no-deps \
    ultralytics==8.3.0 \
    fashion-clip==0.2.2
