FROM apache/airflow:3.0.3-python3.11

USER root

# Java 설치 (Spark용)
RUN apt-get update && \
    apt-get install -y openjdk-17-jdk && \
    apt-get clean

# Playwright 시스템 의존성 설치
RUN apt-get update && \
    apt-get install -y wget curl gnupg && \
    apt-get clean

USER airflow

# Python 패키지 설치
RUN pip install --no-cache-dir playwright pyspark

# Playwright 브라우저 설치
RUN playwright install --with-deps
