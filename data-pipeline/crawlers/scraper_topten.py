import sys
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, lit, udf, input_file_name, split, reverse, regexp_replace
from pyspark.sql.types import StringType

if len(sys.argv) < 2:
    print("❌ 실행 날짜(YYYYMMDD) 인자가 필요합니다.")
    sys.exit(1)

DATE_STR = sys.argv[1]
BRAND_NAME = "topten"

# 1. 스파크 세션 및 몽고DB 설정
spark = SparkSession.builder \
    .appName(f"Fashion_Batch_Job_{BRAND_NAME.upper()}") \
    .config("spark.mongodb.write.connection.uri", "mongodb://datauser:DataPass2026!@mongo-main:27017/datadb.fashion_metadata?authSource=admin") \
    .getOrCreate()

spark.sparkContext.setLogLevel("WARN")

# HDFS 경로 설정
INPUT_PATH = f"hdfs://namenode:9000/raw/{BRAND_NAME}/{DATE_STR}/*.json"

print(f"⏳ [{BRAND_NAME.upper()}] 스파크 작업 시작... 대상 경로: {INPUT_PATH}")

try:
    # 2. JSON 데이터 읽기 (multiline 처리)
    df = spark.read.option("multiline", "true").json(INPUT_PATH)

    # 3. 파일명에서 메타데이터 추출 (예: topten_men_outer_12345.json)
    df = df.withColumn("source_file", reverse(split(input_file_name(), "/")).getItem(0))
    df = df.withColumn("gender", split(col("source_file"), "_").getItem(1))
    df = df.withColumn("category", split(col("source_file"), "_").getItem(2))
    df = df.withColumn("image_filename", regexp_replace(col("source_file"), "\\.json$", ".jpg"))

    # 4. 이미지 다운로드 UDF (HDFS WebHDFS API 사용)
    def download_image_to_hdfs(image_url, filename, date_str):
        if not image_url: return ""
        try:
            import requests
            from hdfs import InsecureClient
            
            # URL 형식이 '//' 로 시작하면 'https:' 붙여주기
            if image_url.startswith("//"):
                image_url = "https:" + image_url
                
            client = InsecureClient('http://namenode:9870', user='root')
            hdfs_path = f"/raw/{BRAND_NAME}/{date_str}/images/{filename}"
            
            res = requests.get(image_url, timeout=10)
            if res.status_code == 200:
                with client.write(hdfs_path, overwrite=True) as writer:
                    writer.write(res.content)
                return hdfs_path
        except Exception as e:
            print(f"⚠️ 이미지 다운로드 실패 ({filename}): {e}")
        return ""

    download_udf = udf(download_image_to_hdfs, StringType())

    # 5. 필요한 컬럼만 선택하고 이미지 다운로드 실행
    # 탑텐은 스크래퍼에서 상품명을 'goodsNm', 썸네일을 'thumbnailImageUrl'로 저장함
    final_df = df.select(
        col("image_filename").alias("filename"),
        lit(BRAND_NAME).alias("brand_name"),
        col("gender"),
        col("category"),
        col("goodsNm").alias("product_name"),
        col("price"),
        col("thumbnailImageUrl").alias("image_url"),
        col("scraped_at").alias("crawled_at")
    )

    # UDF를 호출하여 이미지를 HDFS에 저장하고 그 경로를 img_hdfs_path 컬럼에 기록
    final_df = final_df.withColumn("img_hdfs_path", download_udf(col("image_url"), col("filename"), lit(DATE_STR)))

    # 6. MongoDB에 저장 (Append 모드)
    final_df.write.format("mongodb") \
        .mode("append") \
        .save()

    print(f"🎉 [{BRAND_NAME.upper()}] MongoDB 적재 및 이미지 HDFS 다운로드 완료!")

except Exception as e:
    print(f"❌ 스파크 작업 중 에러 발생: {e}")

finally:
    spark.stop()