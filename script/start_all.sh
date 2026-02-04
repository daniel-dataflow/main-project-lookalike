#!/bin/bash
# ============================================================
# Main Project Lookalike — 통합 Docker 시작 스크립트
# 위치: ~/main-project-lookalike/script/start_all.sh
# 실행: bash ~/main-project-lookalike/script/start_all.sh
# ============================================================

# ──────────────────────────────────────────────
# 경로 고정
# ──────────────────────────────────────────────
PROJECT_ROOT="$HOME/main-project-lookalike"
LOG_DIR="${PROJECT_ROOT}/logs"
mkdir -p "${LOG_DIR}"

# ──────────────────────────────────────────────
# JSON 로그 디렉토리 생성 (추가됨)
# ──────────────────────────────────────────────
mkdir -p "${LOG_DIR}/system"
mkdir -p "${LOG_DIR}/database"
mkdir -p "${LOG_DIR}/elasticsearch"
mkdir -p "${LOG_DIR}/kafka"
mkdir -p "${LOG_DIR}/hadoop"
mkdir -p "${LOG_DIR}/spark"
mkdir -p "${LOG_DIR}/fastapi"
mkdir -p "${LOG_DIR}/airflow"



# 환경변수 로드
. "${PROJECT_ROOT}/.env"

# .env 덮어쓰기 방지
PROJECT_ROOT="$HOME/main-project-lookalike"
LOG_DIR="${PROJECT_ROOT}/logs"

# 타임스탬프
TS=$(date +%Y%m%d_%H%M%S)
MAIN_LOG="${LOG_DIR}/system/${TS}_start.log"
HC_LOG="${LOG_DIR}/system/${TS}_healthcheck.log"
JSON_LOG="${LOG_DIR}/system/${TS}_start_all.json.log"


# ──────────────────────────────────────────────
# 색상
# ──────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'
YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

# ──────────────────────────────────────────────
# JSON 로깅 함수 (추가됨)
# ──────────────────────────────────────────────
log_json() {
    local level="$1"
    local service="$2"
    local message="$3"
    local component="${4:-}"
    local status="${5:-}"
    
    local timestamp=$(date -u +"%Y-%m-%dT%H:%M:%S.%3NZ")
    local json_file="${LOG_DIR}/${service}/${service}.json.log"
    
    cat >> "${json_file}" << JSONEOF
{"timestamp":"${timestamp}","level":"${level}","service":"${service}","component":"${component}","message":"${message}","status":"${status}","hostname":"$(hostname)"}
JSONEOF
}

# ──────────────────────────────────────────────
# 로깅 함수 (JSON 로깅 추가됨)
# ──────────────────────────────────────────────
log_info()  { 
    echo -e "${GREEN}[INFO ]${NC} $(date '+%H:%M:%S') $*" | tee -a "${MAIN_LOG}"
    log_json "INFO" "system" "$*" "start_all" "info"
}

log_warn()  { 
    echo -e "${YELLOW}[WARN ]${NC} $(date '+%H:%M:%S') $*" | tee -a "${MAIN_LOG}"
    log_json "WARNING" "system" "$*" "start_all" "warning"
}

log_error() { 
    echo -e "${RED}[ERROR]${NC} $(date '+%H:%M:%S') $*" | tee -a "${MAIN_LOG}"
    log_json "ERROR" "system" "$*" "start_all" "error"
}

log_phase() { 
    echo -e "\n${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}" | tee -a "${MAIN_LOG}"
    echo -e "${BLUE}  $*${NC}" | tee -a "${MAIN_LOG}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}" | tee -a "${MAIN_LOG}"
    log_json "INFO" "system" "$*" "phase" "started"
}

log_hc()    { 
    echo "$(date '+%H:%M:%S') $*" >> "${HC_LOG}"
}

# ──────────────────────────────────────────────
# 서비스별 JSON 로그 함수 (추가됨)
# ──────────────────────────────────────────────
log_service_info() {
    local service="$1"
    local component="$2"
    local message="$3"
    local status="$4"
    log_json "INFO" "${service}" "${message}" "${component}" "${status}"
}

log_service_error() {
    local service="$1"
    local component="$2"
    local message="$3"
    local status="$4"
    log_json "ERROR" "${service}" "${message}" "${component}" "${status}"
}

log_service_warn() {
    local service="$1"
    local component="$2"
    local message="$3"
    local status="$4"
    log_json "WARNING" "${service}" "${message}" "${component}" "${status}"
}

# ──────────────────────────────────────────────
# 헬스체크 대기 함수 (JSON 로깅 추가됨)
# ──────────────────────────────────────────────
wait_for_healthy() {
    local CONTAINER="$1"
    local MAX="$2"
    local INTERVAL="$3"
    local ELAPSED=0
    local FAIL_LOG="${LOG_DIR}/fail_${CONTAINER}_${TS}.log"
    
    # 서비스명 추출 (컨테이너명에서 -main 제거)
    local SERVICE_NAME="${CONTAINER%-main}"
    
    # 서비스 카테고리 결정
    local SERVICE_CATEGORY="system"
    case "${SERVICE_NAME}" in
        postgres|mongo|redis) SERVICE_CATEGORY="database" ;;
        elasticsearch) SERVICE_CATEGORY="elasticsearch" ;;
        zookeeper|kafka) SERVICE_CATEGORY="kafka" ;;
        namenode|datanode|resourcemanager|nodemanager) SERVICE_CATEGORY="hadoop" ;;
        spark-master|spark-worker*) SERVICE_CATEGORY="spark" ;;
        fastapi) SERVICE_CATEGORY="fastapi" ;;
        airflow*) SERVICE_CATEGORY="airflow" ;;
    esac

    log_info "  [대기] ${CONTAINER} (최대 ${MAX}초)"
    log_service_info "${SERVICE_CATEGORY}" "${SERVICE_NAME}" "헬스체크 시작" "waiting"

    while [ $ELAPSED -lt $MAX ]; do

        # 컨테이너 존재 여부
        if ! docker ps --all --format '{{.Names}}' | grep -qx "${CONTAINER}"; then
            log_hc "MISSING ${CONTAINER}"
            log_warn "  ${CONTAINER} — 컨테이너 시작 대기 중 (${ELAPSED}/${MAX}초)"
            sleep "${INTERVAL}"
            ELAPSED=$((ELAPSED + INTERVAL))
            continue
        fi

        # 컨테이너 실행 상태
        local STATE
        STATE=$(docker inspect --format '{{.State.Status}}' "${CONTAINER}" 2>/dev/null)
        if [ "${STATE}" != "running" ]; then
            log_hc "NOT_RUNNING ${CONTAINER} state=${STATE} elapsed=${ELAPSED}"
            log_warn "  ${CONTAINER} — 상태: ${STATE} (${ELAPSED}/${MAX}초)"
            sleep "${INTERVAL}"
            ELAPSED=$((ELAPSED + INTERVAL))
            continue
        fi

        # healthcheck 정의 여부
        local HAS_HC
        HAS_HC=$(docker inspect --format '{{if .Config.Healthcheck}}yes{{else}}no{{end}}' "${CONTAINER}" 2>/dev/null)
        if [ "${HAS_HC}" = "no" ]; then
            log_hc "HEALTHY ${CONTAINER} (healthcheck 미정의 — running으로 통과)"
            log_info "  [통과] ${CONTAINER} — running (healthcheck 미정의)"
            log_service_info "${SERVICE_CATEGORY}" "${SERVICE_NAME}" "헬스체크 통과 (running)" "healthy"
            return 0
        fi

        # healthcheck 결과
        local HEALTH
        HEALTH=$(docker inspect --format '{{.State.Health.Status}}' "${CONTAINER}" 2>/dev/null)

        case "${HEALTH}" in
            healthy)
                log_hc "HEALTHY ${CONTAINER} elapsed=${ELAPSED}"
                log_info "  [통과] ${CONTAINER} — healthy (${ELAPSED}초)"
                log_service_info "${SERVICE_CATEGORY}" "${SERVICE_NAME}" "헬스체크 통과" "healthy"
                return 0
                ;;
            unhealthy)
                docker logs "${CONTAINER}" > "${FAIL_LOG}" 2>&1
                log_hc "UNHEALTHY ${CONTAINER} elapsed=${ELAPSED} log=${FAIL_LOG}"
                log_error "  ${CONTAINER} — unhealthy"
                log_error "  실패 로그: ${FAIL_LOG}"
                log_service_error "${SERVICE_CATEGORY}" "${SERVICE_NAME}" "헬스체크 실패 (unhealthy)" "unhealthy"
                return 1
                ;;
            *)
                log_hc "WAITING ${CONTAINER} health=${HEALTH} elapsed=${ELAPSED}"
                log_warn "  ${CONTAINER} — ${HEALTH} (${ELAPSED}/${MAX}초)"
                ;;
        esac

        sleep "${INTERVAL}"
        ELAPSED=$((ELAPSED + INTERVAL))
    done

    # 타임아웃
    docker logs "${CONTAINER}" > "${FAIL_LOG}" 2>&1
    log_hc "TIMEOUT ${CONTAINER} max=${MAX} log=${FAIL_LOG}"
    log_error "  ${CONTAINER} — 타임아웃 (${MAX}초 초과)"
    log_error "  실패 로그: ${FAIL_LOG}"
    log_service_error "${SERVICE_CATEGORY}" "${SERVICE_NAME}" "헬스체크 타임아웃" "timeout"
    return 1
}

# ──────────────────────────────────────────────
# 실패 종료 핸들러 (JSON 로깅 추가됨)
# ──────────────────────────────────────────────
on_failure() {
    local WHAT="$1"
    log_error ""
    log_error "⛔ [${WHAT}] 시작 실패 — 스크립트 중단"
    log_error "  시작 로그      : ${MAIN_LOG}"
    log_error "  헬스체크 로그  : ${HC_LOG}"
    log_error "  실패 상세 로그 : ${LOG_DIR}/fail_* 파일 참조"
    log_json "ERROR" "system" "${WHAT} 시작 실패 - 스크립트 중단" "failure" "failed"
    exit 1
}

# ══════════════════════════════════════════════════════
# ▶ MAIN
# ══════════════════════════════════════════════════════
log_phase "Main Project Lookalike — 전체 서비스 시작"
log_info "PROJECT_ROOT : ${PROJECT_ROOT}"
log_info "MAIN LOG     : ${MAIN_LOG}"
log_info "HC LOG       : ${HC_LOG}"
log_info "JSON LOG     : ${JSON_LOG}"

cd "${PROJECT_ROOT}"

# Docker 네트워크 생성
docker network create main-network 2>/dev/null || true
log_service_info "system" "docker-network" "main-network 준비 완료" "ready"

# ─────────────────────────────────────
# Phase 1 — 데이터베이스
# ─────────────────────────────────────
log_phase "Phase 1 — 데이터베이스 (PostgreSQL / MongoDB / Redis)"
log_service_info "database" "all" "데이터베이스 클러스터 시작 시작" "starting"

cd "${PROJECT_ROOT}"
docker compose up -d postgresql mongodb redis 2>&1 | tee -a "${MAIN_LOG}"
log_service_info "database" "all" "데이터베이스 컨테이너 실행 명령 완료" "started"

wait_for_healthy "postgres-main"  60 3 || on_failure "PostgreSQL"
wait_for_healthy "mongo-main"     60 3 || on_failure "MongoDB"
wait_for_healthy "redis-main"     30 3 || on_failure "Redis"

log_service_info "database" "all" "데이터베이스 클러스터 모두 정상" "completed"

# ─────────────────────────────────────
# Phase 2 — Elasticsearch
# ─────────────────────────────────────
log_phase "Phase 2 — Elasticsearch"
log_service_info "elasticsearch" "elasticsearch" "Elasticsearch 시작 시작" "starting"

cd "${PROJECT_ROOT}"
docker compose up -d elasticsearch 2>&1 | tee -a "${MAIN_LOG}"
log_service_info "elasticsearch" "elasticsearch" "Elasticsearch 컨테이너 실행 명령 완료" "started"

wait_for_healthy "elasticsearch-main" 90 5 || on_failure "Elasticsearch"

log_service_info "elasticsearch" "elasticsearch" "Elasticsearch 정상 작동" "completed"

# ─────────────────────────────────────
# Phase 3 — Zookeeper → Kafka
# ─────────────────────────────────────
log_phase "Phase 3 — Zookeeper → Kafka"
log_service_info "kafka" "zookeeper" "Zookeeper 시작 시작" "starting"

cd "${PROJECT_ROOT}"
docker compose up -d zookeeper 2>&1 | tee -a "${MAIN_LOG}"
log_service_info "kafka" "zookeeper" "Zookeeper 컨테이너 실행 명령 완료" "started"

wait_for_healthy "zookeeper-main" 60 5 || on_failure "Zookeeper"

log_service_info "kafka" "kafka" "Kafka 시작 시작" "starting"
docker compose up -d kafka 2>&1 | tee -a "${MAIN_LOG}"
log_service_info "kafka" "kafka" "Kafka 컨테이너 실행 명령 완료" "started"

wait_for_healthy "kafka-main" 120 10 || on_failure "Kafka"

log_service_info "kafka" "all" "Kafka 클러스터 모두 정상" "completed"

# ─────────────────────────────────────
# Phase 4 — Hadoop
# ─────────────────────────────────────
log_phase "Phase 4 — Hadoop"
log_service_info "hadoop" "namenode" "Hadoop NameNode 시작 시작" "starting"

cd "${PROJECT_ROOT}"
docker compose up -d namenode 2>&1 | tee -a "${MAIN_LOG}"
log_service_info "hadoop" "namenode" "NameNode 컨테이너 실행 명령 완료" "started"

wait_for_healthy "namenode-main" 60 5 || on_failure "Hadoop NameNode"

log_service_info "hadoop" "datanode" "Hadoop DataNode/ResourceManager 시작 시작" "starting"
docker compose up -d datanode resourcemanager 2>&1 | tee -a "${MAIN_LOG}"
log_service_info "hadoop" "datanode" "DataNode/ResourceManager 컨테이너 실행 명령 완료" "started"

wait_for_healthy "datanode-main" 60 5 || on_failure "Hadoop DataNode"
wait_for_healthy "resourcemanager-main" 60 5 || on_failure "Hadoop ResourceManager"

log_service_info "hadoop" "nodemanager" "Hadoop NodeManager 시작 시작" "starting"
docker compose up -d nodemanager 2>&1 | tee -a "${MAIN_LOG}"
log_service_info "hadoop" "nodemanager" "NodeManager 컨테이너 실행 명령 완료" "started"

wait_for_healthy "nodemanager-main" 60 5 || on_failure "Hadoop NodeManager"

log_service_info "hadoop" "all" "Hadoop 클러스터 모두 정상" "completed"

# ─────────────────────────────────────
# Phase 5 — Spark
# ─────────────────────────────────────
log_phase "Phase 5 — Spark (Master → Worker)"
log_service_info "spark" "spark-master" "Spark Master 시작 시작" "starting"

cd "${PROJECT_ROOT}"
docker compose up -d spark-master 2>&1 | tee -a "${MAIN_LOG}"
log_service_info "spark" "spark-master" "Spark Master 컨테이너 실행 명령 완료" "started"

wait_for_healthy "spark-master-main" 60 5 || on_failure "Spark Master"

log_service_info "spark" "spark-worker-1" "Spark Worker 시작 시작" "starting"
docker compose up -d spark-worker-1 2>&1 | tee -a "${MAIN_LOG}"
log_service_info "spark" "spark-worker-1" "Spark Worker 컨테이너 실행 명령 완료" "started"

wait_for_healthy "spark-worker-1-main" 60 5 || on_failure "Spark Worker"

log_service_info "spark" "all" "Spark 클러스터 모두 정상" "completed"

# ─────────────────────────────────────
# Phase 6 — FastAPI
# ─────────────────────────────────────
log_phase "Phase 6 — FastAPI"
log_service_info "fastapi" "fastapi" "FastAPI 시작 시작" "starting"

cd "${PROJECT_ROOT}"
docker compose up -d fastapi 2>&1 | tee -a "${MAIN_LOG}"
log_service_info "fastapi" "fastapi" "FastAPI 컨테이너 실행 명령 완료" "started"

wait_for_healthy "fastapi-main" 60 5 || on_failure "FastAPI"

log_service_info "fastapi" "fastapi" "FastAPI 정상 작동" "completed"

# ─────────────────────────────────────
# Phase 7 — Airflow
# ─────────────────────────────────────
log_phase "Phase 7 — Airflow (Webserver → Scheduler)"
log_service_info "airflow" "airflow-webserver" "Airflow Webserver 시작 시작" "starting"

cd "${PROJECT_ROOT}"
docker compose up -d airflow-webserver 2>&1 | tee -a "${MAIN_LOG}"
log_service_info "airflow" "airflow-webserver" "Airflow Webserver 컨테이너 실행 명령 완료" "started"

# Airflow 3.0.3은 헬스체크가 느리므로 여유있게 대기
wait_for_healthy "airflow-webserver-main" 120 10 || on_failure "Airflow Webserver"

log_service_info "airflow" "airflow-scheduler" "Airflow Scheduler 시작 시작" "starting"
docker compose up -d airflow-scheduler 2>&1 | tee -a "${MAIN_LOG}"
log_service_info "airflow" "airflow-scheduler" "Airflow Scheduler 컨테이너 실행 명령 완료" "started"

wait_for_healthy "airflow-scheduler-main" 120 10 || on_failure "Airflow Scheduler"

log_service_info "airflow" "all" "Airflow 모두 정상" "completed"

# ─────────────────────────────────────
# 완료
# ─────────────────────────────────────
log_phase "✅ 모든 서비스 시작 완료"
log_json "INFO" "system" "전체 서비스 시작 완료" "completion" "success"

log_info ""
log_info "  접속 주소:"
log_info "    FastAPI         http://localhost:${FASTAPI_PORT}"
log_info "    FastAPI Docs    http://localhost:${FASTAPI_PORT}/docs"
log_info "    Airflow         http://localhost:${AIRFLOW_WEBSERVER_PORT}  (admin / admin123)"
log_info "    Elasticsearch   http://localhost:${EXTERNAL_ELASTICSEARCH_PORT}"
log_info "    Spark Master    http://localhost:${SPARK_MASTER_WEBUI_PORT}"
log_info "    Spark Worker    http://localhost:${SPARK_WORKER1_WEBUI_PORT}"
log_info ""
log_info "  Hadoop:"
log_info "    NameNode        http://localhost:${HADOOP_NAMENODE_WEBUI_PORT}"
log_info "    ResourceManager http://localhost:${HADOOP_RESOURCEMANAGER_WEBUI_PORT}"
log_info ""
log_info "  로그 디렉토리: ${LOG_DIR}/"
log_info "  JSON 로그:     ${LOG_DIR}/*/*.json.log"
log_info ""
log_info "  JSON 로그 확인:"
log_info "    전체: tail -f ${LOG_DIR}/system/start_all.json.log | jq ."
log_info "    DB:   tail -f ${LOG_DIR}/database/database.json.log | jq ."