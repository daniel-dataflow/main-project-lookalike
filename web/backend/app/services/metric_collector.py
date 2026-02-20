import json
import logging
import asyncio
import docker
from datetime import datetime
from kafka import KafkaProducer
from kafka.errors import KafkaError

logger = logging.getLogger(__name__)

class MetricCollector:
    def __init__(self):
        try:
            self.docker_client = docker.from_env()
        except Exception as e:
            logger.error(f"Docker client initialization failed: {e}")
            self.docker_client = None

        self.producer = None
        self.topic = "system-metrics"
        self.bootstrap_servers = ["kafka:9092"]
        
        # Service mapping (same as LogCollector)
        self.service_map = {
            "airflow": ["airflow-webserver-main", "airflow-scheduler-main"],
            "spark": ["spark-master-main", "spark-worker-1-main"],
            "hadoop": ["namenode-main", "datanode-main"],
            "kafka": ["kafka-main", "zookeeper-main"],
            "database": ["postgres-main", "mongo-main", "redis-main"],
            "search": ["elasticsearch-main"],
            "api": ["fastapi-main"]
        }
        self.container_to_service = {}
        for service, containers in self.service_map.items():
            for container in containers:
                self.container_to_service[container] = service

        self.monitored_containers = list(self.container_to_service.keys())

    def _get_cpu_percent(self, stats) -> float:
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
                    # number of cpus (online_cpus might be available)
                    online_cpus = cpu_stats.get("online_cpus", 1)
                    return (cpu_delta / system_delta) * online_cpus * 100.0
        except Exception:
            pass
        return 0.0

    def _get_memory_percent(self, stats) -> float:
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

    def collect_metrics(self):
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
                
                metric_data = {
                    "timestamp": datetime.utcnow().isoformat(),
                    "container": container_name,
                    "service": service,
                    "cpu_percent": round(cpu_pct, 2),
                    "memory_percent": round(mem_pct, 2),
                    "memory_usage": mem_usage
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
        logger.info("Starting Background Metric Collector...")
        while True:
            try:
                await asyncio.to_thread(self.collect_metrics)
            except Exception as e:
                logger.error(f"Metric collection error: {e}")
            
            await asyncio.sleep(15) # Collect every 15 seconds
