# fashion_batch_job_TT.py

from pyspark.sql import SparkSession
from pyspark.sql.functions import (lit, col, current_timestamp, concat, format_string, 
                                   input_file_name, regexp_extract, lower, element_at, 
                                   coalesce, to_json, row_number, udf)
from pyspark.sql.window import Window
from pyspark.sql.types import StringType
import json
import subprocess
import psycopg2
from datetime import datetime

# --- [1. 설정 정보] ---
BRAND_NAME = "topten"  # 소문자로 통일 (HDFS 경로용)
BRAND_PREFIX = "TT"    # Topten의 약어
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
    .appName("FashionBatchJobTopTen") \
    .config("spark.jars.packages", "org.postgresql:postgresql:42.5.0,org.mongodb.spark:mongo-spark-connector_2.12:10.1.1") \
    .getOrCreate()

# --- [2. UDF 정의: Topten용 필터링 (필요시 사용, 현재는 모두 유지)] ---
# 모든 정보를 넣기로 하셨으므로, JSON 직렬화만 안전하게 처리합니다.
def process_topten_material(material_json_str):
    if not material_json_str:
        return "{}"
    try:
        # 특별한 필터링 없이 JSON 문자열을 그대로 반환하거나 정리만 수행
        return material_json_str 
    except:
        return "{}"

process_udf = udf(process_topten_material, StringType())

# --- [3. PostgreSQL에서 현재 시퀀스 조회] ---
seq_df = spark.read.format("jdbc") \
    .option("url", f"jdbc:postgresql://{PG_HOST}:5432/{PG_DB}") \
    .option("dbtable", "brand_sequences") \
    .option("user", PG_USER) \
    .option("password", PG_PASS) \
    .option("driver", "org.postgresql.Driver") \
    .load()

# DB에는 'TOPTEN'으로 저장되어 있을 가능성이 큽니다.
row = seq_df.filter(col("brand_name") == BRAND_NAME.upper()).select("last_seq").collect()
start_seq = row[0]['last_seq'] + 1 if row else 1
print(f"🚀 Starting {BRAND_NAME.upper()} job from sequence: {start_seq}")

# --- [4. ETL 로직] ---
input_path = f"{HDFS_BASE}{RAW_PATH}/*.json"
raw_df = spark.read.option("multiLine", "true") \
                   .option("inferSchema", "true") \
                   .json(input_path) \
                   .withColumn("file_path", input_file_name())

windowSpec = Window.partitionBy(lit(BRAND_NAME)).orderBy(col("goodsNo"))

# Topten 파일명 예시: topten_men_outer_MSF4KG1001BR.json
# 그룹 1: men, 그룹 2: outer
processed_df = raw_df.withColumn("idx", row_number().over(windowSpec)) \
    .withColumn("product_id", format_string(f"{BRAND_PREFIX}%04d", col("idx").cast("int") + start_seq - 1)) \
    .withColumn("img_hdfs_path", concat(lit(IMAGE_DIR), lit("/"), col("goodsNo"), lit(".jpg"))) \
    .withColumn("gender", lower(regexp_extract(col("file_path"), r"topten_([^_]+)_", 1))) \
    .withColumn("sub_category", lower(regexp_extract(col("file_path"), r"topten_[^_]+_([^_]+)_", 1))) \
    .withColumn("category_code", concat(col("gender"), lit("_"), col("sub_category")))

processed_df.cache()
total_count = processed_df.count()

# --- [5. PostgreSQL 적재] ---
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

# --- [6. MongoDB 적재] ---
mongo_data = processed_df.select(
    col("product_id"),
    col("goodsNo").alias("model_code"),
    lit(BRAND_NAME.upper()).alias("brand_name"),
    col("goodsNm").alias("prod_name"),
    # goodsMaterial 전체를 JSON으로 변환하여 적재
    to_json(col("goodsMaterial")).alias("detail_desc"),
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

# --- [7. 이미지 처리] ---

# 7-1. HDFS 이미지 디렉토리 생성 (없을 경우에만 생성)
# -p 옵션을 주면 부모 디렉토리(/raw/topten/20260210)가 있어도 에러 없이 하위 폴더까지 생성합니다.
mkdir_cmd = f"docker exec -i {CONTAINER_NAME} hdfs dfs -mkdir -p {IMAGE_DIR}"
subprocess.run(mkdir_cmd, shell=True)
print(f"📂 HDFS 경로 확인 및 생성 완료: {IMAGE_DIR}")

image_list = processed_df.select(element_at(col("goodsImages"), 1).alias("main_img"), col("goodsNo")).collect()

for r in image_list:
    if r.main_img and r.goodsNo:
        hdfs_target_path = f"{IMAGE_DIR}/{r.goodsNo}.json" # 파일명에 맞춰 저장
        # -qO- 옵션으로 표준출력으로 보낸 뒤 docker를 통해 HDFS에 바로 저장
        cmd = f"wget -qO- --header='User-Agent: Mozilla/5.0' '{r.main_img}' | docker exec -i {CONTAINER_NAME} hdfs dfs -put -f - {IMAGE_DIR}/{r.goodsNo}.jpg"
        subprocess.run(cmd, shell=True)

print(f"📸 {total_count}개의 이미지 파일이 {IMAGE_DIR}에 저장되었습니다.")
# --- [8. 시퀀스 업데이트] ---
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

print(f"✅ {BRAND_NAME.upper()} 작업 완료 ({total_count}건)")
spark.stop()