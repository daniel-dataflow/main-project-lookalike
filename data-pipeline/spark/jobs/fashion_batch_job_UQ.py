# fashion_batch_job_UQ.py

from pyspark.sql import SparkSession
from pyspark.sql.functions import (lit, col, current_timestamp, concat, format_string, 
                                   input_file_name, regexp_extract, lower, 
                                   coalesce, to_json, row_number, explode)
from pyspark.sql.window import Window
import subprocess
import psycopg2

# --- [1. 설정 정보] ---
BRAND_NAME = "uniqlo"
BRAND_PREFIX = "UQ"
TARGET_DATE = datetime.now().strftime("%Y%m%d") #
#TARGET_DATE = "20260210" 

PG_HOST = "localhost"
PG_DB = "datadb"
PG_USER = "datauser"
PG_PASS = "DataPass2024!"

MONGO_URI = "mongodb://datauser:DataPass2024!@127.0.0.1:27017/admin?authSource=admin"

HDFS_BASE = "hdfs://localhost:9000"
RAW_PATH = f"/raw/{BRAND_NAME}/{TARGET_DATE}"
IMAGE_DIR = f"{RAW_PATH}/image"
CONTAINER_NAME = "namenode-main"

spark = SparkSession.builder \
    .appName("FashionBatchJobUniqlo") \
    .config("spark.jars.packages", "org.postgresql:postgresql:42.5.0,org.mongodb.spark:mongo-spark-connector_2.12:10.1.1") \
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
start_seq = row[0]['last_seq'] + 1 if row else 1
print(f"🚀 Starting {BRAND_NAME.upper()} job from sequence: {start_seq}")

# --- [3. ETL 로직] ---
input_path = f"{HDFS_BASE}{RAW_PATH}/*.json"
raw_df = spark.read.option("multiLine", "true").json(input_path) \
    .withColumn("file_path", input_file_name())

windowSpec = Window.partitionBy(lit(BRAND_NAME)).orderBy(col("goodsNo"))

processed_df = raw_df.withColumn("idx", row_number().over(windowSpec)) \
    .withColumn("product_id", format_string(f"{BRAND_PREFIX}%04d", col("idx").cast("int") + start_seq - 1)) \
    .withColumn("img_hdfs_path", concat(lit(IMAGE_DIR), lit("/"), col("goodsNo"), lit(".jpg"))) \
    .withColumn("gender", lower(regexp_extract(col("file_path"), r"uniqlo_([^_]+)_", 1))) \
    .withColumn("sub_category", lower(regexp_extract(col("file_path"), r"uniqlo_[^_]+_([^_]+)_", 1))) \
    .withColumn("category_code", concat(col("gender"), lit("_"), col("sub_category")))

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
    to_json(col("goodsMaterial")).alias("detail_desc"), # Uniqlo의 복잡한 소재 객체를 JSON 문자열로 저장
    coalesce(col("price").cast("int"), lit(0)).alias("base_price"),
    col("img_hdfs_path"),
    current_timestamp().alias("create_dt")
)

mongo_data.write.format("mongodb") \
    .option("spark.mongodb.write.connection.uri", MONGO_URI) \
    .option("database", "datadb") \
    .option("collection", "product_details") \
    .mode("append") \
    .save()
print("✅ MongoDB 적재 완료")

# --- [6. 이미지 처리 (colors 내 모든 icon_url 수집)] ---
subprocess.run(f"docker exec -i {CONTAINER_NAME} hdfs dfs -mkdir -p {IMAGE_DIR}", shell=True)

# colors 배열을 explode하여 모든 color_code와 icon_url 쌍을 추출 후 중복 제거
color_images = processed_df.select(
    explode(col("colors")).alias("color_info")
).select(
    col("color_info.color_code"),
    col("color_info.icon_url")
).distinct().collect()

print(f"📸 총 {len(color_images)}개의 고유 컬러 아이콘 이미지를 전송합니다.")

for r in color_images:
    if r.icon_url:
        # URL에서 원본 파일명 추출 (예: goods_09_481666_chip.jpg)
        file_name = r.icon_url.split('/')[-1] 
        cmd = f"wget -qO- --header='User-Agent: Mozilla/5.0' '{r.icon_url}' | docker exec -i {CONTAINER_NAME} hdfs dfs -put -f - {IMAGE_DIR}/{file_name}"
        subprocess.run(cmd, shell=True)

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