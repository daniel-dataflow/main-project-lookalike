
import logging
import time
import threading
from datetime import datetime, timezone, timedelta
from collections import defaultdict, deque
from typing import Optional
import docker
from docker.errors import DockerException

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


class AutoRecovery:
    """
    치명적 에러 발생 시 자동 복구 서비스

    기능:
    1. 서비스별 에러 카운트 추적 (슬라이딩 윈도우)
    2. 임계치 초과 시 컨테이너 자동 재시작
    3. 재시작 횟수 제한 (무한루프 방지)
    4. 복구 이력 기록
    5. Slack 알림 연동
    """

    def __init__(self):
        self.enabled = False  # 기본 비활성화 (안전)

        # Docker 클라이언트
        try:
            self.docker_client = docker.from_env()
        except DockerException as e:
            logger.error(f"Docker 클라이언트 초기화 실패: {e}")
            self.docker_client = None

        # ─── 설정 ───
        # 에러 카운트 윈도우 (초)
        self.error_window_sec = 300          # 5분 윈도우
        # 자동 재시작 트리거 임계치
        self.restart_threshold = 10          # 5분간 10건 이상
        # 최대 재시작 횟수 (시간당)
        self.max_restarts_per_hour = 3
        # 재시작 쿨다운 (초)
        self.restart_cooldown = 120          # 2분

        # ─── 자동 재시작 대상 컨테이너 (안전한 서비스만) ───
        self.restartable_containers = {
            "api": "fastapi-main",
            # 필요 시 추가:
            # "airflow": "airflow-webserver-main",
        }

        # ─── 내부 상태 ───
        self._error_counts = defaultdict(deque)   # {service: [(timestamp), ...]}
        self._restart_history = deque(maxlen=100)  # 전체 재시작 이력
        self._restart_count_per_hour = defaultdict(deque)  # {service: [timestamps]}
        self._last_restart_time = {}               # {service: timestamp}

        self._lock = threading.Lock()

    # ─── 설정 관리 ───

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        logger.info(f"자동 복구 {'활성화' if enabled else '비활성화'}")

    def update_settings(self, settings: dict):
        if "restart_threshold" in settings:
            self.restart_threshold = max(3, int(settings["restart_threshold"]))
        if "error_window_sec" in settings:
            self.error_window_sec = max(60, int(settings["error_window_sec"]))
        if "max_restarts_per_hour" in settings:
            self.max_restarts_per_hour = max(1, int(settings["max_restarts_per_hour"]))
        if "restart_cooldown" in settings:
            self.restart_cooldown = max(30, int(settings["restart_cooldown"]))

    def get_config(self) -> dict:
        return {
            "enabled": self.enabled,
            "restart_threshold": self.restart_threshold,
            "error_window_sec": self.error_window_sec,
            "max_restarts_per_hour": self.max_restarts_per_hour,
            "restart_cooldown": self.restart_cooldown,
            "restartable_services": list(self.restartable_containers.keys()),
        }

    def get_status(self) -> dict:
        now = time.time()
        service_errors = {}
        for svc, timestamps in self._error_counts.items():
            active = [t for t in timestamps if now - t < self.error_window_sec]
            service_errors[svc] = len(active)

        return {
            "enabled": self.enabled,
            "service_error_counts": service_errors,
            "restart_history": list(self._restart_history)[-10:],
            "total_restarts": len(self._restart_history),
        }

    # ─── 에러 추적 ───

    def track_error(self, log_entry: dict):
        """에러 로그를 추적하고 임계치 도달 시 복구 실행"""
        if not self.enabled or not self.docker_client:
            return

        service = log_entry.get('service', 'unknown')
        if service not in self.restartable_containers:
            return

        now = time.time()

        with self._lock:
            self._error_counts[service].append(now)

            # 윈도우 밖 항목 제거
            while (self._error_counts[service] and
                   now - self._error_counts[service][0] > self.error_window_sec):
                self._error_counts[service].popleft()

            error_count = len(self._error_counts[service])

            if error_count >= self.restart_threshold:
                self._try_restart(service, error_count)

    def _try_restart(self, service: str, error_count: int):
        """재시작 시도 (안전 제한 확인 후)"""
        now = time.time()
        container_name = self.restartable_containers.get(service)
        if not container_name:
            return

        # 쿨다운 체크
        last_restart = self._last_restart_time.get(service, 0)
        if now - last_restart < self.restart_cooldown:
            logger.info(f"[AutoRecovery] {service} 재시작 쿨다운 중 ({int(self.restart_cooldown - (now - last_restart))}초 남음)")
            return

        # 시간당 재시작 횟수 체크
        hour_ago = now - 3600
        self._restart_count_per_hour[service] = deque(
            t for t in self._restart_count_per_hour[service] if t > hour_ago
        )
        if len(self._restart_count_per_hour[service]) >= self.max_restarts_per_hour:
            logger.warning(f"[AutoRecovery] {service} 시간당 재시작 제한 도달 ({self.max_restarts_per_hour}회)")
            # 제한 도달도 알림
            self._record_restart(service, container_name, "restart_limit",
                                 f"시간당 최대 재시작 횟수({self.max_restarts_per_hour}) 도달", False)
            return

        # 재시작 실행
        self._execute_restart(service, container_name, error_count)

    def _execute_restart(self, service: str, container_name: str, error_count: int):
        """실제 컨테이너 재시작"""
        now = time.time()
        reason = f"{self.error_window_sec // 60}분간 에러 {error_count}건 (임계치: {self.restart_threshold})"

        try:
            container = self.docker_client.containers.get(container_name)
            container.restart(timeout=30)

            self._last_restart_time[service] = now
            self._restart_count_per_hour[service].append(now)

            # 에러 카운트 초기화
            self._error_counts[service].clear()

            logger.warning(f"[AutoRecovery] {container_name} 재시작 완료 - {reason}")
            self._record_restart(service, container_name, "restart", reason, True)

            # Slack 알림
            self._notify_slack(container_name, service, "컨테이너 재시작", reason, True)

        except Exception as e:
            logger.error(f"[AutoRecovery] {container_name} 재시작 실패: {e}")
            self._record_restart(service, container_name, "restart", f"{reason} - 실패: {str(e)}", False)
            self._notify_slack(container_name, service, "컨테이너 재시작", f"{reason}\n실패: {str(e)}", False)

    def _record_restart(self, service: str, container: str, action: str, reason: str, success: bool):
        """재시작 이력 기록"""
        self._restart_history.append({
            "service": service,
            "container": container,
            "action": action,
            "reason": reason,
            "success": success,
            "time": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
        })

    def _notify_slack(self, container_name: str, service: str, action: str, reason: str, success: bool):
        """Slack 알림 전송"""
        try:
            from .slack_notifier import get_slack_notifier
            notifier = get_slack_notifier()
            notifier.notify_auto_recovery(container_name, service, action, reason, success)
        except Exception as e:
            logger.error(f"Slack 알림 전송 실패: {e}")


# ─── 싱글턴 ───
_recovery_instance: Optional[AutoRecovery] = None


def get_auto_recovery() -> AutoRecovery:
    global _recovery_instance
    if _recovery_instance is None:
        _recovery_instance = AutoRecovery()
    return _recovery_instance
