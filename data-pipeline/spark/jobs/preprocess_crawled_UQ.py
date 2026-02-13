from pyspark.sql import SparkSession
from pyspark.sql.functions import udf, lit, col, current_timestamp, concat, format_string, row_number
from pyspark.sql.types import StringType, ArrayType, IntegerType, StructType, StructField
from pyspark.sql.functions import input_file_name
from pyspark.sql.window import Window
from pyspark.sql.functions import monotonically_increasing_id, regexp_extract, when

import re
import subprocess
import requests
import psycopg2
from bs4 import BeautifulSoup
from datetime import datetime


# 1. Spark 세션 설정

MONGO_IP = "localhost"

spark = SparkSession.builder \
    .appName("Uniqlo_Specific_ETL_V2") \
    .config("spark.mongodb.write.connection.uri", f"mongodb://datauser:DataPass2024!@{MONGO_IP}:27017/datadb.product_details?authSource=admin") \
    .config("spark.jars.packages", "org.postgresql:postgresql:42.5.0,org.mongodb.spark:mongo-spark-connector_2.12:10.1.1") \
    .getOrCreate()

BRAND_PREFIX = "UQ"
BRAND_NAME = "UNIQLO"
#TARGET_DATE = datetime.now().strftime("%Y%m%d")
TARGET_DATE = "20260205"

# --- [2. ID 채번: PostgreSQL 시퀀스 테이블 업데이트 및 가져오기] ---
def get_and_update_sequence(count):

    conn = psycopg2.connect(host="localhost", database="datadb", user="datauser", password="DataPass2024!")
    cur = conn.cursor()
    # 브랜드의 마지막 시퀀스를 가져오면서 동시에 업데이트 (Row Lock으로 정합성 보장)
    cur.execute("""
        UPDATE brand_sequences 
        SET last_seq = last_seq + %s 
        WHERE brand_name = %s 
        RETURNING last_seq - %s + 1
    """, (count, BRAND_NAME, count))
    start_num = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return start_num

# --- [3. 상세 정보 파싱 UDF (순수하게 파싱만 수행)] ---
# 중요: 함수 안에서 spark, sc, hdfs 명령어를 모두 제거했습니다.
def parse_uniqlo_details(file_path, html_content):

    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 1. 모델 코드 추출
    model_text = soup.find(string=re.compile("제품 번호"))
    model_code = re.search(r"(\d+)", model_text).group(1) if model_text else "unknown"

    # 2. 이미지 URL 추출
    img_tag = soup.find("meta", property="og:image")
    img_url = img_tag['content'] if img_tag else None

    # 3. 텍스트 정보 파싱
    meta_title = soup.find("meta", property="og:title")
    prod_name = meta_title['content'].replace("| UNIQLO KR", "").strip() if meta_title else "unknown"
    
    price_tag = soup.select_one(".fr-ec-price")
    base_price = int(re.sub(r'[^0-9]', '', price_tag.get_text())) if price_tag else 0

    desc_elements = soup.select(".image-plus-text__horizontal-large-description, [data-testid='pdp-description-area']")
    descriptions = [d.get_text(strip=True) for d in desc_elements if d.get_text(strip=True)]
    detail_desc = "\n".join(descriptions) if descriptions else "상세 설명 없음"

    # 파싱된 데이터와 나중에 이미지를 다운로드할 때 쓸 URL을 같이 보냅니다.
    return model_code, prod_name, base_price, detail_desc, img_url

# 스키마에 img_url을 추가했습니다.
info_schema = StructType([
    StructField("model_code", StringType(), True),
    StructField("prod_name", StringType(), True),
    StructField("base_price", IntegerType(), True),
    StructField("detail_desc", StringType(), True),
    StructField("img_url", StringType(), True)
])
uniqlo_udf = udf(parse_uniqlo_details, info_schema)


# --- [4. ETL 로직 시작] ---
# 1. 파일 읽기 및 경로 정보 포함
raw_df = spark.read.text("hdfs://localhost:9000/lookalike/raw/uniqlo/20260205/*.html", wholetext=True) \
              .select(input_file_name().alias("file_path"), col("value").alias("html_content"))

# 2. 파싱 수행 (UDF 실행)
parsed_df = raw_df.withColumn("info", uniqlo_udf(col("file_path"), col("html_content")))

# 3. 데이터 개수 확인 및 시퀀스 채번
total_count = parsed_df.count()
if total_count == 0:
    print("❌ 처리할 데이터가 없습니다.")
    spark.stop()
    exit()

start_seq = get_and_update_sequence(total_count)

# --- [3.5 HDFS 설정 및 폴더 생성] ---
HDFS_IMAGE_DIR = f"/lookalike/raw/uniqlo/{TARGET_DATE}/image/"

def create_hdfs_dir(path):
    conf = spark._jsc.hadoopConfiguration()
    fs = spark._jvm.org.apache.hadoop.fs.FileSystem.get(conf)
    hdfs_path = spark._jvm.org.apache.hadoop.fs.Path(path)
    if not fs.exists(hdfs_path):
        fs.mkdirs(hdfs_path)

create_hdfs_dir(HDFS_IMAGE_DIR)


# 4. ID 생성, HDFS 이미지 경로 생성, 카테고리 추출 통합
hdfs_base_url = "hdfs://localhost:9000"
full_image_dir = hdfs_base_url + HDFS_IMAGE_DIR 

final_df = parsed_df.withColumn("idx", monotonically_increasing_id() + 1) \
    .withColumn("product_id", format_string(f"{BRAND_PREFIX}%04d", col("idx") + start_seq - 1)) \
    .withColumn("img_hdfs_path", concat(lit(full_image_dir), col("info.model_code"), lit(".jpg"))) \
    .withColumn("extracted_cat", regexp_extract(col("file_path"), r"uniqlo/\d+/([^/]+)/", 1))

final_df.cache()

# --- [5. PostgreSQL 적재] ---
pg_data = final_df.select(
    col("product_id"),
    col("info.model_code").alias("model_code"),
    lit(BRAND_NAME).alias("brand_name"),
    col("info.prod_name").alias("prod_name"),
    col("info.base_price").alias("base_price"),
    # 경로에서 추출한 카테고리가 없으면 기본값 'outer' 사용
    when(col("extracted_cat") != "", col("extracted_cat")).otherwise(lit("outer")).alias("category_code"),
    col("img_hdfs_path"),
    current_timestamp().alias("create_dt"),
    current_timestamp().alias("update_dt")
)

pg_data.write.format("jdbc") \
    .option("url", "jdbc:postgresql://localhost:5432/datadb") \
    .option("driver", "org.postgresql.Driver") \
    .option("dbtable", "products") \
    .option("user", "datauser") \
    .option("password", "DataPass2024!") \
    .mode("append").save()

# --- [6. MongoDB 적재] ---
mongo_data = final_df.select(
    col("product_id"),
    col("info.model_code").alias("model_code"),
    lit(BRAND_NAME).alias("brand_name"),
    col("info.detail_desc").alias("detail_desc"),
    col("img_hdfs_path"),
    current_timestamp().alias("create_dt")
)

mongo_data.write.format("mongodb") \
    .mode("append") \
    .option("connection.uri", f"mongodb://datauser:DataPass2024!@{MONGO_IP}:27017") \
    .option("database", "datadb") \
    .option("collection", "product_details") \
    .option("authSource", "admin") \
    .save()

# --- [7. 이미지 다운로드 로직] ---
CONTAINER_NAME = "namenode-main" 
image_list = final_df.select("info.img_url", "info.model_code").collect()


for row in image_list:
    if row.img_url and row.model_code != "unknown":
        # 파일명을 DB에 저장한 형식과 동일하게 '모델코드.jpg'로 고정
        img_filename = f"{row.model_code}.jpg" 
        hdfs_target_path = f"{HDFS_IMAGE_DIR}{img_filename}"
        
        # 다운로드 및 업로드 실행
        cmd = f"wget -qO- {row.img_url} | docker exec -i {CONTAINER_NAME} hdfs dfs -put - {hdfs_target_path}"
        subprocess.run(cmd, shell=True)


print(f"✅ {BRAND_NAME} {total_count}건 모든 적재 완료!")
spark.stop()
