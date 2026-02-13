# fashion_batch_job_MS.py

from pyspark.sql import SparkSession
from pyspark.sql.functions import udf, lit, col, current_timestamp, concat, format_string, input_file_name, monotonically_increasing_id, regexp_extract, when
from pyspark.sql.types import StringType, IntegerType, StructType, StructField
import re
import subprocess
import psycopg2
from bs4 import BeautifulSoup
from datetime import datetime

# --- [1. 설정 정보] ---
MONGO_IP = "192.168.209.154" 
BRAND_NAME = "musinsa"
BRAND_PREFIX = "MS"
TARGET_DATE = datetime.now().strftime("%Y%m%d") # 실제 운영시 사용
#TARGET_DATE = "20260205"

HDFS_BASE = "hdfs://localhost:9000"
RAW_PATH = f"/raw/{BRAND_NAME}/{TARGET_DATE}"
IMAGE_DIR = f"{RAW_PATH}/image"

# 세션 생성 시 mongodb 전용 옵션을 확실히 추가합니다.
spark = SparkSession.builder \
    .appName(f"{BRAND_NAME}_ETL_{TARGET_DATE}") \
    .config("spark.jars.packages", "org.postgresql:postgresql:42.5.0,org.mongodb.spark:mongo-spark-connector_2.12:10.1.1") \
    .getOrCreate()

# --- [2. ID 채번 함수] ---
def get_and_update_sequence(count):
    conn = psycopg2.connect(host="localhost", database="datadb", user="datauser", password="DataPass2024!")
    cur = conn.cursor()
    cur.execute("""
        UPDATE brand_sequences 
        SET last_seq = last_seq + %s 
        WHERE brand_name = %s 
        RETURNING last_seq - %s + 1
    """, (count, BRAND_NAME.upper(), count))
    result = cur.fetchone()
    if not result:
        # 무신사 레코드가 없을 경우 대비 자동 생성 로직(선택사항)
        raise Exception(f"브랜드 {BRAND_NAME.upper()}를 brand_sequences 테이블에서 찾을 수 없습니다.")
    start_num = result[0]
    conn.commit()
    cur.close()
    conn.close()
    return start_num

# --- [3. 무신사 특화 파싱 UDF] ---

def parse_details(html_content):
    if not html_content or len(html_content) < 100: 
        return "unknown", "unknown", 0, "내용없음", None

    # 1. 모델 코드
    soup = BeautifulSoup(html_content, 'html.parser')
    model_code = "unknown"
    
    ## 무신사 상품번호는 'data-goods-no' 속성에도 많습니다.
    main_tag = soup.select_one("[data-goods-no]")
    if main_tag:
        model_code = main_tag['data-goods-no']
    
    if model_code == "unknown":
        # window.__MSS_PROD_INT_STATE__ 같은 전역 변수에서 추출 시도
        script_json = soup.find("script", string=re.compile("goodsNo"))
        if script_json:
            match = re.search(r'["\']goodsNo["\']\s*:\s*["\']?(\d+)["\']?', script_json.string)
            if match:
                model_code = match.group(1)

    # 상품명 및 가격
    meta_title = soup.find("meta", property="og:title")
    prod_name = meta_title['content'].split(" - ")[0].strip() if meta_title else "unknown"

    price_meta = soup.find("meta", property="product:price:amount")
    base_price = int(price_meta['content']) if price_meta else 0

    img_tag = soup.find("meta", property="og:image")
    img_url = img_tag['content'] if img_tag else None

    return model_code, prod_name, base_price, "상세 설명 없음", img_url

#############################################

info_schema = StructType([
    StructField("model_code", StringType(), True),
    StructField("prod_name", StringType(), True),
    StructField("base_price", IntegerType(), True),
    StructField("detail_desc", StringType(), True),
    StructField("img_url", StringType(), True)
])
parse_udf = udf(parse_details, info_schema)

# --- [4. ETL 로직] ---

input_path = f"{HDFS_BASE}{RAW_PATH}/*.html"
raw_df = spark.read.text(input_path, wholetext=True) \
              .select(input_file_name().alias("file_path"), col("value").alias("html_content"))

parsed_df = raw_df.withColumn("info", parse_udf(col("html_content")))

total_count = parsed_df.count()
if total_count == 0:
    print(f"❌ {TARGET_DATE} 무신사 데이터가 HDFS에 없습니다.")
    spark.stop()
    exit()

start_seq = get_and_update_sequence(total_count)

CONTAINER_NAME = "namenode-main"
subprocess.run(f"docker exec {CONTAINER_NAME} hdfs dfs -mkdir -p {IMAGE_DIR}", shell=True)

# 무신사 파일명 규칙에 맞춘 정규식 적용 (예: musinsa_Men_Top_12345.html)
final_df = parsed_df.withColumn("idx", monotonically_increasing_id() + 1) \
    .withColumn("product_id", format_string(f"{BRAND_PREFIX}%04d", col("idx") + start_seq - 1)) \
    .withColumn("img_hdfs_path", concat(lit(IMAGE_DIR), lit("/"), col("info.model_code"), lit(".jpg"))) \
    .withColumn("gender", regexp_extract(col("file_path"), r"musinsa_([A-Za-z]+)_", 1)) \
    .withColumn("sub_category", regexp_extract(col("file_path"), r"musinsa_[A-Za-z]+_([A-Za-z]+)_", 1)) \
    .withColumn("category_code", when(col("gender") != "", concat(col("gender"), lit("_"), col("sub_category")))
                                .otherwise("Unknown_Category"))

final_df.cache()

# --- [5. PostgreSQL 적재] ---
pg_data = final_df.select(
    col("product_id"),
    col("info.model_code").alias("model_code"),
    lit(BRAND_NAME.upper()).alias("brand_name"),
    col("info.prod_name").alias("prod_name"),
    col("info.base_price").alias("base_price"),
    col("category_code"),
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
    lit(BRAND_NAME.upper()).alias("brand_name"),
    col("info.detail_desc").alias("detail_desc"),
    col("img_hdfs_path"),
    current_timestamp().alias("create_dt")
)

# .option에 authSource와 database를 명확히 풀 경로로 적어줍니다.
clean_mongo_uri = f"mongodb://datauser:DataPass2024!@{MONGO_IP}:27017/?authSource=admin"

# 2. 명시적인 옵션 키를 사용하여 적재합니다.
mongo_data.write.format("mongodb") \
    .mode("append") \
    .option("connection.uri", clean_mongo_uri) \
    .option("database", "datadb") \
    .option("collection", "product_details") \
    .save()

# --- [7. 이미지 다운로드 및 HDFS 업로드] ---
image_list = final_df.select("info.img_url", "info.model_code").collect()

for row in image_list:
    if row.img_url and row.model_code != "unknown":
        img_filename = f"{row.model_code}.jpg"
        hdfs_target_path = f"{IMAGE_DIR}/{img_filename}"
        
        # 무신사는 이미지 서버 부하 방지를 위해 User-Agent 추가 권장
        cmd = f"wget -q --header='User-Agent: Mozilla/5.0' -O- {row.img_url} | docker exec -i {CONTAINER_NAME} hdfs dfs -put - {hdfs_target_path}"
        subprocess.run(cmd, shell=True)

print(f"✅ {BRAND_NAME.upper()} {total_count}건 적재 완료!")
spark.stop()