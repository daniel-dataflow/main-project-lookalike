# Airflow 기본 이미지 사용
FROM apache/airflow:2.10.4-python3.11

# 루트 권한으로 시스템 패키지 설치
USER root

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    openjdk-17-jdk \
    libglib2.0-0 libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxcb1 libxkbcommon0 libatspi2.0-0 libx11-6 \
    libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 libasound2 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Java 환경변수
ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
ENV PATH=$JAVA_HOME/bin:$PATH

# airflow 유저로 변경
USER airflow

# python 라이브러리 설치
RUN pip install --no-cache-dir \
    apache-airflow-providers-apache-spark==4.10.0 \
    apache-airflow-providers-postgres==5.12.0 \
    psycopg2-binary \
    pyspark==3.5.3 \
    playwright==1.41.2 \
    pytest-playwright \
    beautifulsoup4 \
    hdfs \
    pytz \
    pymongo \
    ollama \
    deep_translator \
    tqdm \
    Pillow \
    sentence-transformers \
    elasticsearch

# Playwright 브라우저 설치
RUN playwright install chromium