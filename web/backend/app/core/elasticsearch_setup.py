import os
from elasticsearch import Elasticsearch
from elasticsearch.exceptions import RequestError

# Elasticsearch 연결 설정
ELASTICSEARCH_URL = os.getenv("ELASTICSEARCH_URL", "http://elasticsearch:9200")

def get_es_client():
    """Elasticsearch 클라이언트 생성"""
    return Elasticsearch(ELASTICSEARCH_URL)

def init_elasticsearch_index():
    """
    Elasticsearch 인덱스 및 매핑 초기화
    인덱스가 없으면 생성, 있으면 건너뜀
    """
    es = get_es_client()
    index_name = "container-logs"

    # 인덱스 설정 및 매핑
    index_body = {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0
        },
        "mappings": {
            "properties": {
                "timestamp": {"type": "date"},
                "container": {"type": "keyword"},
                "service": {"type": "keyword"},
                "level": {"type": "keyword"},
                "message": {
                    "type": "text",
                    "fields": {
                        "keyword": {"type": "keyword", "ignore_above": 256}
                    }
                },
                "raw": {"type": "text"}
            }
        }
    }
    try:
        if not es.indices.exists(index=index_name):
            es.indices.create(index=index_name, body=index_body)
            print(f"✅ Elasticsearch index '{index_name}' created.")
        else:
            print(f"ℹ️  Elasticsearch index '{index_name}' already exists.")
    except Exception as e:
        print(f"❌ Failed to initialize Elasticsearch index: {e}")

def init_metric_index():
    es = get_es_client()
    index_name = "container-metrics"
    index_body = {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0
        },
        "mappings": {
            "properties": {
                "timestamp": {"type": "date"},
                "container": {"type": "keyword"},
                "service": {"type": "keyword"},
                "cpu_percent": {"type": "float"},
                "memory_percent": {"type": "float"},
                "memory_usage": {"type": "long"}
            }
        }
    }
    try:
        if not es.indices.exists(index=index_name):
            es.indices.create(index=index_name, body=index_body)
            print(f"✅ Elasticsearch index '{index_name}' created.")
        else:
            print(f"ℹ️  Elasticsearch index '{index_name}' already exists.")
    except Exception as e:
        print(f"❌ Failed to initialize Metric index: {e}")

def setup_index_lifecycle():
    """
    인덱스 수명 주기 정책 (ILM) 설정
    - Hot: 5GB 또는 1일마다 롤오버
    - Delete: 7일 후 삭제
    """
    es = get_es_client()
    policy_name = "container-logs-policy"
    
    ilm_policy = {
        "policy": {
            "phases": {
                "hot": {
                    "actions": {
                        "rollover": {
                            "max_size": "5GB",
                            "max_age": "1d"
                        }
                    }
                },
                "delete": {
                    "min_age": "7d",
                    "actions": {
                        "delete": {}
                    }
                }
            }
        }
    }
    
    try:
        # 1. ILM 정책 생성/업데이트
        es.ilm.put_lifecycle(name=policy_name, body=ilm_policy)
        print(f"✅ Elasticsearch ILM policy '{policy_name}' applied.")
        
        # 2. 인덱스 템플릿에 정책 적용 (이미 생성된 인덱스에는 별도 적용 필요할 수 있음)
        # 여기서는 단순화를 위해 템플릿 설정 생략하고, 
        # 기존 인덱스에 수동으로 settings update를 시도
        es.indices.put_settings(
            index="container-logs",
            body={
                "index": {
                    "lifecycle": {
                        "name": policy_name
                    }
                }
            }
        )
        print(f"✅ Attached ILM policy to 'container-logs'")
        
    except Exception as e:
        print(f"⚠️ Failed to setup Index Lifecycle Policy: {e}")
