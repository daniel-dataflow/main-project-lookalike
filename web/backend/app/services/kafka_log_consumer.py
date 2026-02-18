import json
import logging
import threading
import time
from datetime import datetime
from kafka import KafkaConsumer
from kafka.errors import KafkaError, NoBrokersAvailable
from elasticsearch import helpers
from ..core.elasticsearch_setup import get_es_client
from .log_collector import LogCollector

logger = logging.getLogger(__name__)

class KafkaLogConsumer:
    def __init__(self):
        self.bootstrap_servers = ["kafka:9092"]
        self.topic = "container-logs"
        self.group_id = "log-monitor-group"
        self.es_client = get_es_client()
        self.index_name = "container-logs"
        self.consumer = None
        self.running = False
        self.log_collector = LogCollector() # Fallback
        self.container_id_cache = {}

    def resolve_container_name(self, container_id: str) -> str:
        """
        Resolve container name from ID using Docker API (cached)
        """
        if container_id in self.container_id_cache:
            return self.container_id_cache[container_id]

        if not self.log_collector.docker_client:
            return ""

        try:
            container = self.log_collector.docker_client.containers.get(container_id)
            name = container.name
            # Remove leading slash if present
            if name.startswith("/"):
                name = name[1:]
            
            logger.info(f"Resolved container ID {container_id[:12]} -> {name}")
            self.container_id_cache[container_id] = name
            return name
        except Exception:
            # logger.warning(f"Failed to resolve container ID: {container_id[:12]}")
            return ""
        self.container_id_cache = {}

    def parse_filebeat_message(self, raw_message: dict) -> dict:
        """
        Filebeat 메시지 파싱 - Container Name 추출 로직 강화
        """
        try:
            # Filebeat fields
            timestamp = raw_message.get("@timestamp")
            message = raw_message.get("message", "")
            
            # Container info parsing - Try multiple paths
            container_name = ""
            
            # Path 1: message["container"]["name"]
            if not container_name:
                container_name = raw_message.get("container", {}).get("name", "")
                
            # Path 2: message["docker"]["container"]["name"]
            if not container_name:
                container_name = raw_message.get("docker", {}).get("container", {}).get("name", "")

            # Path 3: message["fields"]["container"]
            if not container_name:
                container_name = raw_message.get("fields", {}).get("container", "")

            # Path 4: Fallback to log file path (extract container ID)
            if not container_name:
                log_path = raw_message.get("log", {}).get("file", {}).get("path", "")
                
                # regex for container ID in path: /var/lib/docker/containers/<id>/<id>-json.log
                import re
                match = re.search(r'containers/([a-f0-9]{64})', log_path)
                if match:
                    container_id = match.group(1)
                    container_name = self.resolve_container_name(container_id)

            # Fallback: Search for "-main" in raw message string
            if not container_name:
                raw_str = json.dumps(raw_message)
                import re
                match = re.search(r'[\w-]+-main', raw_str)
                if match:
                    container_name = match.group(0)
                else:
                    # Debug: Log raw message structure if parsing fails
                    # logger.warning(f"Failed to find container name. Keys: {list(raw_message.keys())}")
                    pass

            # Cleanup
            if container_name and container_name.startswith("/"):
                container_name = container_name[1:]
            
            # Use LogCollector's logic for service mapping
            service = self.log_collector.container_to_service.get(container_name, "unknown")
            
            # Determine level and parse JSON if applicable
            level, parsed_message = self.log_collector._parse_log_line(message)

            return {
                "timestamp": timestamp,
                "container": container_name,
                "service": service,
                "level": level,
                "message": parsed_message,
                "raw": json.dumps(raw_message)
            }
        except Exception as e:
            logger.error(f"Error parsing message: {e}")
            return None

    def consume_and_index(self):
        """
        Kafka consumer loop
        """
        logger.info(f"Starting Kafka consumer for topic: {self.topic}")
        buffer = []
        last_flush_time = time.time()
        flush_interval = 10.0
        batch_size = 50

        try:
            for message in self.consumer:
                if not self.running:
                    break
                
                try:
                    raw_value = message.value
                    parsed_log = self.parse_filebeat_message(raw_value)
                    
                    if parsed_log:
                        # Debug: Log success
                        # logger.info(f"Parsed log for container: {parsed_log.get('container')}") 
                        buffer.append({
                            "_index": self.index_name,
                            "_source": parsed_log
                        })

                    # Flush buffer
                    current_time = time.time()
                    if len(buffer) >= batch_size or (current_time - last_flush_time) >= flush_interval:
                        if buffer:
                            logger.info(f"Flushing buffer to ES: {len(buffer)} docs")
                            helpers.bulk(self.es_client, buffer)
                            buffer = []
                        last_flush_time = current_time
                        
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    continue

        except Exception as e:
            logger.error(f"Kafka consumer error: {e}")

    def start(self):
        """
        Start consumer in a background thread with fallback
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
            logger.info("✅ Kafka connection successful. Starting consumer thread.")
            
            thread = threading.Thread(target=self.consume_and_index, daemon=True)
            thread.start()
            return True
            
        except (KafkaError, NoBrokersAvailable) as e:
            logger.warning(f"❌ Kafka connection failed: {e}")
            return False

    def stop(self):
        self.running = False
        if self.consumer:
            self.consumer.close()
