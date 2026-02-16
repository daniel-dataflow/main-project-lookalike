#!/bin/bash
set -e

echo '🔧 DB 초기화 시작...'

# Airflow DB 생성 (존재하지 않을 경우)
if psql -h postgresql -U "$POSTGRES_USER" -d postgres -tc "SELECT 1 FROM pg_database WHERE datname='$AIRFLOW_DB'" | grep -q 1; then
    echo '✅ Airflow DB가 이미 존재합니다'
else
    createdb -h postgresql -U "$POSTGRES_USER" "$AIRFLOW_DB"
    echo '✅ Airflow DB 생성 완료'
fi

# 프로젝트 테이블 생성
echo '📊 프로젝트 테이블 생성 중...'

psql -h postgresql -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<-EOSQL
    -- Users 테이블
    CREATE TABLE IF NOT EXISTS users (
        user_id VARCHAR(50) PRIMARY KEY,
        password VARCHAR(255),
        name VARCHAR(50),
        email VARCHAR(100) UNIQUE,
        role VARCHAR(20) DEFAULT 'USER',
        provider VARCHAR(20) DEFAULT 'email',
        provider_id VARCHAR(255),
        profile_image VARCHAR(512),
        last_login TIMESTAMP DEFAULT NOW(),
        create_dt TIMESTAMP DEFAULT NOW(),
        update_dt TIMESTAMP DEFAULT NOW()
    );

    -- Users 인덱스
    CREATE UNIQUE INDEX IF NOT EXISTS idx_users_social 
        ON users(provider, provider_id);

    -- Inquiry Board 테이블
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

    CREATE INDEX IF NOT EXISTS idx_inquiry_board_author_id 
        ON inquiry_board(author_id);

    -- Comments 테이블
    CREATE TABLE IF NOT EXISTS comments (
        comment_id BIGSERIAL PRIMARY KEY,
        inquiry_board_id BIGINT REFERENCES inquiry_board(inquiry_board_id) ON DELETE CASCADE,
        author_id VARCHAR(50) REFERENCES users(user_id),
        comment_text TEXT,
        create_dt TIMESTAMP DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_comments_inquiry_board_id 
        ON comments(inquiry_board_id);

    -- Products 테이블
    CREATE TABLE IF NOT EXISTS products (
        product_id BIGSERIAL PRIMARY KEY,
        origine_prod_id VARCHAR(50),
        model_code VARCHAR(50),
        prod_name VARCHAR(50),
        base_price INTEGER,
        category_code VARCHAR(50),
        img_hdfs_path VARCHAR(512),
        create_dt TIMESTAMP DEFAULT NOW(),
        update_dt TIMESTAMP DEFAULT NOW()
    );

    -- Naver Prices 테이블
    CREATE TABLE IF NOT EXISTS naver_prices (
        nprice_id BIGSERIAL PRIMARY KEY,
        product_id BIGINT REFERENCES products(product_id),
        rank SMALLINT,
        price INTEGER,
        mall_name VARCHAR(100),
        mall_url VARCHAR(500),
        create_dt TIMESTAMP DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_naver_prices_product_id 
        ON naver_prices(product_id);

    -- Product Features 테이블
    CREATE TABLE IF NOT EXISTS product_features (
        product_id BIGINT PRIMARY KEY REFERENCES products(product_id),
        detected_desc VARCHAR(1000),
        create_dt TIMESTAMP DEFAULT NOW()
    );

    -- Search Logs 테이블
    CREATE TABLE IF NOT EXISTS search_logs (
        log_id BIGSERIAL PRIMARY KEY,
        user_id VARCHAR(50) REFERENCES users(user_id),
        input_img_path VARCHAR(512),
        input_text TEXT,
        applied_category VARCHAR(50),
        nprice_id BIGINT REFERENCES naver_prices(nprice_id),
        create_dt TIMESTAMP DEFAULT NOW(),
        update_dt TIMESTAMP DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_search_logs_user_id 
        ON search_logs(user_id);
    
    CREATE INDEX IF NOT EXISTS idx_search_logs_nprice_id 
        ON search_logs(nprice_id);

EOSQL

echo '✅ 프로젝트 테이블 초기화 완료'
echo '🚀 DB 초기화 완료!'

