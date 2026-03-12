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

# 동적 브랜드 목록 탐색
BRANDS=()
if [ -d "data-pipeline/database/data" ]; then
    for BRAND_DIR in data-pipeline/database/data/*; do
        if [ -d "$BRAND_DIR" ]; then
            BRANDS+=("$(basename "$BRAND_DIR")")
        fi
    done
fi

if [ ${#BRANDS[@]} -eq 0 ]; then
    echo "❌ data-pipeline/database/data 폴더에 처리할 브랜드 데이터가 없습니다."
    exit 1
fi

echo "[INFO] 필요 패키지(psycopg2-binary, pymongo, python-dotenv) 설치 여부 확인 및 설치 중..."
python3 -m pip install -q psycopg2-binary pymongo python-dotenv

# 1. 초기화 (PostgreSQL Truncate) - 생략됨 (기존 데이터 유지)
echo "[INFO] 기존 데이터를 유지하면서 새로운 데이터만 삽입(Upsert)합니다..."

echo "[INFO] macOS 및 Windows 환경 식별자 찌꺼기 파일들 정리 중..."
find data-pipeline/database/data \( -name "*:Zone.Identifier" -o -name ".DS_Store" \) -type f -delete || true

# 2. HDFS 초기화 - 생략됨 (기존 이미지 유지)

TOTAL_BRANDS=${#BRANDS[@]}
CURRENT_BRAND=0

for BRAND in "${BRANDS[@]}"; do
    CURRENT_BRAND=$((CURRENT_BRAND + 1))
    TARGET_DIR="data-pipeline/database/data/$BRAND"
    
    if [ ! -d "$TARGET_DIR" ]; then
        echo "======================================================"
        echo "⏳ [$BRAND] 데이터 폴더($TARGET_DIR)가 존재하지 않아 건너뜁니다."
        echo "======================================================"
        continue
    fi

    echo "======================================================"
    echo " 🚀 [$CURRENT_BRAND/$TOTAL_BRANDS] $BRAND 데이터 로드 시작"
    echo "======================================================"

    # HDFS 이미지 전송 (hadoop/raw/ 아래 구조 가정)
    HDFS_SRC="$TARGET_DIR/hadoop/raw/$BRAND"
    if [ -d "$HDFS_SRC" ]; then
        echo "[$BRAND] 하둡 HDFS에 로컬 이미지 데이터 업로드 준비 중..."
        
        echo "[$BRAND] 도커 컨테이너로 파일 복사 중 (이 작업은 시간이 다소 소요될 수 있습니다)..."
        docker cp "$HDFS_SRC" namenode-main:/tmp/
        
        echo "[$BRAND] 불필요한 OS 찌꺼기 파일 정리 중..."
        docker exec namenode-main find /tmp/ \( -name "*:Zone.Identifier" -o -name ".DS_Store" \) -type f -delete || true
        
        echo "[$BRAND] HDFS 스토리지로 파일 이동 중 (이 작업은 시간이 다소 소요될 수 있습니다)..."
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
