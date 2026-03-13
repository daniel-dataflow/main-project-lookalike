"""
도커 컨테이너 로그 수집 및 Elasticsearch 영구 저장을 담당하는 서비스 모듈.
- 백그라운드 태스크로 동작하며, 지정된 모니터링 대상 컨테이너들의 로그를 주기적으로 수거.
- 파싱된 로그 라인을 기반으로 슬랙 알림(에러 감지) 및 자동 복구 작업을 연계 수행함.
- 수동 Purge(초기화) 명령이 수행된 지점(_GLOBAL_PURGE_TIMESTAMP) 이전의 로그는 무시하여 오래된 상태가 부활하는 문제를 차단.
"""
import asyncio
import logging
import hashlib
import re
from datetime import datetime
import docker
from docker.errors import DockerException
from elasticsearch import Elasticsearch, helpers
import json

from ..core.elasticsearch_setup import get_es_client
from .slack_notifier import get_slack_notifier
from .auto_recovery import get_auto_recovery
from ..config.logging import SERVICE_MAP, LOG_INDEX_NAME

logger = logging.getLogger(__name__)

# 전역 상태: 마지막으로 Purge 기능이 호출된 시점 (UTC ISO 포맷 문자열)
# 이 시점 이전의 도커 과거 로그들은 ES에 재수집되지 않고 즉각 버려집니다. (좀비 로그 부활 영구 차단)
_GLOBAL_PURGE_TIMESTAMP = None

def set_global_purge_time(iso_timestamp_str: str):
    """
    운영 화면 등의 요청으로 인해 초기화(Purge)를 켰을 때 발생하는 좀비 로그 부활 현상을 방지하고자 전역 시간 마커를 세팅함.

    Args:
        iso_timestamp_str (str): 리셋 기준으로 삼을 UTC ISO8601 포맷 문자열.
    """
    global _GLOBAL_PURGE_TIMESTAMP
    _GLOBAL_PURGE_TIMESTAMP = iso_timestamp_str

class LogCollector:
    """
    컨테이너 로그 수거 및 인덱싱 처리, 부가 작업(알림, 자동복구) 연동을 총괄하는 비즈니스 클래스.
    """
    def __init__(self):
        try:
            self.docker_client = docker.from_env()
        except DockerException as e:
            logger.error(f"Docker 클라이언트 초기화 실패: {e}")
            self.docker_client = None
            
        self.es_client = get_es_client()
        self.index_name = LOG_INDEX_NAME
        
        # 컨테이너 이름에 따른 서비스 매핑
        self.service_map = SERVICE_MAP
        
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
        # (Fix) HTTP 2xx, 3xx 정상 통신 로그 예외 처리 (URL에 "ERROR"나 "CRITICAL"이 들어있더라도 INFO 처리)
        if re.search(r'HTTP/1\.[01]" [23]\d{2}', message):
            return "INFO"

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
        도커에서 추출한 로우(Raw) 텍스트를 파싱하여 메시지 내용과 심각도(레벨)를 분리함.
        MongoDB 방식 등 알려진 JSON 구조일 경우 특정 필드 포맷을 파싱해 구조화함.

        Args:
            message (str): 도커 컨테이너에서 찍힌 한 줄 텍스트.

        Returns:
            tuple[str, str]: 추출된 로깅 레벨("INFO", "ERROR" 등)과 본문 메시지 튜플.
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
        """
        특정 단일 컨테이너에 대해 최근 찍힌 로그(tail 수량)를 검색하여 정제된 포맷의 배열로 반환함.

        Args:
            container_name (str): 도커 서비스 컨테이너 이름.
            tail (int, optional): 수집할 최대 라인 수. 기본값 200.

        Returns:
            list: 정제된 로그 딕셔너리(`timestamp`, `level`, `message` 등 포함) 배열. 파싱 혹은 도커 클라이언트 오류 시 빈 리스트 반환.
        """
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
                
                # [중요] 사용자가 대시보드에서 '초기화(Purge)'를 누른 시점보다
                # 이전에 발생한 과거 찌꺼기 로그가 재수집되어 좀비처럼 살아나는 현상을 완벽 원천 차단
                if _GLOBAL_PURGE_TIMESTAMP and timestamp < _GLOBAL_PURGE_TIMESTAMP:
                    continue
                
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
        """
        다수의 로그 기록을 Elasticsearch에 일괄(Bulk) 인덱싱함.
        네트워크 I/O 최적화를 위해 수행. 동일 시간에 도달한 중복 메시지를 방지하기 위해 컨테이너+시간+본문해시 기반의 고유 Doc ID를 생성함.

        Args:
            logs (list): 인덱싱 대상 로그 객체 목록.
        """
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

    def run_collection_cycle(self):
        """
        전체 모니터링 대상 컨테이너에 대해 단일 로그 수집 주기(Loop)를 실행함.
        수집, ES 적재, Slack 알림 체크, Error 자동복구 체크 절차를 순서대로 진행함.
        """
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
        """
        애플리케이션 생명주기와 함께 동작하는 백그라운드 로그 수집 코루틴.
        주기마다(10초) 쓰레드로 수거 작업을 보내 블락킹 현상을 막음.
        """
        logger.info("백그라운드 로그 수집 서비스 시작...")
        while True:
            try:
                await asyncio.to_thread(self.run_collection_cycle)
            except Exception as e:
                logger.error(f"로그 수집 주기 중 오류: {e}")
            
            # 다음 주기 전 10초 대기
            await asyncio.sleep(10)
