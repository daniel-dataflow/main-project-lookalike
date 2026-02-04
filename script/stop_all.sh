#!/bin/bash

echo "=========================================="
echo "Main Project Lookalike - 전체 서비스 중지"
echo "=========================================="

echo ""
cd ~/main-project-lookalike/
echo "=== 안전 종료 프로세스를 시작합니다 ==="

# 1. 상위 애플리케이션 종료
echo "1. FastAPI 및 Airflow 프로세스 종료..."
docker compose stop airflow-webserver airflow-scheduler fastapi

# 2. 분석 및 메시지 큐 종료
echo "2. Spark 및 Kafka 종료..."
docker compose stop spark-worker-1 spark-master kafka

# 3. Hadoop 클러스터 안전 종료 (데이터 유실 방지)
echo "3. Hadoop 노드 종료..."
docker compose stop nodemanager resourcemanager datanode namenode

# 4. 나머지 기반 인프라 종료
echo "4. DB 및 모니터링 스택 종료..."
docker compose stop zookeeper elasticsearch kibana filebeat postgresql mongodb redis

# 5. 모든 컨테이너 완전 제거 및 네트워크 정리
echo "5. 모든 컨테이너 리소스 정리..."
docker compose down

echo ""
echo "=========================================="
echo "모든 서비스 중지 완료!"
echo "=========================================="

