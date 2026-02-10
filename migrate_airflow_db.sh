#!/bin/bash
# ============================================================
# migrate_airflow_db.sh
# 기존 환경에서 Airflow DB를 datadb에서 airflowdb로 분리하는 스크립트
# 
# 사용법: bash migrate_airflow_db.sh
# ============================================================

set -e

# .env 파일에서 설정 읽기
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/.env" ]; then
    source "$SCRIPT_DIR/.env"
else
    echo "❌ .env 파일을 찾을 수 없습니다 ($SCRIPT_DIR/.env)"
    exit 1
fi

PG_CONTAINER="postgres-main"
AIRFLOW_WS="airflow-webserver-main"
AIRFLOW_SC="airflow-scheduler-main"
KEEP_TABLES="'users','posts','comments','products','naver_prices','product_features','search_logs'"

echo "============================================"
echo "  Airflow DB 분리 마이그레이션"
echo "============================================"
echo ""

# ---- 1. PostgreSQL 컨테이너 확인 ----
echo "1️⃣  PostgreSQL 컨테이너 확인..."
if ! docker ps --format '{{.Names}}' | grep -q "^${PG_CONTAINER}$"; then
    echo "❌ PostgreSQL 컨테이너(${PG_CONTAINER})가 실행 중이 아닙니다."
    exit 1
fi
echo "   ✅ ${PG_CONTAINER} 실행 중"

# ---- 2. 현재 상태 확인 ----
echo ""
echo "2️⃣  현재 datadb 테이블 수 확인..."
TOTAL=$(docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -tc \
    "SELECT count(*) FROM pg_tables WHERE schemaname = 'public';" | tr -d ' ')
echo "   현재 datadb 테이블 수: ${TOTAL}개"

if [ "$TOTAL" -le 7 ]; then
    echo "   ✅ Airflow 테이블이 이미 분리되어 있거나 없습니다. 마이그레이션이 필요 없습니다."
    
    # airflowdb 존재 여부만 확인
    HAS_AIRFLOW_DB=$(docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d postgres -tc \
        "SELECT 1 FROM pg_database WHERE datname='${AIRFLOW_DB}';" | tr -d ' ')
    if [ "$HAS_AIRFLOW_DB" != "1" ]; then
        echo "   ⚠️  airflowdb가 없습니다. 생성합니다..."
        docker exec ${PG_CONTAINER} createdb -U ${POSTGRES_USER} ${AIRFLOW_DB}
        echo "   ✅ airflowdb 생성 완료"
    fi
    echo ""
    echo "🚀 완료!"
    exit 0
fi

echo "   ⚠️  Airflow 테이블이 섞여 있습니다. 분리를 진행합니다."

# ---- 3. Airflow 컨테이너 중지 ----
echo ""
echo "3️⃣  Airflow 컨테이너 중지..."
docker stop ${AIRFLOW_WS} ${AIRFLOW_SC} 2>/dev/null || true
docker rm ${AIRFLOW_WS} ${AIRFLOW_SC} 2>/dev/null || true
echo "   ✅ Airflow 컨테이너 중지 및 제거 완료"

# ---- 4. airflowdb 생성 ----
echo ""
echo "4️⃣  airflowdb 생성..."
HAS_AIRFLOW_DB=$(docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d postgres -tc \
    "SELECT 1 FROM pg_database WHERE datname='${AIRFLOW_DB}';" | tr -d ' ')
if [ "$HAS_AIRFLOW_DB" = "1" ]; then
    echo "   ✅ airflowdb 이미 존재"
else
    docker exec ${PG_CONTAINER} createdb -U ${POSTGRES_USER} ${AIRFLOW_DB}
    echo "   ✅ airflowdb 생성 완료"
fi

# ---- 5. datadb에서 Airflow 테이블 삭제 ----
echo ""
echo "5️⃣  datadb에서 Airflow 테이블 삭제..."

# 기존 연결 끊기
docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d postgres -c \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${POSTGRES_DB}' AND pid <> pg_backend_pid();" > /dev/null 2>&1 || true

# Airflow 테이블 삭제
docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c "
DO \$\$
DECLARE
    tbl RECORD;
    keep_tables TEXT[] := ARRAY[${KEEP_TABLES}];
BEGIN
    FOR tbl IN
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public'
        AND NOT (tablename = ANY(keep_tables))
    LOOP
        EXECUTE format('DROP TABLE IF EXISTS %I CASCADE', tbl.tablename);
        RAISE NOTICE 'Dropped: %', tbl.tablename;
    END LOOP;
END \$\$;
"
echo "   ✅ Airflow 테이블 삭제 완료"

# ---- 6. Airflow 컨테이너 재생성 ----
echo ""
echo "6️⃣  Airflow 컨테이너 재생성 (airflowdb 사용)..."
docker compose up -d airflow-webserver airflow-scheduler
echo "   ✅ Airflow 재시작 완료"

# ---- 7. 결과 확인 ----
echo ""
echo "7️⃣  최종 확인..."
sleep 5
DATADB_COUNT=$(docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -tc \
    "SELECT count(*) FROM pg_tables WHERE schemaname = 'public';" | tr -d ' ')
echo "   📊 datadb 테이블 수: ${DATADB_COUNT}개 (프로젝트 전용)"

echo ""
echo "============================================"
echo "  🚀 마이그레이션 완료!"
echo "  datadb: 프로젝트 테이블 ${DATADB_COUNT}개"
echo "  airflowdb: Airflow 전용 (자동 생성 중)"
echo "============================================"
