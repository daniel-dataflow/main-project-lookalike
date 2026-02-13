# jobs/fashion_batch_job.py

from pyspark.sql import SparkSession
from parsers.uniqlo import UniqloParser

parser = UniqloParser()

raw_df = spark.read.text(
    "hdfs://localhost:9000/lookalike/raw/uniqlo/20260205/*.html",
    wholetext=True
)

def parse_udf_func(file_path, html):
    return parser.parse(file_path, html)

# 이후:
# 1) Spark DF
# 2) ID 채번
# 3) Products write
# 4) Product_details write
