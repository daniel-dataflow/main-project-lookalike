# fashion_batch_job_8S.py

from pyspark.sql import SparkSession
from pyspark.sql.functions import udf, lit, col, current_timestamp, concat, format_string, input_file_name, monotonically_increasing_id, regexp_extract
from pyspark.sql.types import StringType, IntegerType, StructType, StructField
import re
import subprocess
import psycopg2
from bs4 import BeautifulSoup
from datetime import datetime

# --- [1. 설정 정보] ---
MONGO_IP = "localhost"
BRAND_NAME = "8seconds"  # 폴더명 및 브랜드 구분용
BRAND_PREFIX = "8S"      # 8seconds 전용 ID 접두사
TARGET_DATE = datetime.now().strftime("%Y%m%d") # 실제 운영시 사용
#TARGET_DATE = "20260205" 

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
        # 8seconds가 테이블에 없을 경우 초기값 세팅
        cur.execute("INSERT INTO brand_sequences (brand_name, last_seq) VALUES (%s, %s) RETURNING 1", (BRAND_NAME.upper(), count))
        start_num = 1
    else:
        start_num = result[0]
    conn.commit()
    cur.close()
    conn.close()
    return start_num

# --- [3. 8seconds 맞춤형 파싱 UDF] ---

def parse_8s_details(html_content, file_path):
    if not html_content or len(html_content) < 100: 
        return "unknown", "unknown", 0, "내용없음", None
    
    # 1. 모델 코드 (파일명에서 추출)
    model_code = "unknown"
    try:
        file_name = file_path.split('/')[-1]
        model_code = file_name.split('_')[3] # 4번째 덩어리
    except:
        pass
    model_match = re.search(r"GM\d+", file_path)
    if model_match:
        model_code = model_match.group(0)
    
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 2. 상품명 (전달해주신 div class="gods-name" 추출)
    name_tag = soup.find("div", class_="gods-name")
    if not name_tag:
        name_tag = soup.find("div", id="goodDtlTitle")
    prod_name = name_tag.get_text(strip=True) if name_tag else "unknown"

    # 3. 가격 (class="price" 추출 및 콤마 제거)
    base_price = 0
    price_tag = soup.select_one(".price-info .price")
    if price_tag:
        try:
            # "99,900" -> "99900" -> 99900 변환
            price_text = re.sub(r"[^\d]", "", price_tag.get_text())
            base_price = int(price_text)
        except:
            base_price = 0

    # 백업용 가격 셀렉터 (SSF샵 기준)
    if base_price == 0:
        price_tag = soup.select_one(".price em") or \
                    soup.select_one(".sale_price") or \
                    soup.select_one(".discount_price")
        if price_tag:
            base_price = int(re.sub(r"[^\d]", "", price_tag.get_text()))

    # 4. 상세 설명
    desc_tag = soup.find("meta", property="og:description")
    detail_desc = desc_tag['content'] if desc_tag else "상세 설명 없음"

    # 5. 이미지 URL
    img_tag = soup.find("meta", property="og:image")
    img_url = img_tag['content'] if img_tag else None

    return model_code, prod_name, base_price, detail_desc, img_url


#######################
info_schema = StructType([
    StructField("model_code", StringType(), True),
    StructField("prod_name", StringType(), True),
    StructField("base_price", IntegerType(), True),
    StructField("detail_desc", StringType(), True),
    StructField("img_url", StringType(), True)
])
parse_udf = udf(parse_8s_details, info_schema)

# --- [4. ETL 로직] ---

# 1. HDFS에서 8seconds HTML 읽기
input_path = f"{HDFS_BASE}{RAW_PATH}/*.html"
print(f"📂 Reading from: {input_path}")

raw_df = spark.read.text(input_path, wholetext=True) \
              .select(input_file_name().alias("file_path"), col("value").alias("html_content"))

# 2. 파싱 수행
parsed_df = raw_df.withColumn("info", parse_udf(col("html_content"), col("file_path")))

# ⚠️ 이 부분에 있던 final_df.cache()는 아직 변수가 생성 전이라 삭제했습니다.
parsed_df.cache() # 차라리 parsed_df를 캐시하는 것이 효율적입니다.

total_count = parsed_df.count()
if total_count == 0:
    print(f"❌ {BRAND_NAME} 데이터를 찾을 수 없습니다.")
    spark.stop()
    exit()

# [수정 2] 시퀀스 번호를 먼저 받아옵니다 (그래야 product_id를 만들 수 있음)
start_seq = get_and_update_sequence(total_count)

# 이미지 저장 폴더 생성
subprocess.run(f"docker exec {CONTAINER_NAME} hdfs dfs -mkdir -p {IMAGE_DIR}", shell=True)

## [수정 3] final_df를 한 번만, 올바른 순서로 정의합니다.
# 성별(gender) + 카테고리(sub_category)를 합쳐서 Men_Top 형태로 만듭니다.
# 모든 .withColumn이 수직으로 예쁘게 정렬되어야 합니다.
final_df = parsed_df.withColumn("idx", monotonically_increasing_id() + 1) \
    .withColumn("product_id", format_string(f"{BRAND_PREFIX}%04d", col("idx") + start_seq - 1)) \
    .withColumn("img_hdfs_path", concat(lit(IMAGE_DIR), lit(f"/{BRAND_NAME}_"), col("info.model_code"), lit(".jpg"))) \
    .withColumn("gender", regexp_extract(col("file_path"), r"8seconds_([A-Za-z]+)_", 1)) \
    .withColumn("sub_category", regexp_extract(col("file_path"), r"8seconds_[A-Za-z]+_([A-Za-z]+)_", 1)) \
    .withColumn("category_code", concat(col("gender"), lit("_"), col("sub_category")))

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

# --- [7. 이미지 처리] ---
image_list = final_df.select("info.img_url", "info.model_code").collect()
for row in image_list:
    if row.img_url and row.model_code != "unknown":
        hdfs_target_path = f"{IMAGE_DIR}/{row.model_code}.jpg"
        cmd = f"wget -qO- {row.img_url} | docker exec -i {CONTAINER_NAME} hdfs dfs -put - {hdfs_target_path}"
        subprocess.run(cmd, shell=True)

print(f"✅ {BRAND_NAME.upper()} {total_count}건 적재 및 이미지 저장 완료!")
spark.stop()