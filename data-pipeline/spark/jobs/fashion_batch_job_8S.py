# fashion_batch_job_8S.py

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
MONGO_IP = "localhost"
BRAND_NAME = "8seconds"
BRAND_PREFIX = "8S"
# 현재 실행 시점의 날짜 (HDFS 경로와 일치해야 함)
TARGET_DATE = datetime.now().strftime("%Y%m%d") #
#TARGET_DATE = "20260210" 

# DB 접속 정보 (본인의 환경에 맞게 수정 확인)
PG_HOST = "localhost"
PG_DB = "datadb"
PG_USER = "datauser"
PG_PASS = "DataPass2024!"

MONGO_URI = "mongodb://localhost:27017"

HDFS_BASE = "hdfs://localhost:9000"
RAW_PATH = f"/raw/{BRAND_NAME}/{TARGET_DATE}"
IMAGE_DIR = f"{RAW_PATH}/image"
CONTAINER_NAME = "namenode-main"

spark = SparkSession.builder \
    .appName("FashionBatchJob8S") \
    .config("spark.jars.packages", "org.postgresql:postgresql:42.5.0,org.mongodb.spark:mongo-spark-connector_2.12:10.1.1") \
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

# --- [3. PostgreSQL에서 현재 시퀀스 조회] ---
seq_df = spark.read.format("jdbc") \
    .option("url", f"jdbc:postgresql://{PG_HOST}:5432/{PG_DB}") \
    .option("dbtable", "brand_sequences") \
    .option("user", PG_USER) \
    .option("password", PG_PASS) \
    .option("driver", "org.postgresql.Driver") \
    .load()

row = seq_df.filter(col("brand_name") == BRAND_NAME.upper()).select("last_seq").collect()
start_seq = row[0]['last_seq'] + 1 if row else 1
print(f"🚀 Starting {BRAND_NAME} job from sequence: {start_seq}")

# --- [4. ETL 로직] ---
input_path = f"{HDFS_BASE}{RAW_PATH}/*.json"
raw_df = spark.read.option("multiLine", "true") \
                   .option("inferSchema", "true") \
                   .option("samplingRatio", "1.0") \
                   .json(input_path) \
                   .withColumn("file_path", input_file_name())

windowSpec = Window.partitionBy(lit(BRAND_NAME)).orderBy(col("goodsNo"))

processed_df = raw_df.withColumn("idx", row_number().over(windowSpec)) \
    .withColumn("product_id", format_string(f"{BRAND_PREFIX}%04d", col("idx").cast("int") + start_seq - 1)) \
    .withColumn("img_hdfs_path", concat(lit(IMAGE_DIR), lit("/"), col("goodsNo"), lit(".jpg"))) \
    .withColumn("gender", lower(regexp_extract(col("file_path"), r"8seconds_([^_]+)_", 1))) \
    .withColumn("sub_category", lower(regexp_extract(col("file_path"), r"8seconds_[^_]+_([^_]+)_", 1))) \
    .withColumn("category_code", concat(col("gender"), lit("_"), col("sub_category")))

processed_df.cache()
total_count = processed_df.count()

# --- [5. PostgreSQL 적재] ---
pg_data = processed_df.select(
    col("product_id"),
    col("goodsNo").alias("model_code"),  # <-- 이 줄을 추가하세요!
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
    filter_udf(to_json(col("goodsMaterial"))).alias("detail_desc"),
    coalesce(col("price").cast("int"), lit(0)).alias("base_price"),
    col("img_hdfs_path"),
    current_timestamp().alias("create_dt")
)

mongo_data.repartition(1).write.format("mongodb") \
    .option("spark.mongodb.write.connection.uri", "mongodb://datauser:DataPass2024!@127.0.0.1:27017/admin?authSource=admin") \
    .option("database", "datadb") \
    .option("collection", "product_details") \
    .option("maxBatchSize", 20) \
    .option("localThreshold", "0") \
    .option("serverSelectionTimeoutMS", "5000") \
    .mode("append") \
    .save()

# --- [7. 이미지 처리] ---

# 크롤러가 날짜 폴더까지만 만들었을 경우를 대비해 /image 폴더를 생성합니다.
mkdir_cmd = f"docker exec -i {CONTAINER_NAME} hdfs dfs -mkdir -p {IMAGE_DIR}"
subprocess.run(mkdir_cmd, shell=True)
print(f"📂 [8Seconds] HDFS 이미지 디렉토리 확인/생성 완료: {IMAGE_DIR}")

# goodsImages 배열에서 첫 번째 이미지 URL 추출
image_list = processed_df.select(element_at(col("goodsImages"), 1).alias("main_img"), col("goodsNo")).collect()

for r in image_list:
    if r.main_img and r.goodsNo:
        hdfs_target_path = f"{IMAGE_DIR}/{r.goodsNo}.jpg"
        cmd = f"wget -qO- --header='User-Agent: Mozilla/5.0' '{r.main_img}' | docker exec -i {CONTAINER_NAME} hdfs dfs -put - {hdfs_target_path}"
        subprocess.run(cmd, shell=True)

# --- [8. 시퀀스 업데이트] ---
# --- [5. PostgreSQL 적재 완료 후] ---
# ... (기존 pg_data.write.save() 코드 바로 아래에 추가)

try:
    
    # 1. 새로운 시퀀스 번호 계산
    new_last_seq = start_seq + total_count - 1
    
    # 2. DB 연결
    conn = psycopg2.connect(
        host=PG_HOST, 
        database=PG_DB, 
        user=PG_USER, 
        password=PG_PASS
    )
    cur = conn.cursor()
    
    # 3. 시퀀스 업데이트 실행
    cur.execute(
        "UPDATE brand_sequences SET last_seq = %s WHERE brand_name = %s", 
        (new_last_seq, BRAND_NAME.upper())
    )
    
    # 4. 변경사항 확정 및 종료
    conn.commit()
    print(f"✅ brand_sequences 업데이트 완료: {BRAND_NAME.upper()} -> {new_last_seq}")
    
except Exception as e:
    print(f"❌ 시퀀스 업데이트 중 오류 발생: {e}")
    # 시퀀스 업데이트 실패 시 작업 전체의 정합성이 깨질 수 있으므로 로그를 남기는 것이 중요합니다.
finally:
    if 'cur' in locals(): cur.close()
    if 'conn' in locals(): conn.close()


print(f"✅ {BRAND_NAME.upper()} {total_count}건 PostgreSQL/MongoDB 적재 및 이미지 저장 완료!")
spark.stop()




