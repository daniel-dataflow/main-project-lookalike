"""
로그 수집 및 알림 설정 (Logging & Monitoring Configs)
"""

# Elasticsearch 인덱스 설정
LOG_INDEX_NAME = "container-logs"
METRIC_INDEX_NAME = "container-metrics"
PRODUCT_INDEX_NAME = "products"

# 캐시 설정
DASHBOARD_CACHE_TTL = 300  # 초

# 기동 노이즈 패턴 (서버 재시작 시 정상적으로 발생하는 에러)
STARTUP_NOISE_PATTERNS = [
    # Hadoop
    "incompatible clusterids",
    "initialization failed for block pool",
    "all specified directories have failed to load",
    "datanode registration failed",
    "warn datanode",
    "namenode is in safe mode",
    # Kafka
    "broker is not available",
    "nobrokersavailable",
    "error while fetching metadata",
    "leader not available",
    "notleaderforpartitionerror",
    # Airflow
    "scheduler is not running",
    "dag file processor manager",
    # 공통 - 컨테이너 기동 순서 차이
    "connection refused",
    "connection reset by peer",
    "failed to connect",
    "temporary failure in name resolution",
]

# 컨테이너 서비스 매핑
SERVICE_MAP = {
    "Airflow": ["airflow-webserver-main", "airflow-scheduler-main"],
    "Spark": ["spark-master-main", "spark-worker-1-main"],
    "Hadoop": ["namenode-main", "datanode-main"],
    "Kafka": ["kafka-main", "zookeeper-main"],
    "DB": ["postgres-main", "mongo-main", "redis-main"],
    "Elastic": ["elasticsearch-main"],
    "API_BE": ["fastapi-main"],
    "API_ML": ["ml-engine-main"]
}
