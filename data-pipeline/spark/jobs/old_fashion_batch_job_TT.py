# fashion_batch_job_TT.py

from pyspark.sql import SparkSession
from pyspark.sql.functions import udf, lit, col, current_timestamp, concat, format_string, input_file_name, monotonically_increasing_id, regexp_extract
from pyspark.sql.types import StringType, IntegerType, StructType, StructField
import re
import subprocess
import psycopg2
import json
from bs4 import BeautifulSoup
from datetime import datetime

# --- [1. 설정 정보] ---
MONGO_IP = "localhost"
BRAND_NAME = "topten"      # HDFS 경로 및 브랜드 구분
BRAND_PREFIX = "TT"        # 탑텐 전용 ID 접두사 (TopTen)
TARGET_DATE = datetime.now().strftime("%Y%m%d") # 수집 날짜 (필요시 수동 지정)
#TARGET_DATE = "20260205"



HDFS_BASE = "hdfs://localhost:9000"
# 크롤러 소스코드의 HDFS_BASE_PATH인 /datalake/raw/topten 구조를 반영
#RAW_PATH = f"/raw/{BRAND_NAME}/{TARGET_DATE}"
RAW_PATH = f"/raw/topten/{TARGET_DATE}"
IMAGE_DIR = f"{RAW_PATH}/image"
CONTAINER_NAME = "namenode-main"  # <--- docker ps에서 확인된 이름으로 수정

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

# --- [3. TOPTEN10 맞춤형 파싱 UDF] ---

def parse_tt_details(html_content, file_path):
    if not html_content or len(html_content) < 500: 
        return "unknown", "unknown", 0, "내용없음", None
    
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 1. 모델 코드 (파일명 기반)
    model_code = "unknown"
    try:
        model_code = file_path.split('/')[-1].split('_')[3]
    except: pass

    # 기본값 설정
    prod_name = "unknown"
    base_price = 0
    img_url = None

    # --- [핵심: JSON-LD 데이터 파싱] ---
    try:
        # <script type="application/ld+json"> 태그를 모두 찾음
        scripts = soup.find_all("script", type="application/ld+json")
        for script in scripts:
            data = json.loads(script.string)
            # @type이 Product인 JSON 데이터를 찾음
            if data.get("@type") == "Product":
                prod_name = data.get("name", prod_name)
                img_url = data.get("image", img_url)
                # offers 안에 있는 price 추출
                offers = data.get("offers", {})
                if isinstance(offers, dict):
                    price_val = offers.get("price")
                    if price_val:
                        base_price = int(float(str(price_val)))
                break
    except Exception as e:
        print(f"JSON 파싱 에러: {e}")

    # --- [백업: JSON 실패 시 기존 방식] ---
    if prod_name == "unknown":
        og_title = soup.find("meta", property="og:title")
        prod_name = og_title['content'].split('|')[0].strip() if og_title else "unknown"
    
    if base_price == 0:
        price_tag = soup.select_one("#salePrice") or soup.select_one(".gods-price .sale")
        if price_tag:
            base_price = int(re.sub(r"[^\d]", "", price_tag.get_text()))

    if not img_url:
        og_img = soup.find("meta", property="og:image")
        img_url = og_img['content'] if og_img else None

    return model_code, prod_name, base_price, "상세설명", img_url


#################
info_schema = StructType([
    StructField("model_code", StringType(), True),
    StructField("prod_name", StringType(), True),
    StructField("base_price", IntegerType(), True),
    StructField("detail_desc", StringType(), True),
    StructField("img_url", StringType(), True)
])
parse_udf = udf(parse_tt_details, info_schema)

# --- [4. ETL 로직] ---

# HDFS 주소를 명확히 붙여줍니다.
input_path = f"hdfs://localhost:9000{RAW_PATH}/*.html"
print(f"📂 Reading HTML files from: {input_path}")

raw_df = spark.read.text(input_path, wholetext=True) \
    .withColumnRenamed("value", "html_content") \
    .withColumn("file_path", input_file_name())

print(f"📊 읽어온 파일 개수: {raw_df.count()}개")

# 유효한 데이터만 필터링 및 파싱
parsed_df = raw_df.withColumn("info", parse_udf(col("html_content"), col("file_path")))
parsed_df = parsed_df.filter(col("info.prod_name") != "")
parsed_df.select("file_path", "info.prod_name", "info.base_price").show(5, truncate=False)

parsed_df.cache()

total_count = parsed_df.count()
if total_count == 0:
    print(f"❌ {BRAND_NAME.upper()} 유효한 데이터를 찾을 수 없습니다.")
    spark.stop()
    exit()

start_seq = get_and_update_sequence(total_count)

# 최종 데이터 가공
# 파일명에서 gender와 category를 정규식으로 추출
# 예: topten_Men_Outer_... -> gender: Men, category: Outer
final_df = parsed_df.withColumn("idx", monotonically_increasing_id() + 1) \
    .withColumn("product_id", format_string(f"{BRAND_PREFIX}%04d", col("idx") + start_seq - 1)) \
    .withColumn("img_hdfs_path", concat(lit(IMAGE_DIR), lit(f"/{BRAND_NAME}_"), col("info.model_code"), lit(".jpg"))) \
    .withColumn("gender", regexp_extract(col("file_path"), r"topten_([A-Za-z]+)_", 1)) \
    .withColumn("sub_category", regexp_extract(col("file_path"), r"topten_[A-Za-z]+_([A-Za-z]+)_", 1)) \
    .withColumn("category_code", concat(col("gender"), lit("_"), col("sub_category")))

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
    .option("url", "jdbc:postgresql://localhost:5432/datadb").option("driver", "org.postgresql.Driver") \
    .option("dbtable", "products").option("user", "datauser").option("password", "DataPass2024!") \
    .mode("append").save()

# --- [6. MongoDB 적재] ---
mongo_data = final_df.select(
    col("product_id"), col("info.model_code").alias("model_code"),
    lit(BRAND_NAME.upper()).alias("brand_name"),
    col("info.detail_desc").alias("detail_desc"),
    col("img_hdfs_path"), current_timestamp().alias("create_dt")
)

mongo_data.write.format("mongodb").mode("append").option("database", "datadb").option("collection", "product_details").save()

# --- [7. 이미지 처리 (중복 업로드 방지 로직 포함)] ---
# 이미 크롤러에서 이미지를 HDFS에 올렸다면 이 과정은 생략 가능하지만, 
# 만약 크롤러에서 누락된 이미지가 있다면 보충하는 역할을 합니다.
# select문에 "img_hdfs_path"를 추가합니다.
image_list = final_df.select("info.img_url", "info.model_code", "img_hdfs_path").collect()

for row in image_list:
    # 이미지 URL이 있고 모델 코드가 유효한 경우에만 실행
    if row.img_url and row.model_code != "unknown":
        # 1. HDFS에 파일이 이미 존재하는지 체크 (중복 다운로드 방지)
        check_cmd = f"docker exec {CONTAINER_NAME} hdfs dfs -test -e {row.img_hdfs_path}"
        exists = subprocess.run(check_cmd, shell=True).returncode
        
        if exists != 0: # 파일이 없으면(return code가 0이 아니면) 다운로드 수행
            # 2. wget으로 다운받아 바로 docker를 통해 HDFS로 스트리밍 저장
            # row.img_hdfs_path 예: /raw/topten/20260205/image/topten_MSF4VP1502NVP.jpg
            cmd = f"wget -qO- {row.img_url} | docker exec -i {CONTAINER_NAME} hdfs dfs -put - {row.img_hdfs_path}"
            subprocess.run(cmd, shell=True)

print(f"✅ {BRAND_NAME.upper()} {total_count}건 적재 및 이미지 처리 완료!")
spark.stop()