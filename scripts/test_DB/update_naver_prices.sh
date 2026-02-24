#!/bin/bash
# ==============================================================================
# 네이버 API를 통해 naver_prices 정보를 최신화하는 파이썬 배치 스크립트 래퍼
# ==============================================================================

PROJECT_ROOT="/home/ubuntu/main-project-lookalike"
SCRIPT_PATH="/home/ubuntu/main-project-lookalike/scripts/test_DB/update_naver_prices.py"

cd "$PROJECT_ROOT" || exit 1

echo "======================================================"
echo " 🛒 [Lookalike] 네이버 쇼핑 스크래퍼(API) 배치 통합러 "
echo "======================================================"
echo "[INFO] 가상 환경 (혹은 시스템) 파이썬을 이용해 스크립트를 시작합니다."

# 호스트 머신에서 실행될 때, .env 내부의 postgresql (컨테이너명)은 접속할 수 없으므로 127.0.0.1로 오버라이드.
# (파이썬 코드의 load_dotenv는 기존 환경변수가 있으면 덮어쓰지 않음)
export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT=5432

echo "[INFO] 필요 패키지(psycopg2-binary, requests, python-dotenv) 설치 여부 확인 및 설치 중..."
pip install -q requests psycopg2-binary python-dotenv

echo "[INFO] 로컬 호스트 환경에서 127.0.0.1:5432 DB에 직접 연결하여 스크립트를 시작합니다..."
python3 "$SCRIPT_PATH"

echo "======================================================"
echo "[INFO] 배치 스크립트 실행 종료"
echo "======================================================"
