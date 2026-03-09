from pyspark.sql import SparkSession
from pyspark.sql.functions import (lit, col, current_timestamp, concat, format_string, 
                                   input_file_name, regexp_extract, lower, concat_ws, element_at, 
                                   coalesce, to_json, row_number, udf)
from pyspark.sql.window import Window
from pyspark.sql.types import StringType
import psycopg2
import datetime
import os
import sys
import json
import requests
from pytz import timezone
from hdfs import InsecureClient

# 스파크 워커 내 파이썬 버전 에러 방지
os.environ["PYSPARK_PYTHON_VERSION_CHECK"] = "0"

# --- [1. 설정 정보] ---
MONGO_IP = "mongo-main"
BRAND_NAME = "8seconds"
BRAND_PREFIX = "8S"

kst = timezone('Asia/Seoul')

if len(sys.argv) > 1:
    TARGET_DATE = sys.argv[1]
else:
    TARGET_DATE = datetime.datetime.now().strftime("%Y%m%d")

PG_HOST = "postgres-main"  
PG_DB = "datadb"       
PG_USER = "datauser"
PG_PASS = "DataPass2026!"  

MONGO_USER = "datauser"
MONGO_PASS = "DataPass2026!"

HDFS_BASE = "hdfs://namenode-main:9000"
HDFS_WEB_URL = "http://namenode-main:9870"
RAW_PATH = f"/raw/{BRAND_NAME}/{TARGET_DATE}"
IMAGE_DIR = f"/raw/{BRAND_NAME}/image"

spark = SparkSession.builder \
    .appName("FashionBatchJob8S") \
    .config("spark.mongodb.write.connection.uri", f"mongodb://{MONGO_USER}:{MONGO_PASS}@{MONGO_IP}:27017/{PG_DB}?authSource=admin") \
    .getOrCreate()

# --- [2. UDF 정의: 불필요한 키워드 필터링] ---
def filter_material_info(material_json_str):
    if not material_json_str:
        return "{}"
    exclude_keywords = ["유의사항", "분쟁해결", "책임자", "환불", "반품", "교환", "배송", "접수방법", "라벨", "SSF SHOP", "매장픽업"]
    try:
        data = json.loads(material_json_str)
        filtered_data = {k: v for k, v in data.items() if not any(word in k for word in exclude_keywords)}
        return json.dumps(filtered_data, ensure_ascii=False)
    except:
        return "{}"

filter_udf = udf(filter_material_info, StringType())

# --- [3. PostgreSQL 시퀀스 관리] ---
try:
    init_conn = psycopg2.connect(host=PG_HOST, database=PG_DB, user=PG_USER, password=PG_PASS)
    init_cur = init_conn.cursor()
    init_cur.execute("""
        CREATE TABLE IF NOT EXISTS brand_sequences (
            brand_name VARCHAR(50) PRIMARY KEY,
            last_seq INTEGER DEFAULT 0
        );
    """)
    init_conn.commit()
    
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

# --- [4. ETL 로직 (JSON 읽기)] ---
input_path = f"{HDFS_BASE}{RAW_PATH}/*.json"
raw_df = spark.read.option("multiLine", "true") \
                   .option("inferSchema", "true") \
                   .json(input_path) \
                   .withColumn("file_path", input_file_name())

windowSpec = Window.partitionBy(lit(BRAND_NAME)).orderBy(col("goodsNo"))

processed_df = raw_df.withColumn("idx", row_number().over(windowSpec)) \
    .withColumn("product_id", format_string(f"{BRAND_PREFIX}%04d", col("idx").cast("int") + start_seq - 1)) \
    .withColumn("target_img_url", 
        coalesce(
            element_at(col("goodsImages"), 3), # 1순위
            element_at(col("goodsImages"), 5)   # 2순위 
        )
    ) \
    .withColumn("gender", lower(regexp_extract(col("file_path"), r"8seconds_([^_]+)_", 1))) \
    .withColumn("category_code", lower(regexp_extract(col("file_path"), r"8seconds_[^_]+_([^_]+)_", 1))) \
    .withColumn("new_filename", concat_ws("_", lit(BRAND_NAME.lower()), col("gender"), col("category_code"), col("goodsNo"))) \
    .withColumn("img_hdfs_path", concat(lit(IMAGE_DIR), lit("/"), col("new_filename"), lit(".jpg"))) \
    .withColumn("origin_url", (col("url")))

processed_df.cache()
total_count = processed_df.count()

if total_count == 0:
    print("❌ 수집된 데이터가 없습니다. 잡을 종료합니다.")
    spark.stop()
    sys.exit(0)

print(f"📊 처리할 데이터: {total_count} 건")

# --- [5. PostgreSQL 적재] ---
pg_data = processed_df.select(
    col("product_id"),
    col("goodsNo").alias("model_code"),
    lit(BRAND_NAME.upper()).alias("brand_name"),
    col("goodsNm").alias("prod_name"),
    coalesce(col("price").cast("int"), lit(0)).alias("base_price"),
    col("gender"),
    col("category_code"),
    col("img_hdfs_path"),
    col("url").alias("origin_url"),
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

# --- [6. MongoDB 적재] ---
mongo_data = processed_df.select(
    col("product_id"),
    col("goodsNo").alias("model_code"),
    lit(BRAND_NAME.upper()).alias("brand_name"),
    col("goodsNm").alias("prod_name"),
    filter_udf(to_json(col("goodsMaterial"))).alias("detail_desc"),
    col("img_hdfs_path"),
    col("goodsImages").alias("all_images"),
    current_timestamp().alias("create_dt")
)

mongo_data.write.format("mongodb") \
    .option("spark.mongodb.write.database", PG_DB) \
    .option("spark.mongodb.write.collection", "Product_details") \
    .mode("append") \
    .save()

# --- [7. 이미지 다운로드 및 WebHDFS 저장] ---
try:
    hdfs_client = InsecureClient(HDFS_WEB_URL, user="root")
    hdfs_client.makedirs(IMAGE_DIR)
    
    image_list = processed_df.select("target_img_url", "new_filename", "goodsNo").collect()
    print(f"📸 이미지 다운로드 및 HDFS 전송 시작 (총 {len(image_list)}건)...")
    
    success_img = 0
    fail_img = 0
    
    for r in image_list:
        if r.target_img_url and r.new_filename:
            hdfs_target_path = f"{IMAGE_DIR}/{r.new_filename}.jpg"
            try:
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                img_content = requests.get(r.target_img_url, headers=headers, timeout=15).content
                hdfs_client.write(hdfs_target_path, data=img_content, overwrite=True)
                success_img += 1
            except Exception as e:
                print(f"❌ {r.goodsNo} 이미지 저장 실패: {e}")
                fail_img += 1
                
    print(f"✅ 이미지 처리 완료: 성공 {success_img} / 실패 {fail_img}")

except Exception as e:
    print(f"🚨 하둡 클라이언트 연결 또는 이미지 처리 실패: {e}")

# --- [8. 시퀀스 업데이트] ---
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
