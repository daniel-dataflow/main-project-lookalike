"""
Kafka 토픽에서 컨테이너 시스템 메트릭을 구독(Consume)하여 Elasticsearch로 적재하는 수신 모듈.
- MetricCollector가 생성한 방대한 데이터를 비동기적으로(배치 버퍼링) 처리해 엘라스틱 네트워크 오버헤드를 줄이기 위한 의도.
"""
import json
import logging
import threading
import time
from kafka import KafkaConsumer
from kafka.errors import KafkaError, NoBrokersAvailable
from elasticsearch import helpers
from ..core.elasticsearch_setup import get_es_client
from ..config.logging import METRIC_INDEX_NAME

logger = logging.getLogger(__name__)

class KafkaMetricConsumer:
    """
    Consumer Group 방식을 채택하여 여러 백엔드 인스턴스 중 한 곳에서만 메트릭 처리 및 색인 파이프라인이 동작하도록 관리하는 클래스.
    """
    def __init__(self):
        self.bootstrap_servers = ["kafka:9092"]
        self.topic = "system-metrics"
        self.group_id = "metric-monitor-group"
        self.es_client = get_es_client()
        self.index_name = METRIC_INDEX_NAME
        self.consumer = None
        self.running = False

    def consume_and_index(self):
        """
        Kafka Topic 구독을 유지하며 도달한 메트릭들을 내부 버퍼 구조에 쌓고, 버퍼 한도를 초과하거나 일정 시간이 지나면 ES에 Bulk Insert 를 수행하는 루프.
        무한 루프 상 예외가 발생하더라도 즉시 종료되지 않고 Kafka 재연결을 시도해 안정성을 추구함.
        """
        logger.info(f"Starting Kafka Metric consumer thread for topic: {self.topic}")
        buffer = []
        last_flush_time = time.time()
        flush_interval = 2.0
        batch_size = 10

        while self.running:
            if not self.consumer:
                try:
                    self.consumer = KafkaConsumer(
                        self.topic,
                        bootstrap_servers=self.bootstrap_servers,
                        group_id=self.group_id,
                        auto_offset_reset="latest",
                        enable_auto_commit=True,
                        value_deserializer=lambda x: json.loads(x.decode('utf-8')),
                        request_timeout_ms=20000,
                        api_version_auto_timeout_ms=20000
                    )
                    self.consumer.topics()
                    logger.info("✅ Kafka Metric connection successful. Starting consumption.")
                except (KafkaError, NoBrokersAvailable) as e:
                    logger.warning(f"❌ Kafka Metric connection failed ({e}). Retrying in 5 seconds...")
                    time.sleep(5)
                    continue

            try:
                for message in self.consumer:
                    if not self.running:
                        break
                    
                    try:
                        metric_data = message.value
                        
                        if metric_data:
                            buffer.append({
                                "_index": self.index_name,
                                "_source": metric_data
                            })

                        # Flush buffer
                        current_time = time.time()
                        if len(buffer) >= batch_size or (current_time - last_flush_time) >= flush_interval:
                            if buffer:
                                helpers.bulk(self.es_client, buffer)
                                buffer = []
                            last_flush_time = current_time
                            
                    except Exception as e:
                        logger.error(f"Error processing metric message: {e}")
                        continue

            except Exception as e:
                logger.error(f"Kafka Metric consumer error: {e}")
                self.consumer = None
                time.sleep(5)

    def start(self):
        """
        메트릭 수신 작업을 백그라운드 Daemon Thread로 분리 시작함.
        FastAPI 메인 스레드의 응답성 및 블로킹 방지를 위함.
        """
        self.running = True
        
        thread = threading.Thread(target=self.consume_and_index, daemon=True)
        thread.start()

    def stop(self):
        """
        애플리케이션 종료 시 안전하게 쓰레드 루프를 탈출하고 Kafka Consumer 리소스를 반납하도록 지시.
        """
        self.running = False
        if self.consumer:
            self.consumer.close()
