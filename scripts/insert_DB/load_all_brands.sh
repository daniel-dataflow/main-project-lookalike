#!/bin/bash
set -e

# Change to the project root directory
cd "$(dirname "$0")/../.."

echo "======================================================"
echo " 🏗️  [Lookalike] 전체 브랜드 데이터 로드 통합 스크립트"
echo "======================================================"

# 호스트 머신에서 실행 시 DB 컨테이너 도메인 해석 불가 방지
export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT=5432
export MONGODB_HOST=127.0.0.1
export MONGODB_PORT=27017

BRANDS=("8seconds" "topten" "uniqlo" "spao" "zara")

echo "[INFO] 필요 패키지(psycopg2-binary, pymongo, python-dotenv) 설치 여부 확인 및 설치 중..."
python3 -m pip install -q psycopg2-binary pymongo python-dotenv

# 1. 초기화 (PostgreSQL Truncate)
echo "[INFO] PostgreSQL 기존 테이블(products 등)을 초기화합니다..."
python3 scripts/insert_DB/import_brand_csv.py --init

for BRAND in "${BRANDS[@]}"; do
    TARGET_DIR="data-pipeline/database/data/$BRAND"
    
    if [ ! -d "$TARGET_DIR" ]; then
        echo "======================================================"
        echo "⏳ [$BRAND] 데이터 폴더($TARGET_DIR)가 존재하지 않아 건너뜁니다."
        echo "======================================================"
        continue
    fi

    echo "======================================================"
    echo " 🚀 [$BRAND] 데이터 로드 시작"
    echo "======================================================"

    # HDFS 이미지 전송 (hadoop/raw/ 아래 구조 가정)
    HDFS_SRC="$TARGET_DIR/hadoop/raw/$BRAND"
    if [ -d "$HDFS_SRC" ]; then
        echo "[$BRAND] 하둡 HDFS에 로컬 이미지 데이터 업로드..."
        
        # 이전 데이터 지우기 (원한다면)
        docker exec namenode-main hdfs dfs -rm -r -f /raw/$BRAND || true
        docker exec namenode-main hdfs dfs -mkdir -p /raw
        
        docker cp "$HDFS_SRC" namenode-main:/tmp/
        docker exec namenode-main bash -c "set -e; hdfs dfs -put -f /tmp/$BRAND /raw/; rm -rf /tmp/$BRAND; echo '>> HDFS 업로드 완료'"
    else
        echo "[$BRAND] ℹ️  HDFS 폴더($HDFS_SRC) 없음. 스킵."
    fi

    # PostgreSQL CSV Import
    echo "[$BRAND] PostgreSQL csv 임포트..."
    python3 scripts/insert_DB/import_brand_csv.py "$BRAND"

    # MongoDB Import
    echo "[$BRAND] MongoDB json 임포트..."
    python3 scripts/insert_DB/import_mongo_json.py "$BRAND"

    # Elasticsearch Bulk Upload
    # JSON 파일들이 $TARGET_DIR/mongo/analyzed_metadata/ 에 위치
    ES_INPUT="$TARGET_DIR/mongo/analyzed_metadata"
    if [ -d "$ES_INPUT" ]; then
        echo "[$BRAND] Elasticsearch JSON 업로드..."
        docker compose exec -T fastapi python3 data-pipeline/elasticsearch/config/es_upload_json.py \
            --input "$ES_INPUT" \
            --id-field "product_id" \
            --derive-id-from-filename \
            --es http://elasticsearch:9200 \
            --refresh
    else
        echo "[$BRAND] ℹ️  ES 메타데이터 폴더($ES_INPUT) 없음. 스킵."
    fi

    echo " 🎉 [$BRAND] 처리 완료!"
done

echo "======================================================"
echo " 🌐 [통합] 네이버 쇼핑 스크래퍼(API) 업데이트 시작"
echo "======================================================"
# 모든 기본 상품 삽입 후, 네이버 API 연동 스크립트 실행
bash scripts/insert_DB/update_naver_prices.sh

echo "======================================================"
echo " 🏁 모든 브랜드 배치가 성공적으로 완료되었습니다! "
echo "======================================================"
