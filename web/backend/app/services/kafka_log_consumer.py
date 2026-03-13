"""
Kafka 토픽에서 Filebeat가 수집한 분산 컨테이너 로그를 구독 및 파싱하여 Elasticsearch에 저장하는 모듈.
- 대규모 로그 트래픽을 안정적으로 처리하기 위해 버퍼링 및 Bulk 인덱싱 방식을 사용함.
- 다양한 로그 포맷(Docker JSON-file 등)에서 정확히 어떤 컨테이너/서비스에서 발생한 로그인지 추적하는 복원 로직을 포함함.
"""
import json
import logging
import re
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
    """
    Kafka 스트림을 섭취해 Elasticsearch 문서 형태로 변환하는 비즈니스 클래스.
    컨테이너 ID 기반의 캐싱 기법을 이용해 매 로우(Row)마다 Docker API를 찔러 생기는 병목을 해소함.
    """
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
        주어진 컨테이너 ID(Hash)로부터 실제 사람이 읽을 수 있는 이름(예: 'fastapi-main')을 반환함.
        Docker API I/O 비용을 줄이기 위해 내부 딕셔너리에 캐싱(Cache)된 값을 우선 접근함.

        Args:
            container_id (str): 도커 데몬에서 생성한 해시 ID.

        Returns:
            str: 해석된 도커 컨테이너 이름. 찾기 실패 시 빈 문자열.
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
        여러 경로/형태로 유입될 수 있는 Filebeat JSON 객체를 단일화된 스키마로 가공.
        도커 컨테이너 이름 구조나 Log File Path 패턴에 따라 휴리스틱하게 매칭 점검하여 누락된 메타데이터를 보정함.

        Args:
            raw_message (dict): Filebeat가 전달한 raw JSON 데이터.

        Returns:
            dict: 레벨(INFO/ERROR), 컨테이너명, 메시지를 포함하여 정형화된 사전 객체.
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
                match = re.search(r'containers/([a-f0-9]{64})', log_path)
                if match:
                    container_id = match.group(1)
                    container_name = self.resolve_container_name(container_id)

            # Fallback: Search for "-main" in raw message string
            if not container_name:
                raw_str = json.dumps(raw_message)
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
        logger.info(f"Starting Kafka consumer thread for topic: {self.topic}")
        buffer = []
        last_flush_time = time.time()
        flush_interval = 10.0
        batch_size = 50

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
                    logger.info("✅ Kafka connection successful. Starting consumer thread.")
                except (KafkaError, NoBrokersAvailable) as e:
                    logger.warning(f"❌ Kafka connection failed: {e}. Retrying in 5 seconds...")
                    time.sleep(5)
                    continue

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
                self.consumer = None
                time.sleep(5)

    def start(self):
        """
        Start consumer in a background thread with fallback
        """
        self.running = True
        
        thread = threading.Thread(target=self.consume_and_index, daemon=True)
        thread.start()
        return True

    def stop(self):
        self.running = False
        if self.consumer:
            self.consumer.close()
