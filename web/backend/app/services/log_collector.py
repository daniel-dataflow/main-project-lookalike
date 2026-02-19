
import asyncio
import logging
import hashlib
from datetime import datetime
import docker
from docker.errors import DockerException
from elasticsearch import Elasticsearch, helpers
from ..core.elasticsearch_setup import get_es_client
from .slack_notifier import get_slack_notifier
from .auto_recovery import get_auto_recovery
import json

logger = logging.getLogger(__name__)

class LogCollector:
    def __init__(self):
        try:
            self.docker_client = docker.from_env()
        except DockerException as e:
            logger.error(f"Docker 클라이언트 초기화 실패: {e}")
            self.docker_client = None
            
        self.es_client = get_es_client()
        self.index_name = "container-logs"
        
        # 컨테이너 이름에 따른 서비스 매핑
        self.service_map = {
            "airflow": ["airflow-webserver-main", "airflow-scheduler-main"],
            "spark": ["spark-master-main", "spark-worker-1-main"],
            "hadoop": ["namenode-main", "datanode-main"],
            "kafka": ["kafka-main", "zookeeper-main"],
            "database": ["postgres-main", "mongo-main", "redis-main"],
            "search": ["elasticsearch-main"],
            "api": ["fastapi-main"]
        }
        
        # Slack 알림 서비스
        self.slack_notifier = get_slack_notifier()
        # 자동 복구 서비스
        self.auto_recovery = get_auto_recovery()
        
        # 빠른 검색을 위한 역매핑 (컨테이너 -> 서비스)
        self.container_to_service = {}
        for service, containers in self.service_map.items():
            for container in containers:
                self.container_to_service[container] = service

        # 모니터링 대상 컨테이너 목록
        self.monitored_containers = list(self.container_to_service.keys())



    def _determine_level(self, message: str) -> str:
        """메시지 내용을 기반으로 로그 레벨 결정"""
        lower_msg = message.lower()
        if "error" in lower_msg or "exception" in lower_msg:
            return "ERROR"
        elif "warn" in lower_msg:
            return "WARN"
        elif "critical" in lower_msg or "fatal" in lower_msg:
            return "CRITICAL"
        return "INFO"

    def _parse_log_line(self, message: str) -> tuple[str, str]:
        """
        로그 라인 파싱 및 레벨 결정
        Returns: (level, parsed_message)
        """
        try:
            # JSON 파싱 시도
            log_json = json.loads(message)

            # 1. MongoDB JSON 로그 처리 ("t", "s", "c", "msg" 필드 존재 가정)
            # 예: {"t":..., "s":"I", "c":"...", "msg":"..."}
            if isinstance(log_json, dict) and "s" in log_json and "msg" in log_json:
                msg_content = log_json["msg"]
                level_char = log_json.get("s", "I")

                if level_char == "F":
                    level = "CRITICAL"
                elif level_char == "E":
                    level = "ERROR"
                elif level_char == "W":
                    level = "WARN"
                elif level_char in ["I", "D"]:
                    level = "INFO"
                else:
                    level = "INFO"
                
                return level, msg_content

            # 2. 그 외 JSON -> 원본 전체 사용, 레벨은 내용 기반 판별
            return self._determine_level(message), message

        except (json.JSONDecodeError, TypeError):
            # JSON 아님 -> 기존 로직 사용
            return self._determine_level(message), message

    def collect_container_logs(self, container_name: str, tail: int = 200) -> list:
        """특정 컨테이너의 로그 수집"""
        if not self.docker_client:
            return []
            
        try:
            container = self.docker_client.containers.get(container_name)
            # 로그를 바이트로 가져와 디코딩 및 라인 분리
            logs_raw = container.logs(tail=tail, timestamps=True).decode('utf-8')
            
            parsed_logs = []
            for line in logs_raw.splitlines():
                if not line.strip():
                    continue
                    
                # Docker 로그 형식 (timestamps=True):
                # 2024-01-01T12:00:00.000000000Z log_message
                parts = line.split(' ', 1)
                if len(parts) == 2:
                    timestamp_str, message = parts
                    # 나노초 제거 (Elasticsearch는 ISO8601 처리 가능)
                    timestamp = timestamp_str.split('Z')[0][:23] + 'Z' if 'Z' in timestamp_str else timestamp_str
                else:
                    timestamp = datetime.utcnow().isoformat() + 'Z'
                    message = line

                service = self.container_to_service.get(container_name, "unknown")
                
                # 개선된 파싱 로직 적용
                level, parsed_message = self._parse_log_line(message)
                
                log_entry = {
                    "timestamp": timestamp,
                    "container": container_name,
                    "service": service,
                    "level": level,
                    "message": parsed_message,
                    "raw": line
                }
                parsed_logs.append(log_entry)
                
            return parsed_logs
            
        except Exception as e:
            logger.warning(f"{container_name} 로그 수집 중 오류: {e}")
            return []

    def bulk_index_to_elasticsearch(self, logs: list):
        """Elasticsearch에 로그 벌크 인덱싱"""
        if not logs:
            return
            
        actions = []
        for log in logs:
            # 중복 방지를 위한 고유 ID 생성
            # 컨테이너명, 타임스탬프, 메시지 해시 조합 사용
            msg_hash = hashlib.md5(log['message'].encode()).hexdigest()[:8]
            doc_id = f"{log['container']}_{log['timestamp']}_{msg_hash}"
            
            action = {
                "_index": self.index_name,
                "_id": doc_id,
                "_source": log
            }
            actions.append(action)
            
        try:
            helpers.bulk(self.es_client, actions)
            # logger.info(f"{len(logs)}개의 로그를 Elasticsearch에 인덱싱함")
        except Exception as e:
            logger.error(f"로그 벌크 인덱싱 실패: {e}")

    async def run_collection_cycle(self):
        """모든 모니터링 대상 컨테이너에 대해 수집 주기 실행"""
        all_logs = []
        for container_name in self.monitored_containers:
            logs = self.collect_container_logs(container_name)
            all_logs.extend(logs)
            
        if all_logs:
            self.bulk_index_to_elasticsearch(all_logs)
            # Slack 알림 체크 (CRITICAL 즉시 알림 + 에러 급증 감지)
            self.slack_notifier.check_and_alert(all_logs)
            # 자동 복구 체크 (ERROR/CRITICAL 반복 시 컨테이너 재시작)
            for log in all_logs:
                if log.get('level') in ('ERROR', 'CRITICAL'):
                    self.auto_recovery.track_error(log)

    async def start_background_collection(self):
        """로그 수집 백그라운드 태스크 시작"""
        logger.info("백그라운드 로그 수집 서비스 시작...")
        while True:
            try:
                await self.run_collection_cycle()
            except Exception as e:
                logger.error(f"로그 수집 주기 중 오류: {e}")
            
            # 다음 주기 전 10초 대기
            await asyncio.sleep(10)
