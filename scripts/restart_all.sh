#!/bin/bash

echo "=========================================="
echo "Main Project Lookalike - 전체 재시작"
echo "=========================================="

# 중지
bash ~/main-project-lookalike/scripts/stop_all.sh

echo ""
echo "10초 대기 후 재시작..."
sleep 10

# 시작
bash ~/main-project-lookalike/scripts/start_all.sh

