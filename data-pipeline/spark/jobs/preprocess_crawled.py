from pyspark.sql import SparkSession
from pyspark.sql.functions import udf, lit, col, struct, array
from pyspark.sql.types import StringType, ArrayType, IntegerType, StructType, StructField
import os
import re
import uuid
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# 1. Spark 세션 설정
spark = SparkSession.builder \
    .appName("Lookalike_Daily_ETL_Full_Pipeline") \
    .config("spark.mongodb.write.connection.uri", "mongodb://127.0.0.1:27017/lookalike.product_details") \
    .config("spark.jars.packages", "org.postgresql:postgresql:42.5.0,org.mongodb.spark:mongo-spark-connector_2.12:10.1.1") \
    .getOrCreate()

# 2. 날짜 및 경로 설정
target_date = datetime.now().strftime("%Y%m%d")
raw_html_path = f"hdfs://localhost:9000/lookalike/raw/*/{target_date}/*.html"

# --- [3. UDF 및 함수 정의 영역: 실행 전 모두 정의되어야 함] ---

# 카테고리 분류
def classify_category(file_path):
    file_name = file_path.split('/')[-1].lower()
    gender = "woman" if "woman" in file_name or "women" in file_name else "man" if "man" in file_name or "men" in file_name else "unknown"
    
    category_type = "etc"
    if any(k in file_name for k in ["outer", "jacket", "coat"]): category_type = "outer"
    elif any(k in file_name for k in ["pants", "bottom", "jeans"]): category_type = "pants"
    elif any(k in file_name for k in ["shirt", "top", "blouse", "t-shirt"]): category_type = "shirt"
    
    return f"{category_type}_{gender}" if gender != "unknown" and category_type != "etc" else category_type

classify_udf = udf(classify_category, StringType())

# 브랜드 추출
def extract_brand(path):
    parts = path.split('/')
    try: return parts[parts.index('raw') + 1]
    except: return "unknown"

brand_udf = udf(extract_brand, StringType())

# HTML 상세 정보 파싱
def extract_info_from_html(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    name_tag = soup.find(['h1', 'h2']) or soup.select_one('.prod_name, .title')
    prod_name = name_tag.get_text(strip=True) if name_tag else "unknown_product"
    
    price_tag = soup.select_one('.price, .sale_price, .amount')
    price_text = price_tag.get_text(strip=True) if price_tag else "0"
    base_price = int(re.sub(r'[^0-9]', '', price_text)) if price_text != "0" else 0
    
    model_tag = soup.find('meta', {'property': 'product:item_id'})
    model_code = model_tag['content'] if model_tag else str(uuid.uuid4())[:10]
    
    return model_code, prod_name, base_price

info_schema = StructType([
    StructField("model_code", StringType(), True),
    StructField("prod_name", StringType(), True),
    StructField("base_price", IntegerType(), True)
])
info_udf = udf(extract_info_from_html, info_schema)

# 이미지 다운로드 및 HDFS 저장

###
import subprocess
import requests
import uuid
from bs4 import BeautifulSoup
from pyspark.sql.functions import udf
from pyspark.sql.types import ArrayType, StringType

HDFS_BIN = ["docker", "exec", "-i", "namenode-main", "hdfs"]

def extract_and_save_images(html_content, file_path, target_date="20260205"):
    # BeautifulSoup 파싱
    soup = BeautifulSoup(html_content, 'html.parser')
    image_urls = [img.get('src') for img in soup.find_all('img', src=True) if img.get('src')]
    
    downloaded_paths = []
    # brand_name 추출 로직 (함수 내부에 있거나 미리 정의되어야 함)
    brand_name = file_path.split('/')[-3] # 예: /lookalike/raw/zara/... 에서 zara 추출
    hdfs_image_dir = f"/lookalike/raw/{brand_name}/{target_date}/image/"

    # HDFS 디렉토리 먼저 생성 (없을 경우 대비)
    subprocess.run(HDFS_BIN + ["dfs", "-mkdir", "-p", hdfs_image_dir])

    for img_url in list(set(image_urls)):
        if img_url.startswith('//'): img_url = 'https:' + img_url
        if not img_url.startswith('http'): continue # 잘못된 URL 스킵
            
        try:
            resp = requests.get(img_url, stream=True, timeout=5)
            resp.raise_for_status()
            
            ext = img_url.split('.')[-1].split('?')[0][:3]
            if len(ext) > 3 or not ext: ext = 'jpg'
            
            file_name = f"{brand_name}_{uuid.uuid4()}.{ext}"
            full_hdfs_path = hdfs_image_dir + file_name
            
            # 파이프를 이용해 HDFS에 바로 쓰기 (메모리 효율적)
            # hdfs dfs -put - <target_path> 는 표준 입력을 받아 HDFS 파일로 저장합니다.
            process = subprocess.Popen(
                HDFS_BIN + ["dfs", "-put", "-", full_hdfs_path], 
                stdin=subprocess.PIPE
            )
            
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    process.stdin.write(chunk)
            
            process.stdin.close()
            process.wait()
            
            if process.returncode == 0:
                downloaded_paths.append(full_hdfs_path)
                
        except Exception as e:
            continue
            
    return downloaded_paths

# UDF 등록 시 target_date 등 외부 변수가 필요하면 lambda를 활용하세요.
images_udf = udf(lambda h, f: extract_and_save_images(h, f, target_date), ArrayType(StringType()))
###

# --- [4. ETL 프로세스 시작] ---

print(f"--- {target_date} 파이프라인 가동 ---")
raw_rdd = spark.sparkContext.wholeTextFiles(raw_html_path)
df = raw_rdd.toDF(["file_path", "html_content"])

# 공통 가공 데이터프레임 생성
processed_df = df.withColumn("brand_name", brand_udf("file_path")) \
                 .withColumn("category_code", classify_udf("file_path")) \
                 .withColumn("process_date", lit(target_date)) \
                 .withColumn("info", info_udf("html_content")) \
                 .withColumn("image_paths", images_udf("html_content", "file_path"))

processed_df.cache()

# 5. PostgreSQL 적재용 변환
print("🐘 PostgreSQL 적재 중...")

pg_ready_df = processed_df.select(
    col("info.model_code").alias("model_code"),
    col("brand_name").alias("brand_name"),
    col("info.prod_name").alias("prod_name"),
    col("info.base_price").cast("int").alias("base_price"),
    col("category_code").alias("category_code"),
    # 여기를 img_hdfs_path에서 main_img_path로 수정합니다!
    col("image_paths")[0].alias("main_img_path")
)

# PostgreSQL 저장
pg_ready_df.write \
    .format("jdbc") \
    .option("url", "jdbc:postgresql://localhost:5432/datadb") \
    .option("driver", "org.postgresql.Driver") \
    .option("dbtable", "products") \
    .option("user", "datauser") \
    .option("password", "DataPass2024!") \
    .mode("append").save()

print("✅ PostgreSQL 적재 완료!")
# 6. PostgreSQL에서 생성된 product_id 가져오기 (연동 핵심)
print("🔗 PostgreSQL에서 생성된 ID 매핑 중...")
# DB에서 방금 들어간 ID와 model_code를 가져옴
pg_ids_df = spark.read \
    .format("jdbc") \
    .option("url", "jdbc:postgresql://localhost:5432/datadb") \
    .option("driver", "org.postgresql.Driver") \
    .option("dbtable", "products") \
    .option("user", "datauser") \
    .option("password", "DataPass2024!") \
    .load() \
    .select(col("product_id"), col("model_code").cast("string").alias("db_model_code"))

# 7. MongoDB 적재용 데이터 조인 및 변환
print("🍃 MongoDB 적재 중 (product_details) ...")
from pyspark.sql.functions import current_timestamp, array

# 원본 가공 데이터와 DB ID 조인
mongo_ready_df = processed_df.join(
    pg_ids_df, 
    processed_df.info.model_code == pg_ids_df.db_model_code, 
    "inner"
).select(
    col("product_id"),                             # PostgreSQL의 PK와 연동
    col("info.model_code").alias("model_code"),
    col("brand_name").alias("brand_name"),
    col("image_paths").alias("img_hdfs_path"),    # MongoDB는 array 타입
    col("html_content").alias("raw_html"),
    lit(None).cast("string").alias("detail_desc"), 
    array().cast("array<string>").alias("keywords"), 
    current_timestamp().alias("create_dt")        # 입력 시각
)

# MongoDB 저장
mongo_ready_df.write \
    .format("mongodb") \
    .option("spark.mongodb.write.connection.uri", "mongodb://127.0.0.1:27017/lookalike.product_details") \
    .mode("append").save()

print(f"🚀 {target_date} 모든 작업 완료!")
spark.stop()