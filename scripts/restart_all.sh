#!/bin/bash

echo "=========================================="
echo "Main Project Lookalike - 전체 재시작"
echo "=========================================="

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"# 중지
bash "${SCRIPT_DIR}/stop_all.sh"

echo ""
echo "10초 대기 후 재시작..."
sleep 10

# 시작
bash "${SCRIPT_DIR}/start_all.sh"

