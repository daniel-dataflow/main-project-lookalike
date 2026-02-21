from pyspark.sql import SparkSession
from pyspark.sql.functions import (lit, col, current_timestamp, concat, format_string, 
                                   input_file_name, regexp_extract, lower, concat_ws, element_at, 
                                   coalesce, to_json, row_number)
from pyspark.sql.window import Window
import subprocess
import psycopg2
import datetime
import os
import urllib.request
from pytz import timezone
import sys

# 스파크 워커 내 파이썬 버전 에러 방지
os.environ["PYSPARK_PYTHON_VERSION_CHECK"] = "0"

# --- [1. 설정 정보] ---
MONGO_IP = "mongodb"
BRAND_NAME = "8seconds"
BRAND_PREFIX = "8S"

kst = timezone('Asia/Seoul')
TARGET_DATE = datetime.datetime.now(kst).strftime("%Y%m%d")

PG_HOST = "postgresql"  # 컨테이너 이름에 맞게 수정
PG_DB = "datadb"       
PG_USER = "datauser"
PG_PASS = "DataPass2026!"  

MONGO_USER = "datauser"
MONGO_PASS = "DataPass2026!"

HDFS_BASE = "hdfs://namenode:9000"
RAW_PATH = f"/raw/{BRAND_NAME}/{TARGET_DATE}"
IMAGE_DIR = f"/raw/{BRAND_NAME}/image"
CONTAINER_NAME = "namenode-main"

spark = SparkSession.builder \
    .appName("FashionBatchJob8S") \
    .config("spark.mongodb.write.connection.uri", f"mongodb://{MONGO_USER}:{MONGO_PASS}@{MONGO_IP}:27017/{PG_DB}?authSource=admin") \
    .getOrCreate()

# --- [2. PostgreSQL 시퀀스 관리] ---
try:
    # brand_sequences 테이블이 없으면 자동 생성 (코드 레벨 자동화)
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

# 2번째 이미지 주소를 가져오되, 없으면 1번째 주소라도 가져오도록 coalesce 설정
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
    .withColumn("img_hdfs_path", concat(lit(IMAGE_DIR), lit("/"), col("new_filename"), lit(".jpg"))) # 👈 바뀐 파일명 적용

processed_df.cache()
total_count = processed_df.count()
if total_count == 0:
    print("❌ 수집된 데이터가 없습니다. 잡을 종료합니다.")
    spark.stop()
    sys.exit(0)

# --- [4. PostgreSQL 적재] ---
# 테이블명을 기존에 확인하셨던 fashion_products로 맞췄습니다.
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
    col("goodsImages").alias("all_images"), # 모든 이미지 리스트 보존
    current_timestamp().alias("create_dt")
)

mongo_data.write.format("mongodb") \
    .option("spark.mongodb.write.database", PG_DB) \
    .option("spark.mongodb.write.collection", "fashion_metadata") \
    .mode("append") \
    .save()

# --- [6. 이미지 다운로드 및 HDFS 저장] ---
subprocess.run(f"docker exec -i {CONTAINER_NAME} hdfs dfs -mkdir -p {IMAGE_DIR}", shell=True)

image_list = processed_df.select("target_img_url", "new_filename", "goodsNo").collect()

print(f"📸 이미지 다운로드 시작: {len(image_list)} 건")
for r in image_list:
    if r.target_img_url and r.new_filename:
        # HDFS 타겟 경로에 우리가 만든 새 파일명을 넣습니다.
        hdfs_target_path = f"{IMAGE_DIR}/{r.new_filename}.jpg"
        try:
            req = urllib.request.Request(r.target_img_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                img_data = response.read()
            
            # HDFS 전송
            cmd = f"docker exec -i {CONTAINER_NAME} hdfs dfs -put -f - {hdfs_target_path}"
            proc = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE)
            proc.communicate(input=img_data)
        except Exception as e:
            print(f"❌ 이미지 실패 ({r.goodsNo}): {e}")
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