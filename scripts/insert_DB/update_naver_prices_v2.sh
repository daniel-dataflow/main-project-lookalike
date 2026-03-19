#!/bin/bash
# ==============================================================================
# [V2] 네이버 최저가 업데이트 (누락 데이터 수동 보완용)
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SCRIPT_PATH="$SCRIPT_DIR/update_naver_prices_v2.py"

cd "$PROJECT_ROOT" || exit 1

echo "======================================================"
echo " 🛒 [Lookalike] 네이버 최저가 보완 배치 (V2) "
echo "======================================================"

# 1. 환경 변수 설정 (호스트OS 실행 대응)
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5432

# 2. 필수 패키지 확인 및 설치
echo "[1/3] 필요 패키지 확인 중..."
python3 -m pip install -q requests psycopg2-binary python-dotenv

# 3. 인자(LIMIT) 처리
LIMIT=${1:-500}
echo "[2/3] 처리 대상: 가격 미등록 상품 상위 $LIMIT 개"

# 4. 스크립트 실행
echo "[3/3] 배치 작업을 시작합니다 (Host: $POSTGRES_HOST)..."
python3 -u "$SCRIPT_PATH" "$LIMIT"

echo "======================================================"
echo " ✅ 모든 작업이 완료되었습니다."
echo "======================================================"
