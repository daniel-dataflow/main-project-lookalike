import os
import json
import requests
from pathlib import Path
from typing import Any
from pymongo import MongoClient, UpdateOne
from elasticsearch import Elasticsearch, helpers
import logging
from dotenv import load_dotenv

load_dotenv()

class HDFSManager:
    # HDFS 파일 다운로드 및 관리를 담당하는 클래스
    VALID_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

    def __init__(self):
        self.nn_host = os.getenv("HDFS_NAMENODE_HOST", "namenode")
        self.nn_port = os.getenv("HDFS_NAMENODE_WEBUI_PORT", "9870")
        self.hdfs_user = os.getenv("HDFS_USER", "root")

    def _list_files(self, hdfs_root: str) -> list[str]:
        stack = [hdfs_root]
        out: list[str] = []
        while stack:
            cur = stack.pop()
            url = f"http://{self.nn_host}:{self.nn_port}/webhdfs/v1{cur}?op=LISTSTATUS&user.name={self.hdfs_user}"
            r = requests.get(url, timeout=30)
            r.raise_for_status()

            for fs in r.json()["FileStatuses"]["FileStatus"]:
                p = f"{cur.rstrip('/')}/{fs['pathSuffix']}"
                if fs["type"] == "DIRECTORY":
                    stack.append(p)
                elif Path(p).suffix.lower() in self.VALID_EXTS:
                    out.append(p)
        return sorted(out)

    def _download(self, hdfs_path: str, local_path: Path) -> None:
        open_url = f"http://{self.nn_host}:{self.nn_port}/webhdfs/v1{hdfs_path}?op=OPEN&user.name={self.hdfs_user}"
        r1 = requests.get(open_url, allow_redirects=False, timeout=30)
        
        loc = r1.headers.get("Location")
        if not loc:
            raise RuntimeError(f"WebHDFS OPEN failed: {hdfs_path}")
        
        local_path.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(loc, stream=True, timeout=120) as r2:
            r2.raise_for_status()
            with open(local_path, "wb") as f:
                for chunk in r2.iter_content(1024 * 1024):
                    if chunk:
                        f.write(chunk)

    def process_downloads(self, brand: str, hdfs_root: str, incoming_dir: str) -> list[dict[str, Any]]:
        files = self._list_files(hdfs_root)
        incoming = Path(incoming_dir)
        incoming.mkdir(parents=True, exist_ok=True)

        out: list[dict[str, Any]] = []
        for p in files:
            local = incoming / Path(p).name
            if not local.exists():
                self._download(p, local)
            
            out.append({
                "brand": brand,
                "hdfs_path": p,
                "local_path": str(local),
                "image_filename": local.name,
            })
        return out


class PostgresManager:
    def __init__(self):
        self.db_host = os.getenv("POSTGRES_HOST", "postgres-main")
        self.db_user = os.getenv("POSTGRES_USER", "datauser")
        self.db_name = os.getenv("POSTGRES_DB", "datadb")

        self.db_pass = os.getenv("POSTGRES_PASSWORD")
        if not self.db_pass:
            raise ValueError("환경 변수에 POSTGRES_PASSWORD가 설정되지 않았습니다.")

    def get_connection(self):
        return psycopg2.connect(
            host=self.db_host,
            database=self.db_name,
            user=self.db_user,
            password=self.db_pass
        )

    def execute_price_refinement(self) -> None:
        query = """
        BEGIN;

        -- 1. 두 원본 테이블의 이름을 raw로 변경
        ALTER TABLE products RENAME TO products_raw;
        ALTER TABLE naver_prices RENAME TO naver_prices_raw;

        -- 2. products_clean 생성: 가격이 정상 범위인 상품만 추출
        CREATE TABLE products_clean AS
        SELECT p.*
        FROM products_raw p
        WHERE EXISTS (
            SELECT 1
            FROM naver_prices_raw n
            WHERE n.product_id = p.product_id
              AND n.price > p.base_price * 0.4
              AND n.price < p.base_price * 2.0
        );
        ALTER TABLE products_clean ADD PRIMARY KEY (product_id);

        -- 3. naver_prices_clean 생성
        CREATE TABLE naver_prices_clean AS
        SELECT n.*
        FROM naver_prices_raw n
        JOIN products_raw p ON n.product_id = p.product_id
        WHERE n.price > p.base_price * 0.4
          AND n.price < p.base_price * 2.0;

        CREATE INDEX idx_naver_prices_clean_product_id ON naver_prices_clean(product_id);

        -- 4. naver_prices_anomaly 생성: 이상치 격리
        CREATE TABLE naver_prices_anomaly AS
        SELECT n.*
        FROM naver_prices_raw n
        JOIN products_raw p ON n.product_id = p.product_id
        WHERE n.price <= p.base_price * 0.4
           OR n.price >= p.base_price * 2.0;

        -- 5. 기존 파이프라인 연동을 위한 VIEW 생성
        CREATE VIEW products AS SELECT * FROM products_clean;
        CREATE VIEW naver_prices AS SELECT * FROM naver_prices_clean;

        COMMIT;
        """

        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            logging.info("데이터베이스 가격 정제 트랜잭션 시작...")
            cursor.execute(query)
            conn.commit()
            logging.info("가격 정제 및 뷰 생성 완료 성공.")

        except Exception as e:
            if conn:
                conn.rollback()
            logging.error(f"가격 데이터 정제 중 DB 오류 발생 (Rollback 실행): {e}")
            raise e
        
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()


class MongoDBManager:
    # MongoDB 데이터 적재를 담당하는 클래스
    def __init__(self, mongo_uri: str, db_name: str = "datadb", collection: str = "analyzed_metadata"):
        clean_uri = mongo_uri.strip('"\' ')
        self.client = MongoClient(clean_uri)
        self.col = self.client[db_name][collection]

    def upsert_image_metadata(self, json_paths: list[str]) -> int:
        if not json_paths:
            return 0

        ops = []
        for p in json_paths:
            doc = json.loads(Path(p).read_text(encoding="utf-8"))
            pid = doc["product_id"]
            doc["_id"] = pid
            ops.append(UpdateOne({"_id": pid}, {"$set": doc}, upsert=True))

        if ops:
            self.col.bulk_write(ops, ordered=False)
        return len(ops)

    def upsert_text_metadata(self, json_paths: list[str]) -> int:
        if not json_paths:
            return 0

        ops = []
        for p in json_paths:
            doc = json.loads(Path(p).read_text(encoding="utf-8"))
            pid = doc["product_id"]
            update_query = {
                "$set": {
                    "vlm_basic_info": doc.get("basic_info"),
                    "vlm_analysis": doc.get("analysis"),
                    "text_vector": doc.get("text_vector"),
                    "vlm_analyzed_at": doc.get("analyzed_at")
                }
            }
            ops.append(UpdateOne({"product_id": pid}, update_query, upsert=True))

        if ops:
            self.col.bulk_write(ops, ordered=False)
        return len(ops)


class ElasticSearchManager:
    # ElasticSearch 동기화를 담당하는 클래스
    def __init__(self, es_uri: str, mongo_uri: str):
        self.es = Elasticsearch(es_uri, basic_auth=("elastic", "password"))
        self.index_name = "multimodal_products"
        
        self.mongo_manager = MongoDBManager(mongo_uri)

    def _create_index_if_not_exists(self):
        mapping = {
            "mappings": {
                "properties": {
                    "filename":    {"type": "keyword"}, 
                    "brand":       {"type": "keyword"},
                    "category":    {"type": "keyword"},
                    "description": {"type": "text"},
                    "image_url":   {"type": "keyword"},
                    "vec_image": {
                        "type": "dense_vector", "dims": 512, 
                        "index": True, "similarity": "cosine"
                    },
                    "vec_text": {
                        "type": "dense_vector", "dims": 768, 
                        "index": True, "similarity": "cosine"
                    }
                }
            }
        }
        if not self.es.indices.exists(index=self.index_name):
            self.es.indices.create(index=self.index_name, body=mapping)
            print(f"인덱스 '{self.index_name}' 생성 완료")
        else:
            print(f"인덱스 '{self.index_name}'가 이미 존재합니다.")

    def sync_mongo_to_es(self, brand_name: str) -> dict:
        print(f"[{brand_name.lower()}] MongoDB -> ElasticSearch 동기화 시작...")
        self._create_index_if_not_exists()

        def generate_es_docs():
            cursor = self.mongo_manager.col.find({"basic_info.brand": brand_name.lower()})
            for doc in cursor:
                has_text_vec = "text_vector" in doc
                has_image_vec = "image_vector" in doc

                if not has_image_vec and not has_text_vec:
                    continue

                basic_info = doc.get("basic_info", {})
                analysis = doc.get("analysis", {})

                es_doc = {
                    "_index": self.index_name,
                    "_id": str(doc["_id"]),
                    "_source": {
                        "filename": doc.get("filename"),
                        "brand": basic_info.get("brand", brand_name.lower()),
                        "category": basic_info.get("original_category"),
                        "description": analysis.get("description", ""),
                        "image_url": doc.get("filename") 
                    }
                }
                if has_image_vec: es_doc["_source"]["vec_image"] = doc["image_vector"]
                if has_text_vec: es_doc["_source"]["vec_text"] = doc["text_vector"]

                yield es_doc

        try:
            success, failed = helpers.bulk(self.es, generate_es_docs())
            failed_count = len(failed) if isinstance(failed, list) else failed
            print(f"[{brand_name.lower()}] 동기화 완료: 성공 {success}건, 실패 {failed_count}건")
            return {"success": success, "failed": failed_count}
        except Exception as e:
            print(f"에러 발생: {e}")
            return {"success": 0, "failed": -1, "error": str(e)}


