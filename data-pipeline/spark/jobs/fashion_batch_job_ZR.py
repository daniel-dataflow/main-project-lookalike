from pyspark.sql import SparkSession
from pyspark.sql.functions import (lit, col, current_timestamp, concat, format_string, 
                                   input_file_name, regexp_extract, lower, element_at, 
                                   coalesce, to_json, row_number)
from pyspark.sql.window import Window
import datetime
import requests
from hdfs import InsecureClient
import psycopg2
import sys

# --- [1. 설정 정보] ---
BRAND_NAME = "zara"  
BRAND_PREFIX = "ZR"

if len(sys.argv) > 1:
    TARGET_DATE = sys.argv[1]
else:
    TARGET_DATE = datetime.datetime.now().strftime("%Y%m%d")

PG_HOST = "postgresql"
PG_DB = "datadb"
PG_USER = "datauser"
PG_PASS = "DataPass2026!"
MONGO_URI = "mongodb://datauser:DataPass2026!@mongo-main:27017"

HDFS_BASE = "hdfs://namenode-main:9000"
HDFS_WEB_URL = "http://namenode-main:9870"
RAW_PATH = f"/raw/{BRAND_NAME}/{TARGET_DATE}"
IMAGE_DIR = f"/raw/{BRAND_NAME}/image"

spark = SparkSession.builder \
    .appName("FashionBatchJobZara") \
    .config("spark.jars.packages", "org.postgresql:postgresql:42.6.0,org.mongodb.spark:mongo-spark-connector_2.12:10.1.1") \
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
    new_seq_data = [(BRAND_NAME.upper(), 0)]
    new_seq_df = spark.createDataFrame(new_seq_data, ["brand_name", "last_seq"])
    new_seq_df.write.format("jdbc") \
        .option("url", f"jdbc:postgresql://{PG_HOST}:5432/{PG_DB}") \
        .option("dbtable", "brand_sequences") \
        .option("user", PG_USER) \
        .option("password", PG_PASS) \
        .option("driver", "org.postgresql.Driver") \
        .mode("append").save()
    start_seq = 1
else:
    start_seq = row[0]['last_seq'] + 1

# --- [3. ETL 로직] ---
input_path = f"{HDFS_BASE}{RAW_PATH}/*.json"
raw_df = spark.read.option("multiLine", "true").json(input_path) \
    .withColumn("file_path", input_file_name())

windowSpec = Window.partitionBy(lit(BRAND_NAME)).orderBy(col("goodsNo"))

# 파일 경로에서 정보 추출 및 로컬 코드의 규칙 적용
processed_df = raw_df.withColumn("idx", row_number().over(windowSpec)) \
    .withColumn("product_id", format_string(f"{BRAND_PREFIX}%04d", col("idx").cast("int") + start_seq - 1)) \
    .withColumn("gender", lower(regexp_extract(col("file_path"), r"zara_([^_]+)_", 1))) \
    .withColumn("sub_category", lower(regexp_extract(col("file_path"), r"zara_[^_]+_([^_]+)_", 1))) \
    .withColumn("category_code", col("sub_category")) \
    .withColumn("img_filename", concat(lit(BRAND_NAME), lit("_"), col("gender"), lit("_"), col("sub_category"), lit("_"), col("goodsNo"), lit(".jpg"))) \
    .withColumn("img_hdfs_path", concat(lit(IMAGE_DIR), lit("/"), col("img_filename"))) \
    .withColumn("origin_url", concat(lit("https://www.zara.com/kr/ko/share/-p"), col("goodsNo"), lit(".html"))) \
    .withColumn("current_ts", current_timestamp())

total_count = processed_df.count()

# --- [4. PostgreSQL 적재 - 로컬 코드 기준 11개 컬럼 매핑] ---
pg_data = processed_df.select(
    col("product_id"),                             # 1. 상품 고유 ID
    col("goodsNo").alias("model_code"),            # 2. 모델 번호
    lit(BRAND_NAME.upper()).alias("brand_name"),   # 3. 브랜드명 (ZARA)
    col("goodsNm").alias("prod_name"),             # 4. 상품명
    coalesce(col("price").cast("int"), lit(0)).alias("base_price"), # 5. 가격
    col("gender"),                                 # 6. 성별
    col("category_code"),                          # 7. 카테고리 코드
    col("img_hdfs_path"),                          # 8. HDFS 이미지 경로
    col("origin_url"),                            # 9. 원본 URL (로컬 코드와 명칭 통일)
    col("current_ts").alias("create_dt"),          # 10. 생성일
    col("current_ts").alias("update_dt")           # 11. 수정일
)

pg_data.write.format("jdbc") \
    .option("url", f"jdbc:postgresql://{PG_HOST}:5432/{PG_DB}") \
    .option("dbtable", "products") \
    .option("user", PG_USER) \
    .option("password", PG_PASS) \
    .option("driver", "org.postgresql.Driver") \
    .mode("append") \
    .save()

print(f"✅ PostgreSQL 적재 완료 (총 {total_count}건)")
# --- [5. MongoDB 적재] ---
mongo_data = processed_df.select(
    col("product_id"),
    col("goodsNo").alias("model_code"),
    lit(BRAND_NAME.upper()).alias("brand_name"),
    col("goodsNm").alias("prod_name"),
    to_json(col("goodsMaterial")).alias("detail_desc"),
    coalesce(col("price").cast("int"), lit(0)).alias("base_price"),
    col("img_hdfs_path"),
    col("origin_url"),
    current_timestamp().alias("create_dt")
)

mongo_data.write.format("mongodb") \
    .option("spark.mongodb.write.connection.uri", MONGO_URI) \
    .option("database", "datadb") \
    .option("collection", "product_details") \
    .mode("append").save()
print("✅ MongoDB 적재 완료")

# --- [6. 이미지 처리 (WebHDFS)] ---
try:
    hdfs_client = InsecureClient(HDFS_WEB_URL, user="root")
    hdfs_client.makedirs(IMAGE_DIR)
    
    # 썸네일 이미지 URL 결정 로직 반영
    image_list = processed_df.select(
        coalesce(col("thumbnailImageUrl"), element_at(col("goodsImages"), 1)).alias("target_url"),
        col("img_filename")
    ).collect()
    
    print(f"📸 이미지 전송 시작 (총 {len(image_list)}건)...")
    success_img = 0
    for r in image_list:
        if r.target_url:
            try:
                img_content = requests.get(r.target_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'}).content
                hdfs_client.write(f"{IMAGE_DIR}/{r.img_filename}", data=img_content, overwrite=True)
                success_img += 1
            except Exception as e:
                print(f"❌ {r.img_filename} 실패: {e}")
    print(f"✅ 이미지 처리 완료: {success_img}건 저장")
except Exception as e:
    print(f"🚨 하둡 에러: {e}")

# --- [7. 시퀀스 업데이트] ---
try:
    new_last_seq = start_seq + total_count - 1
    conn = psycopg2.connect(host=PG_HOST, database=PG_DB, user=PG_USER, password=PG_PASS)
    cur = conn.cursor()
    cur.execute("UPDATE brand_sequences SET last_seq = %s WHERE brand_name = %s", (new_last_seq, BRAND_NAME.upper()))
    conn.commit()
    cur.close()
    conn.close()
except Exception as e:
    print(f"🚨 DB 업데이트 실패: {e}")

print(f"🏁 {BRAND_NAME.upper()} 작업 완료 (Seq: {new_last_seq})")
spark.stop()
