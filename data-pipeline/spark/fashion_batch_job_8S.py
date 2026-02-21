from pyspark.sql import SparkSession
from pyspark.sql.functions import (lit, col, current_timestamp, concat, format_string, 
                                   input_file_name, regexp_extract, lower, concat_ws, element_at, 
                                   coalesce, to_json, row_number)
from pyspark.sql.window import Window
import psycopg2
import datetime
import os
import sys
import requests
from pytz import timezone

# 스파크 워커 내 파이썬 버전 에러 방지
os.environ["PYSPARK_PYTHON_VERSION_CHECK"] = "0"

# --- [1. 설정 정보] ---
MONGO_IP = "mongo-main"
BRAND_NAME = "8seconds"
BRAND_PREFIX = "8S"

kst = timezone('Asia/Seoul')
TARGET_DATE = datetime.datetime.now(kst).strftime("%Y%m%d")

PG_HOST = "postgres-main"  
PG_DB = "datadb"       
PG_USER = "datauser"
PG_PASS = "DataPass2026!"  

MONGO_USER = "datauser"
MONGO_PASS = "DataPass2026!"

HDFS_BASE = "hdfs://namenode-main:9000"
RAW_PATH = f"/raw/{BRAND_NAME}/{TARGET_DATE}"
IMAGE_DIR = f"/raw/{BRAND_NAME}/image"

# WebHDFS 설정
WEBHDFS_HOST = "namenode-main"
WEBHDFS_PORT = "9870"
WEBHDFS_USER = "root"

spark = SparkSession.builder \
    .appName("FashionBatchJob8S") \
    .config("spark.mongodb.write.connection.uri", f"mongodb://{MONGO_USER}:{MONGO_PASS}@{MONGO_IP}:27017/{PG_DB}?authSource=admin") \
    .getOrCreate()

# --- [2. PostgreSQL 시퀀스 관리] ---
try:
    # brand_sequences 테이블이 없으면 자동 생성
    init_conn = psycopg2.connect(host=PG_HOST, database=PG_DB, user=PG_USER, password=PG_PASS)
    init_cur = init_conn.cursor()
    init_cur.execute("""
        CREATE TABLE IF NOT EXISTS brand_sequences (
            brand_name VARCHAR(50) PRIMARY KEY,
            last_seq INTEGER DEFAULT 0
        );
    """)
    init_conn.commit()
    
    # 현재 시퀀스 조회
    seq_df = spark.read.format("jdbc") \
        .option("url", f"jdbc:postgresql://{PG_HOST}:5432/{PG_DB}") \
        .option("dbtable", "brand_sequences") \
        .option("user", PG_USER) \
        .option("password", PG_PASS) \
        .option("driver", "org.postgresql.Driver") \
        .load()

    row = seq_df.filter(col("brand_name") == BRAND_NAME.upper()).select("last_seq").collect()

    if not row:
        print(f"✨ {BRAND_NAME} sequence not found. Registering...")
        init_cur.execute("INSERT INTO brand_sequences (brand_name, last_seq) VALUES (%s, 0)", (BRAND_NAME.upper(),))
        init_conn.commit()
        start_seq = 1
    else:
        start_seq = row[0]['last_seq'] + 1
    init_cur.close()
    init_conn.close()

except Exception as e:
    print(f"⚠️ Sequence check failed: {e}. Starting from 1.")
    start_seq = 1

print(f"🚀 Job Start | Brand: {BRAND_NAME} | Seq: {start_seq} | Date: {TARGET_DATE}")

# --- [3. ETL 로직] ---
input_path = f"{HDFS_BASE}{RAW_PATH}/*.json"
raw_df = spark.read.option("multiLine", "true") \
                   .option("inferSchema", "true") \
                   .json(input_path) \
                   .withColumn("file_path", input_file_name())

windowSpec = Window.partitionBy(lit(BRAND_NAME)).orderBy(col("goodsNo"))

# 데이터 처리 (파일명 생성 및 HDFS 경로 지정)
processed_df = raw_df.withColumn("idx", row_number().over(windowSpec)) \
    .withColumn("product_id", format_string(f"{BRAND_PREFIX}%04d", col("idx").cast("int") + start_seq - 1)) \
    .withColumn("target_img_url", 
        coalesce(
            element_at(col("goodsImages"), 26), # 1순위: 고화질
            element_at(col("goodsImages"), 2)   # 2순위: 썸네일
        )
    ) \
    .withColumn("gender", lower(regexp_extract(col("file_path"), r"8seconds_([^_]+)_", 1))) \
    .withColumn("sub_category", lower(regexp_extract(col("file_path"), r"8seconds_[^_]+_([^_]+)_", 1))) \
    .withColumn("category_code", concat(col("gender"), lit("_"), col("sub_category"))) \
    .withColumn("new_filename", concat_ws("_", lit(BRAND_NAME.lower()), col("gender"), col("sub_category"), col("goodsNo"))) \
    .withColumn("img_hdfs_path", concat(lit(IMAGE_DIR), lit("/"), col("new_filename"), lit(".jpg")))

processed_df.cache()
total_count = processed_df.count()

if total_count == 0:
    print("❌ 수집된 데이터가 없습니다. 잡을 종료합니다.")
    spark.stop()
    sys.exit(0)

print(f"📊 처리할 데이터: {total_count} 건")

# --- [4. PostgreSQL 적재] ---
pg_data = processed_df.select(
    col("product_id"),
    col("goodsNo").alias("model_code"),
    lit(BRAND_NAME.upper()).alias("brand"),
    col("goodsNm").alias("product_name"),
    col("category_code").alias("category"),
    coalesce(col("price").cast("int"), lit(0)).alias("price"),
    col("img_hdfs_path").alias("local_image_path"),
    current_timestamp().alias("created_at")
)

pg_data.write.format("jdbc") \
    .option("url", f"jdbc:postgresql://{PG_HOST}:5432/{PG_DB}") \
    .option("dbtable", "fashion_products") \
    .option("user", PG_USER) \
    .option("password", PG_PASS) \
    .option("driver", "org.postgresql.Driver") \
    .mode("append") \
    .save()

# --- [5. MongoDB 적재] ---
mongo_data = processed_df.select(
    col("product_id"),
    col("goodsNo").alias("model_code"),
    lit(BRAND_NAME.upper()).alias("brand_name"),
    col("goodsNm").alias("prod_name"),
    to_json(col("goodsMaterial")).alias("detail_desc"),
    col("img_hdfs_path"),
    col("goodsImages").alias("all_images"),
    current_timestamp().alias("create_dt")
)

mongo_data.write.format("mongodb") \
    .option("spark.mongodb.write.database", PG_DB) \
    .option("spark.mongodb.write.collection", "fashion_metadata") \
    .mode("append") \
    .save()

# --- [6. 이미지 다운로드 및 WebHDFS 저장] ---

def upload_to_hdfs_via_webhdfs(local_data, hdfs_path, filename):
    """WebHDFS REST API를 사용하여 파일 업로드"""
    try:
        # 1. CREATE 요청 (리다이렉트 URL 받기)
        create_url = f"http://{WEBHDFS_HOST}:{WEBHDFS_PORT}/webhdfs/v1{hdfs_path}/{filename}?op=CREATE&overwrite=true&user.name={WEBHDFS_USER}"
        
        response = requests.put(create_url, allow_redirects=False)
        
        if response.status_code == 307:  # Temporary Redirect
            # 2. DataNode로 리다이렉트된 URL에 PUT 요청
            datanode_url = response.headers['Location']
            put_response = requests.put(datanode_url, data=local_data)
            
            if put_response.status_code in [200, 201]:
                return True
            else:
                print(f"❌ HDFS 업로드 실패 (DataNode): {put_response.status_code}")
                return False
        else:
            print(f"❌ HDFS CREATE 요청 실패: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ HDFS 업로드 예외: {e}")
        return False

# 이미지 디렉토리 생성 (WebHDFS mkdir)
try:
    mkdir_url = f"http://{WEBHDFS_HOST}:{WEBHDFS_PORT}/webhdfs/v1{IMAGE_DIR}?op=MKDIRS&user.name={WEBHDFS_USER}"
    requests.put(mkdir_url)
except:
    pass

image_list = processed_df.select("target_img_url", "new_filename", "goodsNo").collect()
print(f"📸 이미지 다운로드 시작: {len(image_list)} 건")

success_count = 0
fail_count = 0

for r in image_list:
    if r.target_img_url and r.new_filename:
        filename = f"{r.new_filename}.jpg"
        try:
            # 1. 쇼핑몰 서버에서 이미지 다운로드 (가짜 브라우저 헤더 필수)
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            response = requests.get(r.target_img_url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                # 2. 다운받은 이미지를 WebHDFS를 통해 하둡으로 전송
                if upload_to_hdfs_via_webhdfs(response.content, IMAGE_DIR, filename):
                    success_count += 1
                else:
                    fail_count += 1
                    print(f"❌ HDFS 업로드 실패: {r.goodsNo}")
            else:
                fail_count += 1
                print(f"❌ 이미지 다운로드 실패 ({r.goodsNo}): HTTP {response.status_code}")
                
        except Exception as e:
            fail_count += 1
            print(f"❌ 이미지 처리 예외 발생 ({r.goodsNo}): {e}")

print(f"📸 이미지 처리 완료: 성공 {success_count} / 실패 {fail_count}")

# --- [7. 시퀀스 업데이트] ---
try:
    new_last_seq = start_seq + total_count - 1
    conn = psycopg2.connect(host=PG_HOST, database=PG_DB, user=PG_USER, password=PG_PASS)
    cur = conn.cursor()
    cur.execute("UPDATE brand_sequences SET last_seq = %s WHERE brand_name = %s", (new_last_seq, BRAND_NAME.upper()))
    conn.commit()
    cur.close()
    conn.close()
    print(f"✅ 시퀀스 업데이트 완료: {new_last_seq}")
except Exception as e:
    print(f"❌ 시퀀스 업데이트 오류: {e}")

print(f"🏁 {BRAND_NAME.upper()} 잡 완료! 총 {total_count} 건 처리됨.")
spark.stop()