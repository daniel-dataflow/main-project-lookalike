#!/bin/bash
# ============================================================
# Main Project Lookalike — 통합 Docker 시작 스크립트  V14
# 위치: ~/main-project-lookalike/script/start_all.sh
# 실행: bash ~/main-project-lookalike/script/start_all.sh
#
# V14 변경사항:
#   - 불필요한 conf 파일 사전 체크 기능 10초 대기 로직 제거 (Docker 환경변수로 위임)
# ============================================================

# ──────────────────────────────────────────────
# 경로 고정
# ──────────────────────────────────────────────
PROJECT_ROOT="$HOME/main-project-lookalike"
LOG_DIR="${PROJECT_ROOT}/logs"
FAIL_DIR="${LOG_DIR}/fail"
mkdir -p "${FAIL_DIR}"

# ──────────────────────────────────────────────
# JSON 로그 디렉토리 생성
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
# JSON 로깅 함수
# ──────────────────────────────────────────────
log_json() {
    local level="$1"
    local service="$2"
    local message="$3"
    local component="${4:-}"
    local status="${5:-}"

    local timestamp
    timestamp=$(date -u +"%Y-%m-%dT%H:%M:%S.%3NZ")
    local json_file="${LOG_DIR}/${service}/${service}.json.log"

    cat >> "${json_file}" << JSONEOF
{"timestamp":"${timestamp}","level":"${level}","service":"${service}","component":"${component}","message":"${message}","status":"${status}","hostname":"$(hostname)"}
JSONEOF
}

# ──────────────────────────────────────────────
# 로깅 함수
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
log_hc()    { echo "$(date '+%H:%M:%S') $*" >> "${HC_LOG}"; }

# ──────────────────────────────────────────────
# 서비스별 JSON 로그 함수
# ──────────────────────────────────────────────
log_service_info()  { log_json "INFO"    "${1}" "${3}" "${2}" "${4}"; }
log_service_error() { log_json "ERROR"   "${1}" "${3}" "${2}" "${4}"; }
log_service_warn()  { log_json "WARNING" "${1}" "${3}" "${2}" "${4}"; }

# ──────────────────────────────────────────────
# Fernet Key / Secret Key 자동 생성
# ──────────────────────────────────────────────
ensure_airflow_secrets() {
    local env_file="${PROJECT_ROOT}/.env"
    local changed=0

    # AIRFLOW_FERNET_KEY
    if ! grep -q "^AIRFLOW_FERNET_KEY=.\+" "${env_file}" 2>/dev/null; then
        local fernet
        fernet=$(python3 -c \
            "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" \
            2>/dev/null || \
            python3 -c \
            "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())")
        if grep -q "^AIRFLOW_FERNET_KEY=" "${env_file}" 2>/dev/null; then
            sed -i "s|^AIRFLOW_FERNET_KEY=.*|AIRFLOW_FERNET_KEY=${fernet}|" "${env_file}"
        else
            echo "AIRFLOW_FERNET_KEY=${fernet}" >> "${env_file}"
        fi
        log_warn "AIRFLOW_FERNET_KEY 자동 생성 → .env 저장됨"
        changed=1
    fi

    # AIRFLOW_WEBSERVER_SECRET_KEY
    if ! grep -q "^AIRFLOW_WEBSERVER_SECRET_KEY=.\+" "${env_file}" 2>/dev/null; then
        local secret
        secret=$(python3 -c "import secrets; print(secrets.token_hex(32))")
        if grep -q "^AIRFLOW_WEBSERVER_SECRET_KEY=" "${env_file}" 2>/dev/null; then
            sed -i "s|^AIRFLOW_WEBSERVER_SECRET_KEY=.*|AIRFLOW_WEBSERVER_SECRET_KEY=${secret}|" "${env_file}"
        else
            echo "AIRFLOW_WEBSERVER_SECRET_KEY=${secret}" >> "${env_file}"
        fi
        log_warn "AIRFLOW_WEBSERVER_SECRET_KEY 자동 생성 → .env 저장됨"
        changed=1
    fi

    # AIRFLOW_ADMIN_PASSWORD
    if ! grep -q "^AIRFLOW_ADMIN_PASSWORD=.\+" "${env_file}" 2>/dev/null; then
        local pw
        pw=$(grep "^ADMIN_PASSWORD=" "${env_file}" | cut -d= -f2)
        if [ -z "${pw}" ]; then
            log_error "  ⚠️  ADMIN_PASSWORD가 .env에 설정되지 않았습니다. .env 파일을 확인하세요."
            pw="changeme"
        fi
        if grep -q "^AIRFLOW_ADMIN_PASSWORD=" "${env_file}" 2>/dev/null; then
            sed -i "s|^AIRFLOW_ADMIN_PASSWORD=.*|AIRFLOW_ADMIN_PASSWORD=${pw}|" "${env_file}"
        else
            echo "AIRFLOW_ADMIN_PASSWORD=${pw}" >> "${env_file}"
        fi
        log_warn "AIRFLOW_ADMIN_PASSWORD 자동 설정 → .env 저장됨"
        changed=1
    fi

    # 변경됐으면 재로드
    if [ $changed -eq 1 ]; then
        . "${env_file}"
        log_info "  .env 재로드 완료"
    fi
}


# ──────────────────────────────────────────────
# 헬스체크 대기 함수
# ──────────────────────────────────────────────
wait_for_healthy() {
    local CONTAINER="$1"
    local MAX="$2"
    local INTERVAL="$3"
    local ELAPSED=0
    local FAIL_LOG="${FAIL_DIR}/${TS}_fail_${CONTAINER}.log"

    local SERVICE_NAME="${CONTAINER%-main}"
    local SERVICE_CATEGORY="system"
    case "${SERVICE_NAME}" in
        postgres|mongo|redis)                       SERVICE_CATEGORY="database" ;;
        elasticsearch)                              SERVICE_CATEGORY="elasticsearch" ;;
        zookeeper|kafka)                            SERVICE_CATEGORY="kafka" ;;
        namenode|datanode)                          SERVICE_CATEGORY="hadoop" ;;
        spark-master|spark-worker*)                 SERVICE_CATEGORY="spark" ;;
        fastapi)                                    SERVICE_CATEGORY="fastapi" ;;
        airflow-webserver|airflow-scheduler|\
        airflow-triggerer)                          SERVICE_CATEGORY="airflow" ;;
    esac

    log_info "  [대기] ${CONTAINER} (최대 ${MAX}초)"
    log_service_info "${SERVICE_CATEGORY}" "${SERVICE_NAME}" "헬스체크 시작" "waiting"

    while [ $ELAPSED -lt $MAX ]; do

        if ! docker ps --all --format '{{.Names}}' | grep -qx "${CONTAINER}"; then
            log_hc "MISSING ${CONTAINER}"
            sleep "${INTERVAL}"; ELAPSED=$((ELAPSED + INTERVAL)); continue
        fi

        local STATE
        STATE=$(docker inspect --format '{{.State.Status}}' "${CONTAINER}" 2>/dev/null)
        if [ "${STATE}" != "running" ]; then
            log_hc "NOT_RUNNING ${CONTAINER} state=${STATE} elapsed=${ELAPSED}"
            log_warn "  ${CONTAINER} — 상태: ${STATE} (${ELAPSED}/${MAX}초)"
            sleep "${INTERVAL}"; ELAPSED=$((ELAPSED + INTERVAL)); continue
        fi

        local HAS_HC
        HAS_HC=$(docker inspect --format \
            '{{if .Config.Healthcheck}}yes{{else}}no{{end}}' "${CONTAINER}" 2>/dev/null)
        if [ "${HAS_HC}" = "no" ]; then
            log_hc "HEALTHY ${CONTAINER} (healthcheck 미정의 — running으로 통과)"
            log_info "  [통과] ${CONTAINER} — running (healthcheck 미정의)"
            log_service_info "${SERVICE_CATEGORY}" "${SERVICE_NAME}" "헬스체크 통과 (running)" "healthy"
            return 0
        fi

        local HEALTH
        HEALTH=$(docker inspect --format \
            '{{.State.Health.Status}}' "${CONTAINER}" 2>/dev/null)

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

        sleep "${INTERVAL}"; ELAPSED=$((ELAPSED + INTERVAL))
    done

    docker logs "${CONTAINER}" > "${FAIL_LOG}" 2>&1
    log_hc "TIMEOUT ${CONTAINER} max=${MAX} log=${FAIL_LOG}"
    log_error "  ${CONTAINER} — 타임아웃 (${MAX}초 초과)"
    log_error "  실패 로그: ${FAIL_LOG}"
    log_service_error "${SERVICE_CATEGORY}" "${SERVICE_NAME}" "헬스체크 타임아웃" "timeout"
    return 1
}

# ──────────────────────────────────────────────
# 실패 종료 핸들러
# ──────────────────────────────────────────────
on_failure() {
    local WHAT="$1"
    log_error ""
    log_error "⛔ [${WHAT}] 시작 실패 — 스크립트 중단"
    log_error "  시작 로그      : ${MAIN_LOG}"
    log_error "  헬스체크 로그  : ${HC_LOG}"
    log_error "  실패 상세 로그 : ${FAIL_DIR}/ 참조"
    log_json "ERROR" "system" "${WHAT} 시작 실패 - 스크립트 중단" "failure" "failed"
    exit 1
}

# ══════════════════════════════════════════════════════
# ▶ MAIN
# ══════════════════════════════════════════════════════
log_phase "Main Project Lookalike — 전체 서비스 시작  (V14)"
log_info "PROJECT_ROOT : ${PROJECT_ROOT}"
log_info "MAIN LOG     : ${MAIN_LOG}"
log_info "HC LOG       : ${HC_LOG}"

cd "${PROJECT_ROOT}"

# ── 사전 점검 ────────────────────────────────
ensure_airflow_secrets

docker network create main-project-network 2>/dev/null || true
log_service_info "system" "docker-network" "main-project-network 준비 완료" "ready"

# ─────────────────────────────────────
# 서비스 전체 시작 (병렬 부팅 - Docker Compose 의존성 위임)
# ─────────────────────────────────────
log_phase "Phase 1 ~ 7 — 전체 서비스 병렬 시작 (Docker Compose 위임, 시각적 대기)"
log_service_info "system" "docker-compose" "docker compose up --wait 호출" "starting"

log_info "전체 컨테이너가 정상 작동(Healthy) 상태가 될 때까지 부팅 과정을 추적합니다."
log_info "최대 3분 정도 소요되며 각 서비스의 상태가 표시됩니다."

# docker compose up --wait : 모든 의존성이 healthy 될때까지 대기하며 상태바 출력
# (화면 UI 보존을 위해 tee를 제거하고, 에러 시 로그를 별도 수집합니다)
docker compose up --wait

if [ $? -ne 0 ]; then
    log_error "⚠️ 일부 서비스 부팅에 실패했거나 대기 시간을 초과했습니다."
    
    # 실패(Unhealthy) 컨테이너 원인 분석 및 로그 추출
    UNHEALTHY_CONTAINERS=$(docker ps --filter "health=unhealthy" --format "{{.Names}}")
    if [ -n "$UNHEALTHY_CONTAINERS" ]; then
        log_error "❌ 다음 컨테이너가 Unhealthy 상태입니다: $UNHEALTHY_CONTAINERS"
        for container in $UNHEALTHY_CONTAINERS; do
            log_info "==== $container 실패 원인 로그 (Tail 50) ====" | tee -a "${MAIN_LOG}"
            docker logs "$container" --tail 50 2>&1 | tee -a "${MAIN_LOG}"
        done
    fi
    
    # 비정상 종료(Exited) 컨테이너 원인 분석 (정상 종료된 init-db 제외)
    EXITED_CONTAINERS=$(docker ps -a --filter "status=exited" --format "{{.Names}}" | grep -v "init-db" || true)
    if [ -n "$EXITED_CONTAINERS" ]; then
        log_error "❌ 다음 컨테이너가 비정상 종료(Exited) 되었습니다: $EXITED_CONTAINERS"
        for container in $EXITED_CONTAINERS; do
            log_info "==== $container 종료 원인 로그 (Tail 50) ====" | tee -a "${MAIN_LOG}"
            docker logs "$container" --tail 50 2>&1 | tee -a "${MAIN_LOG}"
        done
    fi

    log_info "전체 상태 확인: docker ps -a"
    exit 1
fi

log_service_info "system" "all_services" "전체 컨테이너 헬스체크 통과" "completed"


# ─────────────────────────────────────
# 완료
# ─────────────────────────────────────
log_phase "✅ 모든 서비스 시작 완료"
log_json "INFO" "system" "전체 서비스 시작 완료" "completion" "success"

# Airflow admin 비밀번호 표시용
AIRFLOW_PW=$(grep "^AIRFLOW_ADMIN_PASSWORD=" "${PROJECT_ROOT}/.env" | cut -d= -f2)

log_info ""
log_info "  ── 접속 주소 ────────────────────────────────────"
log_info "    FastAPI         http://localhost:${FASTAPI_PORT}"
log_info "    FastAPI Docs    http://localhost:${FASTAPI_PORT}/docs"
log_info "    Airflow         http://localhost:${AIRFLOW_WEBSERVER_PORT}  (admin / ${AIRFLOW_PW})"
log_info "    Elasticsearch   http://localhost:${EXTERNAL_ELASTICSEARCH_PORT}"
log_info "    Spark Master    http://localhost:${SPARK_MASTER_WEBUI_PORT}"
log_info "    Spark Worker    http://localhost:${SPARK_WORKER1_WEBUI_PORT}"
log_info "    HDFS NameNode   http://localhost:${HADOOP_NAMENODE_WEBUI_PORT}"
log_info "  ─────────────────────────────────────────────────"
log_info ""
log_info "  ── 로그 ──────────────────────────────────────────"
log_info "    시작 로그   : ${MAIN_LOG}"
log_info "    헬스체크    : ${HC_LOG}"
log_info "    JSON 로그   : ${LOG_DIR}/*/*.json.log"
log_info ""
log_info "  ── JSON 로그 확인 ────────────────────────────────"
log_info "    전체 : tail -f ${LOG_DIR}/system/${TS}_start.log"
log_info "    Airflow: tail -f ${LOG_DIR}/airflow/airflow.json.log | jq ."
log_info "    Spark  : tail -f ${LOG_DIR}/spark/spark.json.log | jq ."
log_info "  ─────────────────────────────────────────────────"