import json
import logging
import threading
import time
from kafka import KafkaConsumer
from kafka.errors import KafkaError, NoBrokersAvailable
from elasticsearch import helpers
from ..core.elasticsearch_setup import get_es_client

logger = logging.getLogger(__name__)

class KafkaMetricConsumer:
    def __init__(self):
        self.bootstrap_servers = ["kafka:9092"]
        self.topic = "system-metrics"
        self.group_id = "metric-monitor-group"
        self.es_client = get_es_client()
        self.index_name = "container-metrics"
        self.consumer = None
        self.running = False

    def consume_and_index(self):
        """
        Kafka consumer loop for metrics
        """
        logger.info(f"Starting Kafka Metric consumer for topic: {self.topic}")
        buffer = []
        last_flush_time = time.time()
        flush_interval = 2.0
        batch_size = 10

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

    def start(self):
        """
        Start consumer in a background thread
        """
        self.running = True
        
        try:
            # Try connecting to Kafka
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
            
            # Test connection
            self.consumer.topics()
            logger.info("✅ Kafka Metric connection successful. Starting consumer thread.")
            
            thread = threading.Thread(target=self.consume_and_index, daemon=True)
            thread.start()
            
        except (KafkaError, NoBrokersAvailable) as e:
            logger.warning(f"❌ Kafka Metric connection failed: {e}")
            # Metrics are less critical than logs, so maybe we don't need a robust fallback immediately,
            # or we could implement direct pushing to ES from MetricCollector if Kafka fails.
            # For now, we'll just log the error.
            pass

    def stop(self):
        self.running = False
        if self.consumer:
            self.consumer.close()
