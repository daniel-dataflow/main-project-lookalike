#!/bin/bash
set -e

# Change to the project root directory (scripts/test_DB/8seconds/ -> ../../..)
cd "$(dirname "$0")/../../.."

echo "======================================================"
echo " 🏗️  [Lookalike] 8seconds 데이터 로드"
echo "======================================================"

echo "[1/3] 하둡 HDFS에 로컬 이미지 데이터 업로드..."
# 전에 있던 데이터 모두 지우기
docker exec namenode-main hdfs dfs -rm -r -f /raw
docker exec namenode-main hdfs dfs -mkdir -p /raw

# 8seconds 데이터 구조 업로드 (와일드카드 문제 회피)
docker cp data-pipeline/elasticsearch/data/8seconds/hadoop/raw/8seconds namenode-main:/tmp/
docker exec namenode-main bash -c 'set -e
hdfs dfs -put -f /tmp/8seconds /raw/
rm -rf /tmp/8seconds
echo ">> HDFS 8seconds 데이터 업로드 완료"
'

echo "[2/3] PostgreSQL 데이터 임포트..."
python3 scripts/test_DB/8seconds/import_8seconds_csv.py

echo "[3/3] Elasticsearch에 임베딩 JSON 업로드..."
# fastapi 컨테이너 내부 환경을 사용해 ES 접속
# 8seconds JSON은 image/text 통합 모델 형태이므로 --input 디렉토리 하나만 지정
docker compose exec -T fastapi python3 data-pipeline/elasticsearch/config/es_upload_json.py \
    --input "data-pipeline/elasticsearch/data/8seconds/datadb.analyzed_metadata.json" \
    --derive-id-from-filename \
    --es http://elasticsearch:9200 \
    --refresh

echo "======================================================"
echo " 🎉 8seconds 데이터 배치가 성공적으로 완료되었습니다! "
echo "======================================================"
