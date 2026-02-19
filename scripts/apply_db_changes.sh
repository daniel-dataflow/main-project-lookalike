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
        ALTER TABLE users ADD COLUMN IF NOT EXISTS provider_id VARCHAR(255);
        ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_image VARCHAR(512);
        ALTER TABLE users ALTER COLUMN password DROP NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_social ON users(provider, provider_id);
    "
    echo "   ✅ 소셜 로그인 컬럼 추가 완료"
fi

# ---- 3. inquiry_board 테이블 정리 (posts → inquiry_board 마이그레이션) ----
echo ""
echo "3️⃣  inquiry_board (게시판) 테이블 마이그레이션..."

# 3-1. 기존 답변 내장형 inquiry_board 테이블 삭제 (answer 컬럼이 있는 경우)
HAS_OLD_INQUIRY=$(docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -tc \
    "SELECT 1 FROM information_schema.columns WHERE table_name='inquiry_board' AND column_name='answer';" | tr -d ' ')

if [ "$HAS_OLD_INQUIRY" = "1" ]; then
    echo "   ⚠️  기존 답변 내장형 inquiry_board 발견 → 삭제합니다..."
    docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c \
        "DROP TABLE IF EXISTS inquiry_board CASCADE;"
    echo "   ✅ 기존 inquiry_board 삭제 완료"
fi

# 3-2. posts 테이블이 있으면 inquiry_board로 이름 변경
HAS_POSTS=$(docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -tc \
    "SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='posts';" | tr -d ' ')

if [ "$HAS_POSTS" = "1" ]; then
    echo "   ⚠️  posts 테이블 발견 → inquiry_board로 이름 변경..."
    docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c "
        ALTER TABLE posts RENAME TO inquiry_board;
        -- comments FK 제약조건이 자동으로 따라가지만 인덱스 이름도 정리
        ALTER INDEX IF EXISTS posts_pkey RENAME TO inquiry_board_pkey;
    "
    echo "   ✅ posts → inquiry_board 이름 변경 완료"
fi

# 3-2b. post_id → inquiry_board_id 컬럼명 변경
HAS_POST_ID_COL=$(docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -tc \
    "SELECT 1 FROM information_schema.columns WHERE table_name='inquiry_board' AND column_name='post_id';" | tr -d ' ')

if [ "$HAS_POST_ID_COL" = "1" ]; then
    echo "   ⚠️  inquiry_board.post_id → inquiry_board_id 컬럼명 변경..."
    docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c "
        ALTER TABLE inquiry_board RENAME COLUMN post_id TO inquiry_board_id;
    "
    echo "   ✅ inquiry_board.post_id → inquiry_board_id 변경 완료"
fi

HAS_COMMENTS_POST_ID=$(docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -tc \
    "SELECT 1 FROM information_schema.columns WHERE table_name='comments' AND column_name='post_id';" | tr -d ' ')

if [ "$HAS_COMMENTS_POST_ID" = "1" ]; then
    echo "   ⚠️  comments.post_id → inquiry_board_id 컬럼명 변경..."
    docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c "
        ALTER TABLE comments RENAME COLUMN post_id TO inquiry_board_id;
    "
    echo "   ✅ comments.post_id → inquiry_board_id 변경 완료"
fi

# 3-3. inquiry_board가 없으면 새로 생성 (첫 설치)
HAS_INQUIRY=$(docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -tc \
    "SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='inquiry_board';" | tr -d ' ')

if [ "$HAS_INQUIRY" = "1" ]; then
    echo "   ✅ inquiry_board 테이블 존재 확인"
else
    echo "   ⚠️  inquiry_board 테이블 생성 중..."
    docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c "
        CREATE TABLE IF NOT EXISTS inquiry_board (
            inquiry_board_id BIGSERIAL PRIMARY KEY,
            title VARCHAR(200) NOT NULL,
            content TEXT,
            author_id VARCHAR(50) REFERENCES users(user_id),
            view_count INTEGER DEFAULT 0,
            is_notice BOOLEAN DEFAULT FALSE,
            create_dt TIMESTAMP DEFAULT NOW(),
            update_dt TIMESTAMP DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_inquiry_board_author_id ON inquiry_board(author_id);
    "
    echo "   ✅ inquiry_board 테이블 생성 완료"
fi

# 3-4. comments 테이블 확인 (없으면 생성)
HAS_COMMENTS=$(docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -tc \
    "SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='comments';" | tr -d ' ')

if [ "$HAS_COMMENTS" = "1" ]; then
    echo "   ✅ comments 테이블 이미 존재"
else
    echo "   ⚠️  comments 테이블 생성 중..."
    docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c "
        CREATE TABLE IF NOT EXISTS comments (
            comment_id BIGSERIAL PRIMARY KEY,
            inquiry_board_id BIGINT REFERENCES inquiry_board(inquiry_board_id) ON DELETE CASCADE,
            author_id VARCHAR(50) REFERENCES users(user_id),
            comment_text TEXT,
            create_dt TIMESTAMP DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_comments_inquiry_board_id ON comments(inquiry_board_id);
    "
    echo "   ✅ comments 테이블 생성 완료"
fi

# ---- 4. search_logs 테이블 확장 + search_results 테이블 생성 ----
echo ""
echo "4️⃣  search_logs 테이블 확장 및 search_results 테이블 생성..."

# 4-1. search_logs 컬럼 추가
HAS_THUMBNAIL_PATH=$(docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -tc \
    "SELECT 1 FROM information_schema.columns WHERE table_name='search_logs' AND column_name='thumbnail_path';" | tr -d ' ')

if [ "$HAS_THUMBNAIL_PATH" = "1" ]; then
    echo "   ✅ search_logs 확장 컬럼 이미 존재"
else
    echo "   ⚠️  search_logs 확장 컬럼 추가 중..."
    docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c "
        ALTER TABLE search_logs
            ADD COLUMN IF NOT EXISTS thumbnail_path VARCHAR(512),
            ADD COLUMN IF NOT EXISTS image_size INTEGER,
            ADD COLUMN IF NOT EXISTS image_width INTEGER,
            ADD COLUMN IF NOT EXISTS image_height INTEGER,
            ADD COLUMN IF NOT EXISTS search_status VARCHAR(20) DEFAULT 'completed',
            ADD COLUMN IF NOT EXISTS result_count INTEGER DEFAULT 0;

        CREATE INDEX IF NOT EXISTS idx_search_logs_create_dt
            ON search_logs(create_dt DESC);
    "
    echo "   ✅ search_logs 확장 컬럼 추가 완료"
fi

# 4-2. search_results 테이블 생성
# ※ 비정규화 구조: product_id FK 대신 상품 정보를 직접 저장 (검색 당시 스냅샷 보존)
HAS_SEARCH_RESULTS=$(docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -tc \
    "SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='search_results';" | tr -d ' ')

if [ "$HAS_SEARCH_RESULTS" = "1" ]; then
    echo "   ✅ search_results 테이블 이미 존재"
else
    echo "   ⚠️  search_results 테이블 생성 중..."
    docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c "
        CREATE TABLE IF NOT EXISTS search_results (
            result_id BIGSERIAL PRIMARY KEY,
            log_id BIGINT REFERENCES search_logs(log_id) ON DELETE CASCADE,
            product_name VARCHAR(200),
            brand VARCHAR(100),
            price INTEGER,
            image_url VARCHAR(512),
            mall_name VARCHAR(100),
            mall_url VARCHAR(500),
            rank SMALLINT,
            create_dt TIMESTAMP DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_search_results_log_id
            ON search_results(log_id);
    "
    echo "   ✅ search_results 테이블 생성 완료"
fi

# ---- 5. 최종 확인 ----
echo ""
echo "5️⃣  최종 테이블 목록 확인..."
TABLE_LIST=$(docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -tc \
    "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;" | tr -d ' ' | grep -v '^$')
TABLE_COUNT=$(echo "$TABLE_LIST" | wc -l | tr -d ' ')

echo "   📊 datadb 테이블 수: ${TABLE_COUNT}개"
echo ""
echo "   테이블 목록:"
echo "$TABLE_LIST" | while read -r tbl; do
    echo "     • $tbl"
done

# ---- 5. products 테이블에 brand_name 컬럼 추가 ----
echo ""
echo "5️⃣  products 테이블에 brand_name 컬럼 추가..."

HAS_BRAND_NAME=$(docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -tc \
    "SELECT 1 FROM information_schema.columns WHERE table_name='products' AND column_name='brand_name';" | tr -d ' ')

if [ "$HAS_BRAND_NAME" = "1" ]; then
    echo "   ✅ brand_name 컬럼 이미 존재"
else
    echo "   ⚠️  brand_name 컬럼 추가 중..."
    docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c "
        ALTER TABLE products
            ADD COLUMN IF NOT EXISTS brand_name VARCHAR(100);
    "
    echo "   ✅ brand_name 컬럼 추가 완료"
fi

echo ""
echo "============================================"
echo "  🚀 DB 변경사항 적용 완료!"
echo ""
echo "  적용된 항목:"
echo "    ✅ Airflow DB (airflowdb) 분리"
echo "    ✅ users 소셜 로그인 컬럼"
echo "    ✅ inquiry_board 게시판 테이블 (posts → inquiry_board 마이그레이션)"
echo "    ✅ comments 댓글 테이블"
echo "    ✅ search_logs 확장 (썸네일, 메타데이터)"
echo "    ✅ search_results 테이블"
echo "    ✅ products 테이블 brand_name 컬럼"
echo "============================================"

# ---- 6. 불필요한 컬럼 삭제 (origine_prod_id) ----
echo ""
echo "6️⃣  불필요한 컬럼 삭제 (origine_prod_id)..."

# products.origine_prod_id 삭제
HAS_ORIGINE=$(docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -tc \
    "SELECT 1 FROM information_schema.columns WHERE table_name='products' AND column_name='origine_prod_id';" | tr -d ' ')

if [ "$HAS_ORIGINE" = "1" ]; then
    echo "   ⚠️  products.origine_prod_id 컬럼 삭제 중..."
    docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c \
        "ALTER TABLE products DROP COLUMN IF EXISTS origine_prod_id;"
    echo "   ✅ products.origine_prod_id 컬럼 삭제 완료"
else
    echo "   ⏭️  products.origine_prod_id 컬럼 이미 삭제됨 (스킵)"
fi

# ---- 7. social_id를 provider_id로 컬럼명 변경 ----
echo ""
echo "7️⃣  users.social_id를 provider_id로 컬럼명 변경..."

HAS_SOCIAL_ID=$(docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -tc \
    "SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='social_id';" | tr -d ' ')

if [ "$HAS_SOCIAL_ID" = "1" ]; then
    echo "   ⚠️  social_id → provider_id 컬럼명 변경 중..."
    docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c "
        DROP INDEX IF EXISTS idx_users_social;
        ALTER TABLE users RENAME COLUMN social_id TO provider_id;
        CREATE UNIQUE INDEX idx_users_social ON users(provider, provider_id);
    "
    echo "   ✅ social_id → provider_id 컬럼명 변경 완료"
else
    echo "   ⏭️  이미 provider_id로 되어있음 (스킵)"
fi

echo ""
echo "============================================"
echo "  ✅ 최종 완료!"
echo "============================================"

# ---- 8. recent_views, likes 테이블 생성 ----
echo ""
echo "8️⃣  최근 본 상품 및 좋아요 테이블 생성..."

HAS_RECENT_VIEWS=$(docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -tc \
    "SELECT 1 FROM information_schema.tables WHERE table_name='recent_views';" | tr -d ' ')

if [ "$HAS_RECENT_VIEWS" != "1" ]; then
    echo "   ⚠️  recent_views, likes 테이블 생성 중..."
    docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c "
        CREATE TABLE recent_views (
            view_id BIGSERIAL PRIMARY KEY,
            user_id VARCHAR(50) REFERENCES users(user_id) ON DELETE CASCADE,
            product_id BIGINT REFERENCES products(product_id) ON DELETE CASCADE,
            view_dt TIMESTAMP DEFAULT NOW()
        );
        CREATE INDEX idx_recent_views_user ON recent_views(user_id, view_dt DESC);
        CREATE UNIQUE INDEX idx_recent_views_unique ON recent_views(user_id, product_id);

        CREATE TABLE likes (
            like_id BIGSERIAL PRIMARY KEY,
            user_id VARCHAR(50) REFERENCES users(user_id) ON DELETE CASCADE,
            product_id BIGINT REFERENCES products(product_id) ON DELETE CASCADE,
            create_dt TIMESTAMP DEFAULT NOW()
        );
        CREATE INDEX idx_likes_user ON likes(user_id, create_dt DESC);
        CREATE UNIQUE INDEX idx_likes_unique ON likes(user_id, product_id);
    "
    echo "   ✅ recent_views, likes 테이블 생성 완료"
else
    echo "   ⏭️  recent_views, likes 테이블 이미 존재 (스킵)"
fi

echo ""

# 9. products 테이블 gender 컬럼 추가
echo "9️⃣  products 테이블 gender 컬럼 추가..."
HAS_GENDER=$(docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -tc \
    "SELECT 1 FROM information_schema.columns WHERE table_name='products' AND column_name='gender';" | tr -d ' ')

if [ "$HAS_GENDER" = "1" ]; then
    echo "   ⏭️  products.gender 컬럼 이미 존재 (스킵)"
else
    echo "   ⚠️  products.gender 컬럼 추가 중..."
    docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c "
        ALTER TABLE products ADD COLUMN gender VARCHAR(10);
        COMMENT ON COLUMN products.gender IS '성별 구분: 남자 / 여자 / NULL(공용)';
    "
    echo "   ✅ products.gender 컬럼 추가 완료"
fi

echo ""

# 9-1. search_logs 테이블 gender 컬럼 추가
echo "🔟  search_logs 테이블 gender 컬럼 추가..."
HAS_LOG_GENDER=$(docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -tc \
    "SELECT 1 FROM information_schema.columns WHERE table_name='search_logs' AND column_name='gender';" | tr -d ' ')

if [ "$HAS_LOG_GENDER" = "1" ]; then
    echo "   ⏭️  search_logs.gender 컬럼 이미 존재 (스킵)"
else
    echo "   ⚠️  search_logs.gender 컬럼 추가 중..."
    docker exec ${PG_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c "
        ALTER TABLE search_logs ADD COLUMN gender VARCHAR(10);
        COMMENT ON COLUMN search_logs.gender IS '검색 시 선택한 성별: 남자 / 여자';
    "
    echo "   ✅ search_logs.gender 컬럼 추가 완료"
fi

echo ""
echo "============================================"
echo "  ✅ 모든 DB 변경사항 적용 완료!"
echo ""
echo "  적용된 항목:"
echo "    ✅ Airflow DB (airflowdb) 분리"
echo "    ✅ users 소셜 로그인 컬럼 (provider, provider_id, profile_image)"
echo "    ✅ inquiry_board 게시판 테이블 (posts → 마이그레이션)"
echo "    ✅ comments 댓글 테이블"
echo "    ✅ search_logs 확장 (thumbnail_path, image_size/width/height, search_status, result_count)"
echo "    ✅ search_logs gender 컬럼 추가"
echo "    ✅ search_results 테이블 (비정규화: product_name/brand/price/image_url/mall_name/mall_url/rank)"
echo "    ✅ products 테이블 brand_name 컬럼 추가"
echo "    ✅ products 테이블 origine_prod_id 컬럼 삭제"
echo "    ✅ social_id → provider_id 컬럼명 변경"
echo "    ✅ recent_views, likes 테이블 생성"
echo "    ✅ products 테이블 gender 컬럼 추가"
echo "============================================"
