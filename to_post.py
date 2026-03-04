import json
import psycopg2
from pathlib import Path

JSON_DIR = "/data/zara_process/20260303/json"

conn = psycopg2.connect(
    host="localhost",      # ✅ 포트가 열려 있으니 localhost 사용 가능
    port=5432,
    dbname="datadb",
    user="datauser",
    password="DataPass2026!"     # 여기에 실제 비밀번호
)

sql = """
INSERT INTO "Products"
(product_id, model_code, brand_name, prod_name, base_price,
 gender, category_code, img_hdfs_path, origine_url, create_dt, update_dt)
VALUES (%(product_id)s, %(model_code)s, %(brand_name)s, %(prod_name)s, %(base_price)s,
        %(gender)s, %(category_code)s, %(img_hdfs_path)s, %(origine_url)s,
        %(create_dt)s, %(update_dt)s)
ON CONFLICT (product_id)
DO UPDATE SET
    model_code = EXCLUDED.model_code,
    brand_name = EXCLUDED.brand_name,
    prod_name = EXCLUDED.prod_name,
    base_price = EXCLUDED.base_price,
    gender = EXCLUDED.gender,
    category_code = EXCLUDED.category_code,
    img_hdfs_path = EXCLUDED.img_hdfs_path,
    origine_url = EXCLUDED.origine_url,
    update_dt = EXCLUDED.update_dt;
"""

with conn:
    with conn.cursor() as cur:
        for file in Path(JSON_DIR).glob("*.json"):
            with open(file, "r", encoding="utf-8") as f:
                data = json.load(f)

            cur.execute(sql, data)
            print(f"Inserted/Updated: {data['product_id']}")

conn.close()
print("✅ 모든 JSON 데이터 저장 완료")
