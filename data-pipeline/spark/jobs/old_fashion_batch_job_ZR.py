# fashion_batch_job_ZR.py

from pyspark.sql import SparkSession
from pyspark.sql.functions import udf, lit, col, current_timestamp, concat, format_string, input_file_name, monotonically_increasing_id, regexp_extract
from pyspark.sql.types import StringType, IntegerType, StructType, StructField
import re
import json
import subprocess
import psycopg2
from bs4 import BeautifulSoup
from datetime import datetime

# --- [1. 설정 정보] ---
MONGO_IP = "localhost"
BRAND_NAME = "zara"
BRAND_PREFIX = "ZR"
TARGET_DATE = datetime.now().strftime("%Y%m%d") # 실제 운영시 사용
#TARGET_DATE = "20260205" # 상황에 맞게 수정

HDFS_BASE = "hdfs://localhost:9000"
RAW_PATH = f"/raw/{BRAND_NAME}/{TARGET_DATE}"
IMAGE_DIR = f"{RAW_PATH}/image"
CONTAINER_NAME = "namenode-main"

spark = SparkSession.builder \
    .appName(f"{BRAND_NAME}_ETL_{TARGET_DATE}") \
    .config("spark.mongodb.write.connection.uri", f"mongodb://datauser:DataPass2024!@{MONGO_IP}:27017/datadb.product_details?authSource=admin") \
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
        cur.execute("INSERT INTO brand_sequences (brand_name, last_seq) VALUES (%s, %s) RETURNING 1", (BRAND_NAME.upper(), count))
        start_num = 1
    else:
        start_num = result[0]
    conn.commit()
    cur.close()
    conn.close()
    return start_num

# --- [3. ZARA 맞춤형 파싱 UDF] ---

import json # 함수 상단에 json 임포트 확인

def parse_zara_details(html_content, file_path):
    # 자라 페이지는 본문이 상당히 길기 때문에 500자 기준은 적절합니다.
    if not html_content or len(html_content) < 500: 
        return "unknown", "unknown", 0, "내용없음", None
    
    # 1. 모델 코드 추출 (zara_Men_Outer_12700720_20260205.html 기준)
    model_code = "unknown"
    try:
        file_name = file_path.split('/')[-1]
        parts = file_name.split('_')
        # 파일명이 규칙적이라면 12700720은 4번째(인덱스 3)에 위치합니다.
        if len(parts) >= 4:
            model_code = parts[3] 
        else:
            # 혹시 zara_12700720_... 형태일 경우를 대비
            model_code = parts[1]
    except:
        pass

    soup = BeautifulSoup(html_content, 'html.parser')
    prod_name = "unknown"
    base_price = 0
    img_url = None
    detail_desc = "상세 설명 없음"

    # --- [핵심: LD+JSON 데이터 추출] ---
    try:
        scripts = soup.find_all("script", type="application/ld+json")
        for script in scripts:
            if not script.string: continue
            
            data = json.loads(script.string)
            
            # 리스트 형태일 경우 첫 번째 요소 사용
            if isinstance(data, list): 
                data = data[0]
            
            # @graph 구조인 경우 처리
            if "@graph" in data:
                for item in data["@graph"]:
                    if item.get("@type") == "Product":
                        data = item
                        break

            if data.get("@type") == "Product":
                prod_name = data.get("name", prod_name)
                # description이 dict 형태일 때를 대비해 strip() 처리
                detail_desc = data.get("description", detail_desc)
                
                # 이미지 추출 (ZARA는 보통 리스트로 들어있음)
                images = data.get("image")
                if isinstance(images, list) and len(images) > 0:
                    img_url = images[0]
                elif isinstance(images, str):
                    img_url = images
                
                # 가격 추출
                offers = data.get("offers")
                if offers:
                    # offers가 리스트인 경우와 단일 dict인 경우 모두 대응
                    main_offer = offers[0] if isinstance(offers, list) else offers
                    price_val = main_offer.get("price")
                    if price_val:
                        # 소수점 제거 및 정수 변환
                        base_price = int(float(str(price_val)))
                break
    except Exception as e:
        print(f"JSON Parsing Error: {e}") # 디버깅용

    # --- [백업: 태그 직접 파싱] ---
    if prod_name == "unknown":
        # 자라는 h1의 클래스명이 유동적이므로 태그 자체로 접근
        name_tag = soup.find("h1")
        prod_name = name_tag.get_text(strip=True) if name_tag else "unknown"

    if base_price == 0:
        # 자라 가격 태그 (현재 기준 주요 클래스)
        price_tag = soup.select_one(".price-current__amount, .money-amount__main")
        if price_tag:
            try:
                price_text = re.sub(r"[^\d]", "", price_tag.get_text())
                base_price = int(price_text)
            except: pass

    return model_code, prod_name, base_price, detail_desc, img_url



#################

info_schema = StructType([
    StructField("model_code", StringType(), True),
    StructField("prod_name", StringType(), True),
    StructField("base_price", IntegerType(), True),
    StructField("detail_desc", StringType(), True),
    StructField("img_url", StringType(), True)
])
parse_udf = udf(parse_zara_details, info_schema)

# --- [4. ETL 로직] ---
input_path = f"{HDFS_BASE}{RAW_PATH}/*.html"
raw_df = spark.read.text(input_path, wholetext=True) \
              .select(input_file_name().alias("file_path"), col("value").alias("html_content"))

parsed_df = raw_df.withColumn("info", parse_udf(col("html_content"), col("file_path")))
parsed_df.cache()

total_count = parsed_df.count()
if total_count == 0:
    print(f"❌ {BRAND_NAME} 데이터를 찾을 수 없습니다.")
    spark.stop()
    exit()

start_seq = get_and_update_sequence(total_count)
subprocess.run(f"docker exec {CONTAINER_NAME} hdfs dfs -mkdir -p {IMAGE_DIR}", shell=True)

# 자라 파일명 규칙: zara_Gender_Category_ID...
final_df = parsed_df.withColumn("idx", monotonically_increasing_id() + 1) \
    .withColumn("product_id", format_string(f"{BRAND_PREFIX}%04d", col("idx") + start_seq - 1)) \
    .withColumn("img_hdfs_path", concat(lit(IMAGE_DIR), lit(f"/{BRAND_NAME}_"), col("info.model_code"), lit(".jpg"))) \
    .withColumn("gender", regexp_extract(col("file_path"), r"zara_([A-Za-z]+)_", 1)) \
    .withColumn("sub_category", regexp_extract(col("file_path"), r"zara_[A-Za-z]+_([A-Za-z]+)_", 1)) \
    .withColumn("category_code", concat(col("gender"), lit("_"), col("sub_category")))

final_df.cache()

# --- [5. PostgreSQL 적재] ---
pg_data = final_df.select(
    col("product_id"), col("info.model_code").alias("model_code"),
    lit(BRAND_NAME.upper()).alias("brand_name"), col("info.prod_name").alias("prod_name"),
    col("info.base_price").alias("base_price"), col("category_code"),
    col("img_hdfs_path"), current_timestamp().alias("create_dt"), current_timestamp().alias("update_dt")
)
pg_data.write.format("jdbc") \
    .option("url", "jdbc:postgresql://localhost:5432/datadb").option("driver", "org.postgresql.Driver") \
    .option("dbtable", "products").option("user", "datauser").option("password", "DataPass2024!") \
    .mode("append").save()

# --- [6. MongoDB 적재] ---
mongo_data = final_df.select(
    col("product_id"), col("info.model_code").alias("model_code"),
    lit(BRAND_NAME.upper()).alias("brand_name"), col("info.detail_desc").alias("detail_desc"),
    col("img_hdfs_path"), current_timestamp().alias("create_dt")
)
mongo_data.write.format("mongodb").mode("append").option("database", "datadb").option("collection", "product_details").save()

# --- [7. 이미지 처리] ---
image_list = final_df.select("info.img_url", "info.model_code").collect()
for row in image_list:
    if row.img_url and row.model_code != "unknown":
        hdfs_target_path = f"{IMAGE_DIR}/{row.model_code}.jpg"
        # 자라 이미지는 Referer 체크가 있을 수 있으므로 -H 옵션 추가 고려
        cmd = f"wget -qO- '{row.img_url}' | docker exec -i {CONTAINER_NAME} hdfs dfs -put - {hdfs_target_path}"
        subprocess.run(cmd, shell=True)

print(f"✅ {BRAND_NAME.upper()} {total_count}건 적재 완료!")
spark.stop()

