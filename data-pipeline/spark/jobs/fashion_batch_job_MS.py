from pyspark.sql import SparkSession
from pyspark.sql.functions import (lit, col, current_timestamp, concat, format_string, 
                                   lower, coalesce, row_number, input_file_name, 
                                   regexp_extract, to_json)
from pyspark.sql.window import Window
import psycopg2
import datetime
import requests
from hdfs import InsecureClient
import sys

# --- [1. 설정 정보] ---
BRAND_NAME = "musinsa"
BRAND_PREFIX = "MS"

if len(sys.argv) > 1:
    TARGET_DATE = sys.argv[1]
else:
    TARGET_DATE = datetime.datetime.now().strftime("%Y%m%d")

PG_HOST = "postgresql"
PG_DB = "datadb"
PG_USER = "datauser"
PG_PASS = "DataPass2026!"
MONGO_URI = "mongodb://datauser:DataPass2026!@mongo-main:27017"

HDFS_BASE = "hdfs://namenode:9000"
HDFS_WEB_URL = "http://namenode-main:9870"
RAW_PATH = f"/raw/{BRAND_NAME}/{TARGET_DATE}"
IMAGE_DIR = f"/raw/{BRAND_NAME}/image"

spark = SparkSession.builder \
    .appName("FashionBatchJobMusinsa") \
    .config(
        "spark.jars.packages",
        "org.postgresql:postgresql:42.6.0,"
        "org.mongodb.spark:mongo-spark-connector_2.12:10.1.1"
    ) \
    .getOrCreate()

# --- [2. 시퀀스 조회] ---
seq_df = spark.read.format("jdbc") \
    .option("url", f"jdbc:postgresql://{PG_HOST}:5432/{PG_DB}") \
    .option("dbtable", "brand_sequences") \
    .option("user", PG_USER) \
    .option("password", PG_PASS) \
    .option("driver", "org.postgresql.Driver") \
    .load()

row = seq_df.filter(col("brand_name") == BRAND_NAME.upper()).select("last_seq").collect()

if not row:
    print(f"✨ {BRAND_NAME} sequence not found. Registering new brand in DB...")
    new_seq_data = [(BRAND_NAME.upper(), 0)]
    new_seq_df = spark.createDataFrame(new_seq_data, ["brand_name", "last_seq"])
    
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

print(f"🚀 Starting {BRAND_NAME} job from sequence: {start_seq}")

# --- [3. ETL 로직] ---
input_path = f"{HDFS_BASE}{RAW_PATH}/*.json"
raw_df = spark.read.option("multiLine", "true").json(input_path) \
    .withColumn("file_path", input_file_name())

raw_df = raw_df \
    .withColumn("gender", regexp_extract(col("file_path"), f"{BRAND_NAME}_([^_]+)_", 1)) \
    .withColumn("sub_category", regexp_extract(col("file_path"), f"{BRAND_NAME}_[^_]+_([^_]+)_", 1)) \
    .withColumn("category_code", col("sub_category"))

windowSpec = Window.partitionBy(lit(BRAND_NAME)).orderBy(col("goodsNo"))

processed_df = raw_df.withColumn("idx", row_number().over(windowSpec)) \
    .withColumn("product_id", format_string(f"{BRAND_PREFIX}%04d", col("idx").cast("int") + start_seq - 1)) \
    .withColumn("image_filename", concat(lit(f"{BRAND_NAME}_"), col("gender"), lit("_"), col("sub_category"), lit("_"), col("goodsNo"), lit(".jpg"))) \
    .withColumn("img_hdfs_path", concat(lit(f"{IMAGE_DIR}/"), col("image_filename")))

processed_df.cache()
total_count = processed_df.count()

# --- [4. PostgreSQL 적재] ---
pg_data = processed_df.select(
    col("product_id"),
    coalesce(col("product_code"), col("goodsNo")).alias("model_code"),
    lit(BRAND_NAME.upper()).alias("brand_name"),
    col("goodsNm").alias("prod_name"),
    coalesce(col("price").cast("int"), lit(0)).alias("base_price"),
    col("gender"),
    col("category_code"),
    col("img_hdfs_path"),
    coalesce(col("url"), col("scraped_url")).alias("origin_url"), 
    current_timestamp().alias("create_dt"),
    current_timestamp().alias("update_dt")
)

pg_data.write.format("jdbc") \
    .option("url", f"jdbc:postgresql://{PG_HOST}:5432/{PG_DB}") \
    .option("dbtable", "products") \
    .option("user", PG_USER) \
    .option("password", PG_PASS) \
    .option("driver", "org.postgresql.Driver") \
    .mode("append").save()
print("✅ PostgreSQL 적재 완료")

# --- [5. MongoDB 적재] ---
mongo_data = processed_df.select(
    col("product_id"),
    coalesce(col("product_code"), col("goodsNo")).alias("model_code"),
    lit(BRAND_NAME.upper()).alias("brand_name"),
    col("goodsNm").alias("prod_name"),
    to_json(col("goodsMaterial")).alias("detail_desc"), # 핏, 계절감 등
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

# --- [6. 이미지 처리 (HDFS)] ---
try:
    hdfs_client = InsecureClient(HDFS_WEB_URL, user="root")
    hdfs_client.makedirs(IMAGE_DIR)
    
    image_list = processed_df.select(col("thumbnailImageUrl").alias("main_img"), col("image_filename")).collect()
    
    print(f"📸 이미지 다운로드 및 HDFS 직접 전송 시작 (총 {len(image_list)}건)...")
    success_img = 0
    for r in image_list:
        if r.main_img:
            hdfs_target_path = f"{IMAGE_DIR}/{r.image_filename}"
            try:
                img_content = requests.get(r.main_img, timeout=10, headers={'User-Agent': 'Mozilla/5.0'}).content
                hdfs_client.write(hdfs_target_path, data=img_content, overwrite=True)
                success_img += 1
            except Exception as e:
                print(f"❌ {r.image_filename} 저장 실패: {e}")
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
    print(f"✅ brand_sequences 업데이트: {BRAND_NAME.upper()} -> {new_last_seq}")
finally:
    if 'conn' in locals(): conn.close()

print(f"🏁 {BRAND_NAME.upper()} {total_count}건 배치 완료!")
spark.stop()
