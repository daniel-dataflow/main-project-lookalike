"""
컨테이너 리소스 메트릭(CPU, Memory)을 수집하여 Kafka로 발행하는 서비스 모듈.
- 실시간 리소스 모니터링 파이프라인(ELK/Grafana)의 데이터 생성기로서 기능함.
- Docker 데몬 및 Kafka 브로커 오류 대응을 내부적으로 처리.
"""
import json
import logging
import asyncio
import docker
from datetime import datetime
from kafka import KafkaProducer
from kafka.errors import KafkaError
from ..config.logging import SERVICE_MAP

logger = logging.getLogger(__name__)

class MetricCollector:
    """
    모니터링 대상 서비스의 도커 Stats API를 조회해 시스템 메트릭을 추출하고 스트리밍하는 비즈니스 클래스.
    """
    def __init__(self):
        try:
            self.docker_client = docker.from_env()
        except Exception as e:
            logger.error(f"Docker client initialization failed: {e}")
            self.docker_client = None

        self.producer = None
        self.topic = "system-metrics"
        self.bootstrap_servers = ["kafka:9092"]
        
        self.service_map = SERVICE_MAP
        self.container_to_service = {}
        for service, containers in self.service_map.items():
            for container in containers:
                self.container_to_service[container] = service

        self.monitored_containers = list(self.container_to_service.keys())

    def _get_cpu_percent(self, stats) -> float:
        """
        도커 컨테이너 stats 스냅샷으로부터 CPU 사용 비율을 0~100 사이의 백분율로 계산함.
        멀티코어 시스템일 경우 누적된 delta 값을 활용해야 하므로 이전 스냅샷과 이번 스냅샷 간의 차이를 이용해 산출.

        Args:
            stats (dict): Docker API로부터 반환받은 raw stats 딕셔너리.

        Returns:
            float: 백분율로 환산된 실질 CPU 사용량.
        """
        try:
            cpu_stats = stats.get("cpu_stats", {})
            precpu_stats = stats.get("precpu_stats", {})
            
            cpu_usage = cpu_stats.get("cpu_usage", {}).get("total_usage", 0)
            precpu_usage = precpu_stats.get("cpu_usage", {}).get("total_usage", 0)
            
            system_cpu_usage = cpu_stats.get("system_cpu_usage", 0)
            system_precpu_usage = precpu_stats.get("system_cpu_usage", 0)
            
            if system_cpu_usage > 0 and system_precpu_usage > 0:
                cpu_delta = cpu_usage - precpu_usage
                system_delta = system_cpu_usage - system_precpu_usage
                
                if system_delta > 0 and cpu_delta > 0:
                    return (cpu_delta / system_delta) * 100.0
        except Exception:
            pass
        return 0.0

    def _get_memory_percent(self, stats) -> float:
        """
        도커 stats 정보에서 현재 할당된 메모리 한도 대비 사용량 비율을 산출.

        Args:
            stats (dict): Docker API로부터 반환받은 raw stats 딕셔너리.

        Returns:
            float: 백분율 메모리 사용량.
        """
        try:
            memory_stats = stats.get("memory_stats", {})
            usage = memory_stats.get("usage", 0)
            limit = memory_stats.get("limit", 0)
            
            if limit > 0:
                return (usage / limit) * 100.0
        except Exception:
            pass
        return 0.0

    def _get_memory_usage(self, stats) -> int:
        return stats.get("memory_stats", {}).get("usage", 0)

    def _get_memory_limit(self, stats) -> int:
        return stats.get("memory_stats", {}).get("limit", 0)

    def collect_metrics(self):
        """
        모니터링 대상 컨테이너들에 대한 Stats 조회 폴링을 수행하고, 수집된 데이터를 Kafka Topic에 발행함.
        수집 주기는 백그라운드 태스크에서 통제하며, 메트릭 수집 불가 시 전체 로직이 중단되지 않도록 예외 처리.
        """
        if not self.docker_client:
            return

        for container_name in self.monitored_containers:
            try:
                container = self.docker_client.containers.get(container_name)
                stats = container.stats(stream=False)
                
                service = self.container_to_service.get(container_name, "unknown")
                cpu_pct = self._get_cpu_percent(stats)
                mem_pct = self._get_memory_percent(stats)
                mem_usage = self._get_memory_usage(stats)
                mem_limit = self._get_memory_limit(stats)
                
                metric_data = {
                    "timestamp": datetime.utcnow().isoformat(),
                    "container": container_name,
                    "service": service,
                    "cpu_percent": round(cpu_pct, 2),
                    "memory_percent": round(mem_pct, 2),
                    "memory_usage": mem_usage,
                    "memory_limit": mem_limit
                }
                
                self._publish_to_kafka(metric_data)
                
            except Exception as e:
                # logger.warning(f"Failed to collect metrics for {container_name}: {e}")
                pass

    def _publish_to_kafka(self, data):
        if not self.producer:
            try:
                self.producer = KafkaProducer(
                    bootstrap_servers=self.bootstrap_servers,
                    value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                    request_timeout_ms=2000
                )
            except Exception:
                return

        try:
            self.producer.send(self.topic, data)
        except Exception as e:
            logger.error(f"Failed to send metric to Kafka: {e}")
            self.producer = None # Reset producer to retry connection later

    async def start(self):
        """
        애플리케이션 생성 주기에 맞춰 동작하는 백그라운드 코루틴 루프.
        주기(15초)마다 메트릭 수집 작업을 쓰레드로 오프로드하여 비동기 실행 흐름을 차단하지 않게 설정.
        """
        logger.info("Starting Background Metric Collector...")
        while True:
            try:
                await asyncio.to_thread(self.collect_metrics)
            except Exception as e:
                logger.error(f"Metric collection error: {e}")
            
            await asyncio.sleep(15) # Collect every 15 seconds
