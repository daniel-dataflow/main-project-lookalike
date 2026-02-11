#!/bin/bash
# ============================================================
# apply_db_changes.sh
# 프로젝트 DB 스키마 변경사항을 한번에 적용하는 스크립트
# 이미 적용된 변경은 스킵하므로 여러 번 실행해도 안전합니다.
#
# 사용법: bash scripts/apply_db_changes.sh  (프로젝트 루트에서 실행)
# ============================================================

set -e

# .env 파일에서 설정 읽기 (source 대신 grep으로 안전하게 파싱)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "❌ .env 파일을 찾을 수 없습니다 ($ENV_FILE)"
    exit 1
fi

# 필요한 변수만 안전하게 추출 (주석, 변수치환 등 무시)
_env_val() { grep -m1 "^$1=" "$ENV_FILE" | cut -d'=' -f2- | tr -d '\r'; }

POSTGRES_USER=$(_env_val POSTGRES_USER)
POSTGRES_DB=$(_env_val POSTGRES_DB)
POSTGRES_PASSWORD=$(_env_val POSTGRES_PASSWORD)
AIRFLOW_DB=$(_env_val AIRFLOW_DB)

# 기본값 설정
: "${POSTGRES_USER:=datauser}"
: "${POSTGRES_DB:=datadb}"
: "${AIRFLOW_DB:=airflowdb}"

PG_CONTAINER="postgres-main"

echo "============================================"
echo "  📦 프로젝트 DB 변경사항 적용"
echo "============================================"
echo ""

# ---- 0. PostgreSQL 컨테이너 확인 ----
echo "0️⃣  PostgreSQL 컨테이너 확인..."
if ! docker ps --format '{{.Names}}' | grep -q "^${PG_CONTAINER}$"; then
    echo "❌ PostgreSQL 컨테이너(${PG_CONTAINER})가 실행 중이 아닙니다."
    echo "   먼저 docker compose up -d postgresql 을 실행해주세요."
    exit 1
fi
echo "   ✅ ${PG_CONTAINER} 실행 중"

# ---- 1. Airflow DB 분리 확인 ----
echo ""
echo "1️⃣  Airflow DB 확인..."
HAS_AIRFLOW_DB=$(docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d postgres -tc \
    "SELECT 1 FROM pg_database WHERE datname='${AIRFLOW_DB}';" | tr -d ' ')
if [ "$HAS_AIRFLOW_DB" = "1" ]; then
    echo "   ✅ airflowdb 이미 존재"
else
    echo "   ⚠️  airflowdb가 없습니다. 생성합니다..."
    docker exec ${PG_CONTAINER} createdb -U ${POSTGRES_USER} ${AIRFLOW_DB}
    echo "   ✅ airflowdb 생성 완료"
fi

# ---- 2. users 테이블 소셜 로그인 컬럼 추가 ----
echo ""
echo "2️⃣  users 테이블 소셜 로그인 컬럼 확인..."

# provider 컬럼 확인
HAS_PROVIDER=$(docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -tc \
    "SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='provider';" | tr -d ' ')

if [ "$HAS_PROVIDER" = "1" ]; then
    echo "   ✅ provider 컬럼 이미 존재"
else
    echo "   ⚠️  소셜 로그인 컬럼 추가 중..."
    docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c "
        ALTER TABLE users ADD COLUMN IF NOT EXISTS provider VARCHAR(20) DEFAULT 'email';
        ALTER TABLE users ADD COLUMN IF NOT EXISTS social_id VARCHAR(255);
        ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_image VARCHAR(512);
        ALTER TABLE users ALTER COLUMN password DROP NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_social ON users(provider, social_id);
    "
    echo "   ✅ 소셜 로그인 컬럼 추가 완료"
fi

# ---- 3. inquiry_board 테이블 생성 ----
echo ""
echo "3️⃣  inquiry_board (문의 게시판) 테이블 확인..."

HAS_INQUIRY=$(docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -tc \
    "SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='inquiry_board';" | tr -d ' ')

if [ "$HAS_INQUIRY" = "1" ]; then
    echo "   ✅ inquiry_board 테이블 이미 존재"
else
    echo "   ⚠️  inquiry_board 테이블 생성 중..."
    docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c "
        CREATE TABLE IF NOT EXISTS inquiry_board (
            inquiry_id BIGSERIAL PRIMARY KEY,
            title VARCHAR(200) NOT NULL,
            content TEXT,
            author_id VARCHAR(50) REFERENCES users(user_id),
            status VARCHAR(20) DEFAULT 'pending',
            answer TEXT,
            answered_by VARCHAR(50) REFERENCES users(user_id),
            answered_at TIMESTAMP,
            view_count INTEGER DEFAULT 0,
            create_dt TIMESTAMP DEFAULT NOW(),
            update_dt TIMESTAMP DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_inquiry_board_author_id ON inquiry_board(author_id);
        CREATE INDEX IF NOT EXISTS idx_inquiry_board_status ON inquiry_board(status);
    "
    echo "   ✅ inquiry_board 테이블 생성 완료"
fi

# ---- 4. 최종 확인 ----
echo ""
echo "4️⃣  최종 테이블 목록 확인..."
TABLE_LIST=$(docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -tc \
    "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;" | tr -d ' ' | grep -v '^$')
TABLE_COUNT=$(echo "$TABLE_LIST" | wc -l | tr -d ' ')

echo "   📊 datadb 테이블 수: ${TABLE_COUNT}개"
echo ""
echo "   테이블 목록:"
echo "$TABLE_LIST" | while read -r tbl; do
    echo "     • $tbl"
done

echo ""
echo "============================================"
echo "  🚀 DB 변경사항 적용 완료!"
echo ""
echo "  적용된 항목:"
echo "    ✅ Airflow DB (airflowdb) 분리"
echo "    ✅ users 소셜 로그인 컬럼"
echo "    ✅ inquiry_board 문의 게시판 테이블"
echo "============================================"
