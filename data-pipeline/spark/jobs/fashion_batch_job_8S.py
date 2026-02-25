<<<<<<< HEAD
from pyspark.sql import SparkSession
from pyspark.sql.functions import (lit, col, current_timestamp, concat, format_string, 
                                   input_file_name, regexp_extract, lower, concat_ws, element_at, 
                                   coalesce, to_json, row_number)
from pyspark.sql.window import Window
import psycopg2
import datetime
import os
import sys
import requests
from pytz import timezone

# 스파크 워커 내 파이썬 버전 에러 방지
os.environ["PYSPARK_PYTHON_VERSION_CHECK"] = "0"
=======
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
import datetime
# 26.2.21
import requests
from hdfs import InsecureClient
>>>>>>> e3af4a39a663b22702673a7773f76cf6fa49757b

# --- [1. 설정 정보] ---
MONGO_IP = "mongo-main"
BRAND_NAME = "8seconds"
BRAND_PREFIX = "8S"
<<<<<<< HEAD

kst = timezone('Asia/Seoul')
TARGET_DATE = datetime.datetime.now(kst).strftime("%Y%m%d")

PG_HOST = "postgres-main"  
PG_DB = "datadb"       
PG_USER = "datauser"
PG_PASS = "DataPass2026!"  

MONGO_USER = "datauser"
MONGO_PASS = "DataPass2026!"

HDFS_BASE = "hdfs://namenode-main:9000"
RAW_PATH = f"/raw/{BRAND_NAME}/{TARGET_DATE}"
IMAGE_DIR = f"/raw/{BRAND_NAME}/image"

# WebHDFS 설정
WEBHDFS_HOST = "namenode-main"
WEBHDFS_PORT = "9870"
WEBHDFS_USER = "root"

spark = SparkSession.builder \
    .appName("FashionBatchJob8S") \
    .config("spark.mongodb.write.connection.uri", f"mongodb://{MONGO_USER}:{MONGO_PASS}@{MONGO_IP}:27017/{PG_DB}?authSource=admin") \
    .getOrCreate()

# --- [2. PostgreSQL 시퀀스 관리] ---
try:
    # brand_sequences 테이블이 없으면 자동 생성
    init_conn = psycopg2.connect(host=PG_HOST, database=PG_DB, user=PG_USER, password=PG_PASS)
    init_cur = init_conn.cursor()
    init_cur.execute("""
        CREATE TABLE IF NOT EXISTS brand_sequences (
            brand_name VARCHAR(50) PRIMARY KEY,
            last_seq INTEGER DEFAULT 0
        );
    """)
    init_conn.commit()
    
    # 현재 시퀀스 조회
    seq_df = spark.read.format("jdbc") \
=======
# 현재 실행 시점의 날짜 (HDFS 경로와 일치해야 함)
TARGET_DATE = datetime.datetime.now().strftime("%Y%m%d")
#TARGET_DATE = "20260210" 

# DB 접속 정보 (본인의 환경에 맞게 수정 확인)
PG_HOST = "postgresql"
PG_DB = "datadb"
PG_USER = "datauser"
PG_PASS = "DataPass2026!"

MONGO_URI = "mongodb://datauser:DataPass2026!@mongo-main:27017"

HDFS_BASE = "hdfs://namenode-main:9000"
# [수정] 26.2.21 WebHDFS 접속용 URL 추가 (포트 9870)
#HDFS_WEB_URL = "http://namenode:9870"
HDFS_WEB_URL = "http://namenode-main:9870"
RAW_PATH = f"/raw/{BRAND_NAME}/{TARGET_DATE}"
IMAGE_DIR = f"/raw/{BRAND_NAME}/image"
CONTAINER_NAME = "namenode-main"

spark = SparkSession.builder \
    .appName("FashionBatchJob8S") \
    .config("spark.jars.packages", "org.postgresql:postgresql:42.6.0,org.mongodb.spark:mongo-spark-connector_2.12:10.1.1") \
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
# 26.2.18
if not row:
    print(f"✨ {BRAND_NAME} sequence not found. Registering new brand in DB...")
    # 1. 새 데이터를 담은 DF 생성
    new_seq_data = [(BRAND_NAME.upper(), 0)]
    new_seq_df = spark.createDataFrame(new_seq_data, ["brand_name", "last_seq"])
    
    # 2. DB에 실제 INSERT 수행 (이게 빠져있었습니다!)
    new_seq_df.write.format("jdbc") \
>>>>>>> e3af4a39a663b22702673a7773f76cf6fa49757b
        .option("url", f"jdbc:postgresql://{PG_HOST}:5432/{PG_DB}") \
        .option("dbtable", "brand_sequences") \
        .option("user", PG_USER) \
        .option("password", PG_PASS) \
        .option("driver", "org.postgresql.Driver") \
<<<<<<< HEAD
        .load()

    row = seq_df.filter(col("brand_name") == BRAND_NAME.upper()).select("last_seq").collect()

    if not row:
        print(f"✨ {BRAND_NAME} sequence not found. Registering...")
        init_cur.execute("INSERT INTO brand_sequences (brand_name, last_seq) VALUES (%s, 0)", (BRAND_NAME.upper(),))
        init_conn.commit()
        start_seq = 1
    else:
        start_seq = row[0]['last_seq'] + 1
    init_cur.close()
    init_conn.close()

except Exception as e:
    print(f"⚠️ Sequence check failed: {e}. Starting from 1.")
    start_seq = 1

print(f"🚀 Job Start | Brand: {BRAND_NAME} | Seq: {start_seq} | Date: {TARGET_DATE}")

# --- [3. ETL 로직] ---
input_path = f"{HDFS_BASE}{RAW_PATH}/*.json"
raw_df = spark.read.option("multiLine", "true") \
                   .option("inferSchema", "true") \
=======
        .mode("append") \
        .save()
    
    start_seq = 1
else:
    start_seq = row[0]['last_seq'] + 1
##
#start_seq = row[0]['last_seq'] + 1 if row else 1
print(f"🚀 Starting {BRAND_NAME} job from sequence: {start_seq}")

# --- [4. ETL 로직] ---
input_path = f"{HDFS_BASE}{RAW_PATH}/*.json"
raw_df = spark.read.option("multiLine", "true") \
                   .option("inferSchema", "true") \
                   .option("samplingRatio", "1.0") \
>>>>>>> e3af4a39a663b22702673a7773f76cf6fa49757b
                   .json(input_path) \
                   .withColumn("file_path", input_file_name())

windowSpec = Window.partitionBy(lit(BRAND_NAME)).orderBy(col("goodsNo"))

<<<<<<< HEAD
# 데이터 처리 (파일명 생성 및 HDFS 경로 지정)
processed_df = raw_df.withColumn("idx", row_number().over(windowSpec)) \
    .withColumn("product_id", format_string(f"{BRAND_PREFIX}%04d", col("idx").cast("int") + start_seq - 1)) \
    .withColumn("target_img_url", 
        coalesce(
            element_at(col("goodsImages"), 26), # 1순위: 고화질
            element_at(col("goodsImages"), 2)   # 2순위: 썸네일
        )
    ) \
    .withColumn("gender", lower(regexp_extract(col("file_path"), r"8seconds_([^_]+)_", 1))) \
    .withColumn("sub_category", lower(regexp_extract(col("file_path"), r"8seconds_[^_]+_([^_]+)_", 1))) \
    .withColumn("category_code", concat(col("gender"), lit("_"), col("sub_category"))) \
    .withColumn("new_filename", concat_ws("_", lit(BRAND_NAME.lower()), col("gender"), col("sub_category"), col("goodsNo"))) \
    .withColumn("img_hdfs_path", concat(lit(IMAGE_DIR), lit("/"), col("new_filename"), lit(".jpg")))
=======
processed_df = raw_df.withColumn("idx", row_number().over(windowSpec)) \
    .withColumn("product_id", format_string(f"{BRAND_PREFIX}%04d", col("idx").cast("int") + start_seq - 1)) \
    .withColumn("img_hdfs_path", concat(lit(IMAGE_DIR), lit("/"), col("goodsNo"), lit(".jpg"))) \
    .withColumn("gender", lower(regexp_extract(col("file_path"), r"8seconds_([^_]+)_", 1))) \
    .withColumn("sub_category", lower(regexp_extract(col("file_path"), r"8seconds_[^_]+_([^_]+)_", 1))) \
    .withColumn("category_code", concat(col("gender"), lit("_"), col("sub_category")))
>>>>>>> e3af4a39a663b22702673a7773f76cf6fa49757b

processed_df.cache()
total_count = processed_df.count()

<<<<<<< HEAD
if total_count == 0:
    print("❌ 수집된 데이터가 없습니다. 잡을 종료합니다.")
    spark.stop()
    sys.exit(0)

print(f"📊 처리할 데이터: {total_count} 건")

# --- [4. PostgreSQL 적재] ---
pg_data = processed_df.select(
    col("product_id"),
    col("goodsNo").alias("model_code"),
    lit(BRAND_NAME.upper()).alias("brand"),
    col("goodsNm").alias("product_name"),
    col("category_code").alias("category"),
    coalesce(col("price").cast("int"), lit(0)).alias("price"),
    col("img_hdfs_path").alias("local_image_path"),
    current_timestamp().alias("created_at")
=======
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
>>>>>>> e3af4a39a663b22702673a7773f76cf6fa49757b
)

pg_data.write.format("jdbc") \
    .option("url", f"jdbc:postgresql://{PG_HOST}:5432/{PG_DB}") \
<<<<<<< HEAD
    .option("dbtable", "fashion_products") \
=======
    .option("dbtable", "products") \
>>>>>>> e3af4a39a663b22702673a7773f76cf6fa49757b
    .option("user", PG_USER) \
    .option("password", PG_PASS) \
    .option("driver", "org.postgresql.Driver") \
    .mode("append") \
    .save()

<<<<<<< HEAD
# --- [5. MongoDB 적재] ---
=======
# --- [6. MongoDB 적재] ---
>>>>>>> e3af4a39a663b22702673a7773f76cf6fa49757b
mongo_data = processed_df.select(
    col("product_id"),
    col("goodsNo").alias("model_code"),
    lit(BRAND_NAME.upper()).alias("brand_name"),
    col("goodsNm").alias("prod_name"),
<<<<<<< HEAD
    to_json(col("goodsMaterial")).alias("detail_desc"),
    col("img_hdfs_path"),
    col("goodsImages").alias("all_images"),
=======
    filter_udf(to_json(col("goodsMaterial"))).alias("detail_desc"),
    coalesce(col("price").cast("int"), lit(0)).alias("base_price"),
    col("img_hdfs_path"),
>>>>>>> e3af4a39a663b22702673a7773f76cf6fa49757b
    current_timestamp().alias("create_dt")
)

mongo_data.write.format("mongodb") \
<<<<<<< HEAD
    .option("spark.mongodb.write.database", PG_DB) \
    .option("spark.mongodb.write.collection", "fashion_metadata") \
    .mode("append") \
    .save()

# --- [6. 이미지 다운로드 및 WebHDFS 저장] ---

def upload_to_hdfs_via_webhdfs(local_data, hdfs_path, filename):
    """WebHDFS REST API를 사용하여 파일 업로드"""
    try:
        # 1. CREATE 요청 (리다이렉트 URL 받기)
        create_url = f"http://{WEBHDFS_HOST}:{WEBHDFS_PORT}/webhdfs/v1{hdfs_path}/{filename}?op=CREATE&overwrite=true&user.name={WEBHDFS_USER}"
        
        response = requests.put(create_url, allow_redirects=False)
        
        if response.status_code == 307:  # Temporary Redirect
            # 2. DataNode로 리다이렉트된 URL에 PUT 요청
            datanode_url = response.headers['Location']
            put_response = requests.put(datanode_url, data=local_data)
            
            if put_response.status_code in [200, 201]:
                return True
            else:
                print(f"❌ HDFS 업로드 실패 (DataNode): {put_response.status_code}")
                return False
        else:
            print(f"❌ HDFS CREATE 요청 실패: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ HDFS 업로드 예외: {e}")
        return False

# 이미지 디렉토리 생성 (WebHDFS mkdir)
try:
    mkdir_url = f"http://{WEBHDFS_HOST}:{WEBHDFS_PORT}/webhdfs/v1{IMAGE_DIR}?op=MKDIRS&user.name={WEBHDFS_USER}"
    requests.put(mkdir_url)
except:
    pass

image_list = processed_df.select("target_img_url", "new_filename", "goodsNo").collect()
print(f"📸 이미지 다운로드 시작: {len(image_list)} 건")

success_count = 0
fail_count = 0

for r in image_list:
    if r.target_img_url and r.new_filename:
        filename = f"{r.new_filename}.jpg"
        try:
            # 1. 쇼핑몰 서버에서 이미지 다운로드 (가짜 브라우저 헤더 필수)
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            response = requests.get(r.target_img_url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                # 2. 다운받은 이미지를 WebHDFS를 통해 하둡으로 전송
                if upload_to_hdfs_via_webhdfs(response.content, IMAGE_DIR, filename):
                    success_count += 1
                else:
                    fail_count += 1
                    print(f"❌ HDFS 업로드 실패: {r.goodsNo}")
            else:
                fail_count += 1
                print(f"❌ 이미지 다운로드 실패 ({r.goodsNo}): HTTP {response.status_code}")
                
        except Exception as e:
            fail_count += 1
            print(f"❌ 이미지 처리 예외 발생 ({r.goodsNo}): {e}")

print(f"📸 이미지 처리 완료: 성공 {success_count} / 실패 {fail_count}")

# --- [7. 시퀀스 업데이트] ---
try:
    new_last_seq = start_seq + total_count - 1
    conn = psycopg2.connect(host=PG_HOST, database=PG_DB, user=PG_USER, password=PG_PASS)
    cur = conn.cursor()
    cur.execute("UPDATE brand_sequences SET last_seq = %s WHERE brand_name = %s", (new_last_seq, BRAND_NAME.upper()))
    conn.commit()
    cur.close()
    conn.close()
    print(f"✅ 시퀀스 업데이트 완료: {new_last_seq}")
except Exception as e:
    print(f"❌ 시퀀스 업데이트 오류: {e}")

print(f"🏁 {BRAND_NAME.upper()} 잡 완료! 총 {total_count} 건 처리됨.")
spark.stop()
=======
    .option("spark.mongodb.write.connection.uri", MONGO_URI) \
    .option("authSource", "admin") \
    .option("database", "datadb") \
    .option("collection", "product_details") \
    .mode("append") \
    .save()
print("✅ MongoDB 적재 완료")

# --- [7. 이미지 처리] ---

# 크롤러가 날짜 폴더까지만 만들었을 경우를 대비해 /image 폴더를 생성합니다.
# mkdir_cmd = f"docker exec -i {CONTAINER_NAME} hdfs dfs -mkdir -p {IMAGE_DIR}"
# subprocess.run(mkdir_cmd, shell=True)
# print(f"📂 [8Seconds] HDFS 이미지 디렉토리 확인/생성 완료: {IMAGE_DIR}")

# # goodsImages 배열에서 첫 번째 이미지 URL 추출
# image_list = processed_df.select(element_at(col("goodsImages"), 1).alias("main_img"), col("goodsNo")).collect()

# for r in image_list:
#     if r.main_img and r.goodsNo:
#         hdfs_target_path = f"{IMAGE_DIR}/{r.goodsNo}.jpg"
#         cmd = f"wget -qO- --header='User-Agent: Mozilla/5.0' '{r.main_img}' | docker exec -i {CONTAINER_NAME} hdfs dfs -put - {hdfs_target_path}"
#         subprocess.run(cmd, shell=True)
try:
    hdfs_client = InsecureClient(HDFS_WEB_URL, user="root")
    hdfs_client.makedirs(IMAGE_DIR)
    
    image_list = processed_df.select(element_at(col("goodsImages"), 1).alias("main_img"), col("product_id")).collect()
    
    print(f"📸 이미지 다운로드 및 HDFS 직접 전송 시작 (총 {len(image_list)}건)...")
    success_img = 0
    for r in image_list:
        if r.main_img:
            hdfs_target_path = f"{IMAGE_DIR}/{r.product_id}.jpg"
            try:
                # requests로 웹 이미지 다운로드
                img_content = requests.get(r.main_img, timeout=10).content
                # hdfs_client로 하둡에 직접 쓰기
                hdfs_client.write(hdfs_target_path, data=img_content, overwrite=True)
                success_img += 1
            except Exception as e:
                print(f"❌ {r.product_id} 저장 실패: {e}")
    print(f"✅ 이미지 처리 완료: {success_img}건 저장 성공")

except Exception as e:
    print(f"🚨 하둡 클라이언트 연결 실패: {e}")



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




>>>>>>> e3af4a39a663b22702673a7773f76cf6fa49757b
