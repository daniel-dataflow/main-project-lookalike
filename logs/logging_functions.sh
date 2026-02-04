#!/bin/bash
################################################################################
# JSON 로깅 함수
################################################################################

# 로그 디렉토리 설정
PROJECT_ROOT="${HOME}/main-project-lookalike"
LOG_DIR="${PROJECT_ROOT}/logs"

# JSON 로그 생성 함수
log_json() {
    local level="$1"           # INFO, ERROR, WARNING
    local service="$2"         # system, database, kafka, etc.
    local message="$3"         # 로그 메시지
    local component="${4:-}"   # 선택: postgres, mongodb, etc.
    local status="${5:-}"      # 선택: success, failed, healthy, etc.

    local timestamp=$(date -u +"%Y-%m-%dT%H:%M:%S.%3NZ")
    local log_file="${LOG_DIR}/${service}/${service}.json.log"

    # 디렉토리가 없으면 생성
    mkdir -p "${LOG_DIR}/${service}"

    # JSON 로그 생성
    cat >> "${log_file}" << EOF
{"timestamp":"${timestamp}","level":"${level}","service":"${service}","component":"${component}","message":"${message}","status":"${status}","hostname":"$(hostname)"}
EOF
}

# 간편 함수들
log_info() {
    log_json "INFO" "$@"
    echo "[INFO] [$2] $3"
}

log_error() {
    log_json "ERROR" "$@"
    echo "[ERROR] [$2] $3" >&2
}

log_warn() {
    log_json "WARNING" "$@"
    echo "[WARN] [$2] $3"
}