import logging
import requests
from ..config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

def optimize_elasticsearch_index():
    """
    Elasticsearch 인덱스 최적화 설정
    - refresh_interval: 30s (기본 1s -> 30s)
    """
    es_url = settings.ELASTICSEARCH_URL
    index_name = "container-logs"
    
    try:
        # 인덱스 설정 변경 API 호출
        url = f"{es_url}/{index_name}/_settings"
        payload = {
            "index": {
                "refresh_interval": "30s",
                "number_of_replicas": 0
            }
        }
        
        response = requests.put(url, json=payload)
        
        if response.status_code == 200:
            logger.info(f"✅ Elasticsearch optimization applied: {index_name} refresh_interval=30s")
        elif response.status_code == 404:
             logger.warning(f"⚠️ Index not found during optimization: {index_name}")
        else:
            logger.warning(f"⚠️ Failed to apply ES optimization: {response.text}")
            
    except Exception as e:
        logger.error(f"❌ ES optimization error: {e}")
