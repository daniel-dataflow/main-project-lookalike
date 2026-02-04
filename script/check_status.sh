#!/bin/bash

echo "=========================================="
echo "Main Project Lookalike - 상태 확인"
echo "=========================================="

echo ""
echo "=== Docker 컨테이너 ==="
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

echo ""
echo "=== Python 프로세스 ==="
ps aux | grep -E "uvicorn|airflow" | grep -v grep

echo ""
echo "=== 포트 사용 현황 ==="
sudo netstat -tlnp | grep -E "8900|8902|8903|8904|8905|5432|3306|27017|6379"

echo ""
echo "=== 메모리 사용량 ==="
free -h

echo ""
echo "=== 디스크 사용량 ==="
df -h | grep -E "Filesystem|/dev/|overlay"

echo ""
echo "=== 서비스 접속 테스트 ==="
echo -n "FastAPI (8900): "
curl -s http://localhost:8900/health > /dev/null && echo "OK" || echo "FAIL"

echo -n "Airflow Webserver (8902): "
curl -s http://localhost:8902/health > /dev/null && echo "OK" || echo "FAIL"

echo -n "Elasticsearch (8903): "
curl -s http://localhost:8903 > /dev/null && echo "OK" || echo "FAIL"

echo -n "Spark Master (8904): "
curl -s http://localhost:8904 > /dev/null && echo "OK" || echo "FAIL"

