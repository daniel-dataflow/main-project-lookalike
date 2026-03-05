FROM python:3.11-slim-bookworm

RUN apt-get update && \
    apt-get install -y openjdk-17-jdk-headless curl procps && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

ENV SPARK_VERSION=3.5.3
ENV HADOOP_VERSION=3

RUN curl -fsSL https://archive.apache.org/dist/spark/spark-${SPARK_VERSION}/spark-${SPARK_VERSION}-bin-hadoop${HADOOP_VERSION}.tgz \
    | tar -xz -C /opt && \
    mv /opt/spark-${SPARK_VERSION}-bin-hadoop${HADOOP_VERSION} /opt/spark

# 🔥 spark 유저 생성
RUN useradd -m -u 1000 -s /bin/bash spark

ENV SPARK_HOME=/opt/spark
ENV PATH=$SPARK_HOME/bin:$PATH

ENV PYSPARK_PYTHON=/usr/local/bin/python
ENV PYSPARK_DRIVER_PYTHON=/usr/local/bin/python

# 🔥 권한 수정
RUN mkdir -p /opt/spark/work && \
    chown -R spark:spark /opt/spark

USER spark
WORKDIR /opt/spark