from pyspark.sql import SparkSession
from pyspark.sql.functions import udf, lit, col, current_timestamp, concat, format_string, input_file_name, monotonically_increasing_id, regexp_extract, when
from pyspark.sql.types import StringType, IntegerType, StructType, StructField
import re
import subprocess
import psycopg2
from bs4 import BeautifulSoup
from datetime import datetime

# --- [1. 설정 정보] ---
MONGO_IP = "localhost"
BRAND_NAME = "uniqlo"  # 소문자로 폴더명과 일치 권장
BRAND_PREFIX = "UQ"
# TARGET_DATE = datetime.now().strftime("%Y%m%d") # 실제 운영시 사용
TARGET_DATE = "20260205" 

# HDFS 기본 경로 설정
HDFS_BASE = "hdfs://localhost:9000"
# 요청하신 경로 규칙: /raw/브랜드명/일자/
RAW_PATH = f"/raw/{BRAND_NAME}/{TARGET_DATE}"
input_path = f"{HDFS_BASE}{RAW_PATH}/*.html"
IMAGE_DIR = f"{RAW_PATH}/image"

spark = SparkSession.builder \
    .appName(f"{BRAND_NAME}_ETL_{TARGET_DATE}") \
    .config("spark.mongodb.write.connection.uri", f"mongodb://datauser:DataPass2024!@{MONGO_IP}:27017/datadb.product_details?authSource=admin") \
    .config("spark.jars.packages", "org.postgresql:postgresql:42.5.0,org.mongodb.spark:mongo-spark-connector_2.12:10.1.1") \
    .getOrCreate()

# --- [2. ID 채번 함수] ---
def get_and_update_sequence(count):
    conn = psycopg2.connect(host="localhost", database="datadb", user="datauser", password="DataPass2024!")
    cur = conn.cursor()
    # DB에는 대문자로 저장되어 있을 수 있으므로 UPPER 사용
    cur.execute("""
        UPDATE brand_sequences 
        SET last_seq = last_seq + %s 
        WHERE brand_name = %s 
        RETURNING last_seq - %s + 1
    """, (count, BRAND_NAME.upper(), count))
    result = cur.fetchone()
    if not result:
        raise Exception(f"브랜드 {BRAND_NAME}를 brand_sequences 테이블에서 찾을 수 없습니다.")
    start_num = result[0]
    conn.commit()
    cur.close()
    conn.close()
    return start_num

# --- [3. 파싱 UDF] ---
def parse_details(html_content):
    if not html_content: return "unknown", "unknown", 0, "내용없음", None
    
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 모델 코드 (제품 번호 추출)
    model_text = soup.find(string=re.compile("제품 번호"))
    model_code = re.search(r"(\d+)", model_text).group(1) if model_text else "unknown"

    # 이미지 URL (원본 사이트 주소)
    img_tag = soup.find("meta", property="og:image")
    img_url = img_tag['content'] if img_tag else None

    # 상품명 및 가격
    meta_title = soup.find("meta", property="og:title")
    prod_name = meta_title['content'].replace("| UNIQLO KR", "").strip() if meta_title else "unknown"
    
    price_tag = soup.select_one(".fr-ec-price")
    base_price = int(re.sub(r'[^0-9]', '', price_tag.get_text())) if price_tag else 0

    # 상세 설명
    desc_elements = soup.select(".image-plus-text__horizontal-large-description, [data-testid='pdp-description-area']")
    detail_desc = "\n".join([d.get_text(strip=True) for d in desc_elements]) if desc_elements else "상세 설명 없음"

    return model_code, prod_name, base_price, detail_desc, img_url

info_schema = StructType([
    StructField("model_code", StringType(), True),
    StructField("prod_name", StringType(), True),
    StructField("base_price", IntegerType(), True),
    StructField("detail_desc", StringType(), True),
    StructField("img_url", StringType(), True)
])
parse_udf = udf(parse_details, info_schema)

# --- [4. ETL 로직] ---

# 1. 동적 경로에서 HTML 읽기
input_path = f"{HDFS_BASE}{RAW_PATH}/*.html"
print(f"📂 Reading from: {input_path}")

raw_df = spark.read.text(input_path, wholetext=True) \
              .select(input_file_name().alias("file_path"), col("value").alias("html_content"))

# 2. 파싱 및 기본 가공
parsed_df = raw_df.withColumn("info", parse_udf(col("html_content")))

total_count = parsed_df.count()
if total_count == 0:
    print(f"❌ {TARGET_DATE} 일자에 처리할 데이터가 없습니다.")
    spark.stop()
    exit()

# 3. ID 채번 및 경로 생성
start_seq = get_and_update_sequence(total_count)

# HDFS 이미지 디렉토리 생성 (Docker 명령어 사용)
CONTAINER_NAME = "namenode-main"
subprocess.run(f"docker exec {CONTAINER_NAME} hdfs dfs -mkdir -p {IMAGE_DIR}", shell=True)

final_df = parsed_df.withColumn("idx", monotonically_increasing_id() + 1) \
    .withColumn("product_id", format_string(f"{BRAND_PREFIX}%04d", col("idx") + start_seq - 1)) \
    .withColumn("img_hdfs_path", concat(lit(IMAGE_DIR), lit("/"), col("info.model_code"), lit(".jpg"))) \
    .withColumn("category_code", lit("outer")) # 필요시 경로에서 추출 로직 추가

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

mongo_data.write.format("mongodb") \
    .mode("append") \
    .option("database", "datadb") \
    .option("collection", "product_details") \
    .save()

# --- [7. 이미지 다운로드 및 HDFS 업로드] ---
image_list = final_df.select("info.img_url", "info.model_code").collect()

for row in image_list:
    if row.img_url and row.model_code != "unknown":
        img_filename = f"{row.model_code}.jpg"
        hdfs_target_path = f"{IMAGE_DIR}/{img_filename}"
        
        # 원본 URL에서 다운로드 받아 바로 HDFS로 파이프 연결
        cmd = f"wget -qO- {row.img_url} | docker exec -i {CONTAINER_NAME} hdfs dfs -put - {hdfs_target_path}"
        subprocess.run(cmd, shell=True)

print(f"✅ {BRAND_NAME.upper()} {total_count}건 적재 및 이미지 저장 완료! (Path: {IMAGE_DIR})")
spark.stop()