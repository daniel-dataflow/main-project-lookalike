#!/bin/bash
set -e

# Change to the project root directory
cd "$(dirname "$0")/../.."

echo "======================================================"
echo " 🏗️  [Lookalike] 통합 데이터 파이프라인 마스터 자동 재구축"
echo "======================================================"

echo "[1/4] 하둡 HDFS에 로컬 이미지 업로드..."
# 임시 폴더에서 브랜드를 분류하여 디렉토리 구조를 생성 후 단일 명령으로 분배
mkdir -p /tmp/upload_images_grouped
for img in data-pipeline/elasticsearch/data/image/*.jpg; do
    [ -f "$img" ] || continue
    filename=$(basename "$img")
    brand=$(echo "$filename" | cut -d"_" -f1 | tr '[:upper:]' '[:lower:]')
    mkdir -p "/tmp/upload_images_grouped/$brand/image"
    cp "$img" "/tmp/upload_images_grouped/$brand/image/"
done

docker cp /tmp/upload_images_grouped namenode-main:/tmp/
docker exec namenode-main bash -c '
hdfs dfs -mkdir -p /raw
hdfs dfs -put -f /tmp/upload_images_grouped/* /raw/
rm -rf /tmp/upload_images_grouped
echo ">> HDFS 업로드 완료"
'
rm -rf /tmp/upload_images_grouped

echo "[2/4] PostgreSQL 데이터베이스 초기화 및 CSV 임포트..."
python3 scripts/test_DB/import_products_csv.py

echo "[3/4] 네이버 가격 수집 스크립트 실행..."
bash scripts/test_DB/update_naver_prices.sh

echo "[4/4] Elasticsearch에 임베딩 JSON 복합 업로드..."
# fastapi 컨테이너 내부 환경을 사용해 ES 접속
docker compose exec -T fastapi python3 data-pipeline/elasticsearch/config/es_upload_json.py \
    --image-input data-pipeline/elasticsearch/data/image_embed_json/ \
    --text-input data-pipeline/elasticsearch/data/text_embed_json/ \
    --merge-mode inner \
    --derive-id-from-filename \
    --es http://elasticsearch:9200 \
    --refresh

echo "======================================================"
echo " 🎉 모든 파이프라인 재생성 배치가 성공적으로 완료되었습니다! "
echo "======================================================"
