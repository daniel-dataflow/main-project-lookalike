import os
import re
import aiohttp
import asyncio

import psycopg2
from psycopg2.extras import execute_values


def sanitize_filename(name):
    """파일명으로 사용할 수 없는 문자 제거"""
    return re.sub(r'[<>:"/\\|?*]', '_', name).rstrip()

async def download_image(session, url, folder, filename):
    """이미지 URL에서 파일을 다운로드하여 저장"""
    if not url or url == "N/A" or not url.startswith("http"):
        return None
    
    if not os.path.exists(folder):
        os.makedirs(folder)

    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
            if response.status == 200:
                filepath = os.path.join(folder, filename)
                content = await response.read()
                with open(filepath, 'wb') as f:
                    f.write(content)
                return filepath
    except Exception as e:
        print(f"Failed to download {url}: {e}")
    return None

def get_unique_filename(folder, filename):
    """중복 파일명이 있을 경우 숫자를 붙여 고유하게 만듦"""
    base, ext = os.path.splitext(filename)
    counter = 1
    unique_filename = filename
    while os.path.exists(os.path.join(folder, unique_filename)):
        unique_filename = f"{base}_{counter}{ext}"
        counter += 1
    return unique_filename

def save_to_postgres(product_data):
    """PostgreSQL에 데이터를 저장하거나 이미 있으면 업데이트(Upsert)합니다."""
    # DB 연결 정보 (추후 Airflow Connection으로 대체 권장)
    db_config = {
        "host": "localhost",
        "database": "fashion_db",
        "user": "postgres_user",
        "password": "your_password",
        "port": "5432"
    }
    
    conn = psycopg2.connect(**db_config)
    try:
        with conn.cursor() as cursor:
            # PostgreSQL의 Upsert 구문 (ON CONFLICT)
            sql = """
            INSERT INTO fashion_products (brand, model_code, name, price, description, local_image_path)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (model_code) 
            DO UPDATE SET
                name = EXCLUDED.name,
                price = EXCLUDED.price,
                description = EXCLUDED.description,
                local_image_path = EXCLUDED.local_image_path,
                created_at = CURRENT_TIMESTAMP;
            """
            cursor.execute(sql, (
                product_data['brand'], product_data['model_code'], 
                product_data['name'], product_data['price'], 
                product_data['description'], product_data['local_image_path']
            ))
        conn.commit()
    except Exception as e:
        print(f"PostgreSQL 저장 오류: {e}")
        conn.rollback()
    finally:
        conn.close()