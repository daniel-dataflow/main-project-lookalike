#!/bin/bash
set -e

# Change to the project root directory
cd "$(dirname "$0")/../.."

echo "======================================================"
echo " 🧹  [Lookalike] 모든 데이터베이스 강제 초기화 스크립트"
echo "======================================================"
echo "⚠️  주의: 이 스크립트는 PostgreSQL, MongoDB, Elasticsearch, Hadoop HDFS의"
echo "         모든 상품 관련 데이터를 한 번에 완전히 삭제합니다!"
echo "진행하시겠습니까? (y/n)"
read -r response

if [[ ! "$response" =~ ^[Yy]$ ]]; then
    echo "취소되었습니다."
    exit 0
fi

# 호스트 머신에서 실행 시 DB 컨테이너 도메인 해석 불가 방지
export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT=5432
export MONGODB_HOST=127.0.0.1
export MONGODB_PORT=27017

echo "[INFO] 필요 패키지(psycopg2-binary, pymongo, requests) 설치 여부 확인 및 설치 중..."
python3 -m pip install -q psycopg2-binary pymongo requests python-dotenv

echo "[1/4] Hadoop HDFS 데이터 통채로 삭제 중..."
docker exec namenode-main hdfs dfs -rm -r -f /raw || true
docker exec namenode-main hdfs dfs -mkdir -p /raw
echo "✅ HDFS 초기화 완료."

echo "[2/4] PostgreSQL 데이터 초기화 중..."
python3 scripts/insert_DB/import_brand_csv.py --init

echo "[3/4] MongoDB 데이터 삭제 중..."
python3 -c "
import os
import sys
from pymongo import MongoClient

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

MONGO_HOST = os.environ.get('MONGODB_HOST', '127.0.0.1')
MONGO_PORT = os.environ.get('MONGODB_PORT', '27017')
MONGO_DB = os.environ.get('POSTGRES_DB', 'datadb') 
MONGO_USER = os.environ.get('MONGODB_USER', 'datauser')
MONGO_PASS = os.environ.get('MONGODB_PASSWORD', 'DataPass2026!')

mongo_url = os.environ.get('MONGODB_URL', f'mongodb://{MONGO_USER}:{MONGO_PASS}@{MONGO_HOST}:{MONGO_PORT}/')

try:
    client = MongoClient(mongo_url)
    db = client[MONGO_DB]
    db.products.drop()
    print('✅ MongoDB (products 컬렉션) 초기화 완료.')
except Exception as e:
    print(f'❌ MongoDB 초기화 실패: {e}')
    sys.exit(1)
"

echo "[4/4] Elasticsearch 인덱스 삭제 중..."
python3 -c "
import requests
import sys
import os
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname('__file__'), '.env'))
except ImportError:
    pass

ES_PORT = os.environ.get('ELASTICSEARCH_PORT', '8903')

# Delete 'products' index on Elasticsearch
url = f'http://127.0.0.1:{ES_PORT}/products'
try:
    response = requests.delete(url)
    if response.status_code in [200, 404]:
        print('✅ Elasticsearch (products 인덱스) 초기화 완료.')
    else:
        print(f'❌ Elasticsearch 초기화 실패: {response.status_code} - {response.text}')
        sys.exit(1)
except Exception as e:
    print(f'❌ Elasticsearch 서버에 연결할 수 없거나 에러 발생: {e}')
    sys.exit(1)
"

echo "======================================================"
echo " 🗑️  모든 데이터베이스 및 파일 시스템이 깨끗하게 지워졌습니다!"
echo " 이제 ./scripts/insert_DB/load_all_brands.sh 스크립트를 실행하여"
echo " 전체 데이터를 새롭게 구축할 수 있습니다."
echo "======================================================"
