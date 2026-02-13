# spark/jobs/uniqlo_batch.py

from pyspark.sql import SparkSession
from pyspark.sql.functions import (
    col, lit, current_timestamp, input_file_name, row_number, udf
)
from spark.parsers.uniqlo_parser import UniqloParser
from pyspark.sql.types import (
    StructType, StructField, StringType, IntegerType, ArrayType
)

from pyspark.sql.window import Window
from spark.common.filename_parser import parse_filename_strict
#from bs4 import BeautifulSoup
#import re
import os
import requests
import subprocess

# 🔑 공통 유틸
from spark.utils.id_generator import get_start_seq


# =====================================================
# 1. Spark 시작
# =====================================================
spark = SparkSession.builder \
    .appName("uniqlo-batch") \
    .config(
        "spark.mongodb.write.connection.uri",
        "mongodb://127.0.0.1:27017/lookalike.product_details"
    ) \
    .getOrCreate()

BRAND = "uniqlo"
BRAND_DB_NAME = "UNIQLO"
BRAND_PREFIX = "UQ"
TARGET_DATE = "20260205"

# =====================================================
# 2. Parser 생성
# =====================================================
parser = UniqloParser()

# =====================================================
# 3. parser 사용하는 함수 정의 (← 여기!)
# =====================================================
def parse_with_parser(html: str):
    try:
        result = parser.parse(html)
        return (
            result.get("model_code"),
            result.get("prod_name"),
            result.get("price"),
            result.get("description"),
            result.get("image_urls")
        )
    except Exception:
        return (None, None, None, None, [])

# =====================================================
# 4. HTML 읽기 (HDFS)
# =====================================================
raw_df = spark.read.text(
    f"hdfs://localhost:9000/lookalike/raw/{BRAND}/{TARGET_DATE}/*.html",
    wholetext=True
).select(
    input_file_name().alias("file_path"),
    col("value").alias("html")
)


# =====================================================
# 5. 파일명 → category_code 추출
# 예: uniqloMen_Bottom_E418910-000_20260204.html
# =====================================================
def extract_category(path: str) -> str:
    filename = os.path.basename(path)
    parts = filename.split("_")
    if len(parts) >= 3:
        return f"{parts[1]}_{parts[2]}"   # Men_Bottom
    return "UNKNOWN"

category_udf = spark.udf.register(
    "category_udf", extract_category
)

raw_df = raw_df.withColumn(
    "category_code",
    category_udf(col("file_path"))
)

# =====================================================
# 6 HTML 파싱 (UNIQLO 전용)
# =====================================================
from pyspark.sql.types import (
    StructType, StructField, StringType, IntegerType, ArrayType
)
from spark.parsers.uniqlo_parser import UniqloParser

# 🔑 파서 객체 (드라이버에서 1번만 생성)
parser = UniqloParser()

# def parse_with_parser(html: str):
#     result = parser.parse(html)
#     return (
#         result.get("model_code"),
#         result.get("product_name"),
#         result.get("price"),
#         result.get("description"),
#         result.get("image_urls")
#     )

schema = StructType([
    StructField("model_code", StringType(), True),
    StructField("prod_name", StringType(), True),
    StructField("base_price", IntegerType(), True),
    StructField("detail_desc", StringType(), True),
    StructField("img_urls", ArrayType(StringType()), True)
])

parse_udf = spark.udf.register(
    "parse_udf",
    parse_with_parser,
    schema
)

parsed_df = raw_df.withColumn(
    "parsed",
    parse_udf(col("html"))
).select(
    "file_path",
    "category_code",
    col("parsed.*")
)

# =====================================================
# 7. product_id 채번 (🔥 핵심)
# =====================================================
batch_count = parsed_df.count()

start_seq = get_start_seq(
    brand_name=BRAND_DB_NAME,
    batch_count=batch_count
)

window = Window.orderBy(lit(1))

parsed_df = parsed_df.withColumn(
    "product_id_num",
    row_number().over(window) + start_seq - 1
)

parsed_df = parsed_df.withColumn(
    "product_id",
    lit(BRAND_PREFIX) + col("product_id_num").cast("string")
)


# =====================================================
# 8. 이미지 다운로드 + HDFS 저장
# 경로: /lookalike/image/{brand}/{product_id}_{seq}.jpg
# =====================================================
def save_images(product_id: str, urls: list):
    hdfs_dir = f"/lookalike/image/{BRAND}/"
    subprocess.run(["hdfs", "dfs", "-mkdir", "-p", hdfs_dir])

    saved_paths = []

    for idx, url in enumerate(urls, start=1):
        local_path = f"/tmp/{product_id}_{idx}.jpg"
        hdfs_path = f"{hdfs_dir}{product_id}_{idx}.jpg"

        try:
            r = requests.get(url, timeout=5)
            with open(local_path, "wb") as f:
                f.write(r.content)

            subprocess.run(["hdfs", "dfs", "-put", "-f", local_path, hdfs_path])
            saved_paths.append(hdfs_path)
        except Exception:
            pass
        finally:
            if os.path.exists(local_path):
                os.remove(local_path)

    return saved_paths


save_udf = spark.udf.register("save_udf", save_images, "ARRAY<STRING>")

parsed_df = parsed_df.withColumn(
    "img_hdfs_paths",
    save_udf(col("product_id"), col("img_urls"))
)

parsed_df = parsed_df.withColumn(
    "main_img_path",
    col("img_hdfs_paths")[0]
)


# =====================================================
# 9 PostgreSQL 저장 (Products)
# =====================================================
pg_df = parsed_df.select(
    "product_id",
    "model_code",
    lit(BRAND_DB_NAME).alias("brand_name"),
    "prod_name",
    "base_price",
    "category_code",
    col("main_img_path").alias("img_hdfs_path"),
    current_timestamp().alias("create_dt"),
    current_timestamp().alias("update_dt")
)

pg_df.write.format("jdbc") \
    .option("url", "jdbc:postgresql://localhost:5432/datadb") \
    .option("dbtable", "products") \
    .option("user", "datauser") \
    .option("password", "DataPass2024!") \
    .mode("append") \
    .save()


# =====================================================
# 10. MongoDB 저장 (Product_details)
# =====================================================
mongo_df = parsed_df.select(
    "product_id",
    "model_code",
    lit(BRAND_DB_NAME).alias("brand_name"),
    col("img_hdfs_paths").alias("img_hdfs_path"),
    "detail_desc",
    current_timestamp().alias("create_dt")
)

mongo_df.write.format("mongodb") \
    .mode("append") \
    .save()


# =====================================================
# 11. 종료
# =====================================================
spark.stop()
print("✅ UNIQLO 배치 처리 완료")
