# spark/job_product_ingest.py

from common.filename_parser import parse_filename
from parsers.uniqlo_parser import UniqloParser
from common.image_utils import download_image
from spark.common.filename_parser import parse_filename_strict


def process_html(path, brand):
    filename = path.split("/")[-1]
    meta = parse_filename(filename, brand)

    html = open(path).read()
    parser = UniqloParser()
    parsed = parser.parse(html)

    # product_id 생성 (brand_sequences)
    # 이미지 저장
    # PostgreSQL insert
    # MongoDB insert
