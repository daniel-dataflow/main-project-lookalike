#!/bin/bash

# =================================================================
# Naver Shopping Price Batch Job Manual Runner
# =================================================================

# 1. 경로 설정 (프로젝트 루트 기준)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/../.." && pwd )"
NV_PY_PATH="$PROJECT_ROOT/data-pipeline/spark/jobs/fashion_batch_job_NV.py"

echo "--------------------------------------------------------"
echo "🚀 네이버 쇼핑 최저가 배치 작업을 시작합니다."
echo "📅 실행 시간: $(date '+%Y-%m-%d %H:%M:%S')"
echo "--------------------------------------------------------"

# 2. 실행
# NV.py 내부적으로 ../../../.env 를 로드함.
# 호스트OS(WSL 등)에서 실행 시 컨테이너 이름(postgresql)을 찾을 수 없으므로 localhost로 명시적 주입.
export POSTGRES_HOST=localhost

python3 -u "$NV_PY_PATH"

# 3. 결과 확인
if [ $? -eq 0 ]; then
    echo "--------------------------------------------------------"
    echo "✅ 배치 작업이 성공적으로 완료되었습니다."
else
    echo "--------------------------------------------------------"
    echo "❌ 배치 작업 중 오류가 발생했습니다."
    exit 1
fi
