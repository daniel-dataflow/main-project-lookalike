# 1. Airflow 기본 이미지
FROM apache/airflow:2.10.4-python3.11

# 2. root 권한
USER root

# 3. 시스템 패키지 설치
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    openjdk-17-jdk \
    docker.io \
    curl \
    ca-certificates \
    libglib2.0-0 libnss3 libnspr4 libdbus-1-3 \
    libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxcb1 libxkbcommon0 \
    libatspi2.0-0 libx11-6 \
    libxcomposite1 libxdamage1 libxext6 \
    libxfixes3 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 libasound2 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# docker 그룹 추가
RUN usermod -aG docker airflow

# 4. Java 환경 변수
ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
ENV PATH=$JAVA_HOME/bin:$PATH

# 5. Hadoop 설치
ENV HADOOP_VERSION=3.3.6
ENV HADOOP_HOME=/opt/hadoop

RUN curl -fsSL https://downloads.apache.org/hadoop/common/hadoop-${HADOOP_VERSION}/hadoop-${HADOOP_VERSION}.tar.gz \
    | tar -xz -C /opt/ && \
    mv /opt/hadoop-${HADOOP_VERSION} ${HADOOP_HOME} && \
    chown -R airflow: ${HADOOP_HOME}

ENV PATH=$PATH:${HADOOP_HOME}/bin

# 6. Spark 환경 변수
ENV SPARK_HOME=/opt/spark
ENV PYSPARK_PYTHON=/usr/local/bin/python
ENV PYSPARK_DRIVER_PYTHON=/usr/local/bin/python

# 7. airflow 유저로 전환
USER airflow

# 8. Python 라이브러리 설치
RUN pip install --no-cache-dir \
    apache-airflow-providers-apache-spark==4.10.0 \
    apache-airflow-providers-postgres==5.12.0 \
    psycopg2-binary \
    pyspark==3.5.3 \
    playwright==1.41.2 \
    beautifulsoup4 \
    hdfs

# 9. Playwright 브라우저 설치
USER root
RUN playwright install chromium
USER airflow