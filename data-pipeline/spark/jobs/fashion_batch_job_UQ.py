# fashion_batch_job_UQ.py

from pyspark.sql import SparkSession
from pyspark.sql.functions import (lit, col, element_at, current_timestamp, concat, format_string, 
                                   input_file_name, regexp_extract, lower, 
                                   coalesce, to_json, row_number, explode)
from pyspark.sql.window import Window
import subprocess
import datetime
import psycopg2
# 26.2.21
import requests
from hdfs import InsecureClient

# --- [1. 설정 정보] ---
BRAND_NAME = "uniqlo"
BRAND_PREFIX = "UQ"
TARGET_DATE = datetime.datetime.now().strftime("%Y%m%d")
#TARGET_DATE = "20260210" 

import os

PG_HOST = os.environ.get("POSTGRES_HOST", "postgres-main")
PG_DB = os.environ.get("POSTGRES_DB", "datadb")
PG_USER = os.environ.get("POSTGRES_USER", "datauser")
PG_PASS = os.environ.get("POSTGRES_PASSWORD", "")

MONGO_USER = os.environ.get("MONGODB_USER", "datauser")
MONGO_PASS = os.environ.get("MONGODB_PASSWORD", "")
MONGO_URI = os.environ.get("MONGO_URI", f"mongodb://{MONGO_USER}:{MONGO_PASS}@mongo-main:27017")
HDFS_BASE = "hdfs://namenode-main:9000"
HDFS_WEB_URL = "http://namenode-main:9870"
RAW_PATH = f"/raw/{BRAND_NAME}/{TARGET_DATE}"
IMAGE_DIR = f"/raw/{BRAND_NAME}/image"
CONTAINER_NAME = "namenode-main"

spark = SparkSession.builder \
    .appName("FashionBatchJobUniqlo") \
    .config("spark.jars.packages", "org.postgresql:postgresql:42.6.0,org.mongodb.spark:mongo-spark-connector_2.12:10.1.1") \
    .getOrCreate()

# --- [2. PostgreSQL에서 현재 시퀀스 조회] ---
seq_df = spark.read.format("jdbc") \
    .option("url", f"jdbc:postgresql://{PG_HOST}:5432/{PG_DB}") \
    .option("dbtable", "brand_sequences") \
    .option("user", PG_USER) \
    .option("password", PG_PASS) \
    .option("driver", "org.postgresql.Driver") \
    .load()

row = seq_df.filter(col("brand_name") == BRAND_NAME.upper()).select("last_seq").collect()
# 26.2.18
if not row:
    print(f"✨ {BRAND_NAME} sequence not found. Registering new brand in DB...")
    # 1. 새 데이터를 담은 DF 생성
    new_seq_data = [(BRAND_NAME.upper(), 0)]
    new_seq_df = spark.createDataFrame(new_seq_data, ["brand_name", "last_seq"])
    
    # 2. DB에 실제 INSERT 수행
    new_seq_df.write.format("jdbc") \
        .option("url", f"jdbc:postgresql://{PG_HOST}:5432/{PG_DB}") \
        .option("dbtable", "brand_sequences") \
        .option("user", PG_USER) \
        .option("password", PG_PASS) \
        .option("driver", "org.postgresql.Driver") \
        .mode("append") \
        .save()
    
    start_seq = 1
else:
    start_seq = row[0]['last_seq'] + 1

print(f"🚀 Starting {BRAND_NAME.upper()} job from sequence: {start_seq}")

# --- [3. ETL 로직] ---
input_path = f"{HDFS_BASE}{RAW_PATH}/*.json"
raw_df = spark.read.option("multiLine", "true").json(input_path) \
    .withColumn("file_path", input_file_name())

windowSpec = Window.partitionBy(lit(BRAND_NAME)).orderBy(col("goodsNo"))

# [수정 포인트 1]: 파일명 파싱을 명확히 하고, 정규식 패턴에 맞게 조합
# JSON 파일명 구조(예상): uniqlo_men_outer_..._...json
processed_df = raw_df.withColumn("idx", row_number().over(windowSpec)) \
    .withColumn("product_id", format_string(f"{BRAND_PREFIX}%04d", col("idx").cast("int") + start_seq - 1)) \
    .withColumn("gender", regexp_extract(col("file_path"), r"uniqlo_([^_]+)_", 1)) \
    .withColumn("sub_category", regexp_extract(col("file_path"), r"uniqlo_[^_]+_([^_]+)_", 1)) \
    .withColumn("category_code", concat(lower(col("gender")), lit("_"), lower(col("sub_category"))))

# ✅ 정규식에 맞는 이미지 파일명 조합: 브랜드(uniqlo)_성별_카테고리_상품코드.jpg
# YOLO 정규식: r"^(?P<brand>[^_]+)_(?P<gender>Men|Women)_(?P<category>Top|Bottom|Outer)_(?P<product_code>[^_]+)(?:_.*)?$"
processed_df = processed_df.withColumn(
    "image_filename", 
    concat(
        lit(BRAND_NAME), lit("_"), 
        col("gender"), lit("_"), 
        col("sub_category"), lit("_"), 
        col("goodsNo"), lit(".jpg")
    )
)

# HDFS 경로 업데이트
processed_df = processed_df.withColumn(
    "img_hdfs_path", 
    concat(lit(IMAGE_DIR), lit("/"), col("image_filename"))
)

processed_df.cache()
total_count = processed_df.count()

# --- [4. PostgreSQL 적재] ---
pg_data = processed_df.select(
    col("product_id"),
    col("goodsNo").alias("model_code"),
    lit(BRAND_NAME.upper()).alias("brand_name"),
    col("goodsNm").alias("prod_name"),
    col("category_code"),
    coalesce(col("price").cast("int"), lit(0)).alias("base_price"),
    col("img_hdfs_path"),
    col("image_filename"),
    col("url").alias("product_url"),
    current_timestamp().alias("create_dt"),
    current_timestamp().alias("update_dt")
)

pg_data.write.format("jdbc") \
    .option("url", f"jdbc:postgresql://{PG_HOST}:5432/{PG_DB}") \
    .option("dbtable", "products") \
    .option("user", PG_USER) \
    .option("password", PG_PASS) \
    .option("driver", "org.postgresql.Driver") \
    .mode("append") \
    .save()
print("✅ PostgreSQL 적재 완료")

# --- [5. MongoDB 적재] ---
mongo_data = processed_df.select(
    col("product_id"),
    col("goodsNo").alias("model_code"),
    lit(BRAND_NAME.upper()).alias("brand_name"),
    col("goodsNm").alias("prod_name"),
    to_json(col("goodsMaterial")).alias("detail_desc"), 
    coalesce(col("price").cast("int"), lit(0)).alias("base_price"),
    col("img_hdfs_path"),
    current_timestamp().alias("create_dt")
)

mongo_data.write.format("mongodb") \
    .option("spark.mongodb.write.connection.uri", MONGO_URI) \
    .option("authSource", "admin") \
    .option("database", "datadb") \
    .option("collection", "product_details") \
    .mode("append") \
    .save()
print("✅ MongoDB 적재 완료")

# --- [6. 이미지 처리] ---
try:
    hdfs_client = InsecureClient(HDFS_WEB_URL, user="root")
    hdfs_client.makedirs(IMAGE_DIR)
    
    # [수정 포인트 2]: 파일 다운로드 시 image_filename 사용
    image_list = processed_df.select(
        element_at(col("goodsImages"), 1).alias("main_img"), 
        col("image_filename"), 
        col("product_id")
    ).collect()
    
    print(f"📸 이미지 다운로드 및 HDFS 직접 전송 시작 (총 {len(image_list)}건)...")
    success_img = 0
    for r in image_list:
        if r.main_img:
            # 새로 생성한 image_filename으로 HDFS 경로 설정
            hdfs_target_path = f"{IMAGE_DIR}/{r.image_filename}"
            try:
                img_content = requests.get(r.main_img, timeout=10).content
                hdfs_client.write(hdfs_target_path, data=img_content, overwrite=True)
                success_img += 1
            except Exception as e:
                print(f"❌ {r.product_id} 저장 실패: {e}")
    print(f"✅ 이미지 처리 완료: {success_img}건 저장 성공")

except Exception as e:
    print(f"🚨 하둡 클라이언트 연결 실패: {e}")

# --- [7. 시퀀스 업데이트] ---
try:
    new_last_seq = start_seq + total_count - 1
    conn = psycopg2.connect(host=PG_HOST, database=PG_DB, user=PG_USER, password=PG_PASS)
    cur = conn.cursor()
    cur.execute("UPDATE brand_sequences SET last_seq = %s WHERE brand_name = %s", 
                (new_last_seq, BRAND_NAME.upper()))
    conn.commit()
    print(f"✅ brand_sequences 업데이트 완료: {BRAND_NAME.upper()} -> {new_last_seq}")
except Exception as e:
    print(f"❌ 시퀀스 업데이트 오류: {e}")
finally:
    if 'cur' in locals(): cur.close()
    if 'conn' in locals(): conn.close()

print(f"🏁 {BRAND_NAME.upper()} 작업 성공적으로 종료 ({total_count}건)")
spark.stop()
