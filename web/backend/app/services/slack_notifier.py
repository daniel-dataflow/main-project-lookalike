"""
인프라 및 애플리케이션 상태를 관리자 워크스페이스(Slack)로 전파하는 알림 모듈.
- 비즈니스 연속성을 해치는 에러 발생 시 신속한 대응을 위해 외부 메시징 채널과 연동함.
"""
import logging
import time
import json
import os
from datetime import datetime, timezone, timedelta
from collections import deque
from typing import Optional
import urllib.request
import urllib.error
from ..config.logging import STARTUP_NOISE_PATTERNS

logger = logging.getLogger(__name__)

# 한국 시간대 (UTC+9)
KST = timezone(timedelta(hours=9))


def _parse_int_env(key: str) -> Optional[int]:
    """환경변수를 int로 파싱. 비어 있으면 None 반환."""
    v = os.environ.get(key, "").strip()
    return int(v) if v else None


# _STARTUP_NOISE_PATTERNS is now managed via config.logging.STARTUP_NOISE_PATTERNS



class SlackNotifier:
    """
    모니터링 중 발생하는 각종 장애 및 임계점 초과 상황을 관리자 워크스페이스(Slack)로 전파하는 알림 서비스 클래스.
    무분별한 알람으로 인한 피로도를 줄이기 위해, 노이즈 필터링/쿨다운/서킷 브레이커 등의 제어 로직을 내장함.
    """

    def __init__(self, webhook_url: Optional[str] = None):
        # ─── Webhook URL ───
        self.webhook_url = webhook_url or os.environ.get("SLACK_WEBHOOK_URL", "")
        self.enabled = bool(self.webhook_url)

        # ─── .env에서 알림 필터 설정 읽기 ───
        self.min_alert_level = os.environ.get("SLACK_MIN_ALERT_LEVEL", "CRITICAL")
        self.active_hours_start: Optional[int] = _parse_int_env("SLACK_ACTIVE_HOURS_START")
        self.active_hours_end: Optional[int] = _parse_int_env("SLACK_ACTIVE_HOURS_END")
        self.excluded_services: list[str] = []

        # ─── .env에서 쿨다운 설정 읽기 ───
        self.critical_cooldown = 60         # 같은 메시지 쿨다운 (초)
        self.error_spike_cooldown = 300     # 급증 알림 쿨다운 (초)

        # ─── .env에서 에러 급증 감지 설정 읽기 ───
        self.spike_window_sec = int(os.environ.get("SLACK_SPIKE_WINDOW_SEC", 600))
        self.spike_threshold = int(os.environ.get("SLACK_SPIKE_THRESHOLD", 15))

        # ─── [NEW] 기동 유예 기간 ───
        # 서버 재시작 직후 N초 동안은 모든 알림 억제 (Hadoop/Kafka 초기화 노이즈 방지)
        self._start_time = time.time()
        self._startup_grace_sec = int(os.environ.get("SLACK_STARTUP_GRACE_SEC", 300))

        # ─── [NEW] 기동 노이즈 패턴 필터 ───
        self._startup_noise_patterns = list(STARTUP_NOISE_PATTERNS)

        # ─── [NEW] Global Rate Limiter ───
        # 지정 시간 창 내에서 최대 N건만 전송
        self._rate_window_sec = int(os.environ.get("SLACK_RATE_WINDOW_SEC", 300))
        self._max_alerts_per_window = int(os.environ.get("SLACK_MAX_ALERTS_PER_WINDOW", 10))
        self._sent_timestamps: deque = deque()

        # ─── [NEW] Circuit Breaker ───
        # 상태: "closed" | "open" | "half_open"
        self._circuit_state = "closed"
        self._circuit_opened_at = 0.0
        self._circuit_open_sec = int(os.environ.get("SLACK_CIRCUIT_OPEN_SEC", 1800))
        self._circuit_backoff_multiplier = 1  # OPEN 반복 시 2배씩 증가

        # ─── 내부 상태 ───
        self._last_critical_alerts = {}     # {message_hash: timestamp}
        self._last_spike_alert = 0
        self._error_window = deque()
        self._alert_history = deque(maxlen=50)  # 최근 알림 이력

        # 레벨 우선순위 (높을수록 심각)
        self._level_priority = {
            "CRITICAL": 4, "ERROR": 3, "WARN": 2, "INFO": 1
        }

        # 서버 접속 URL (슬랙 링크에 사용)
        _env = os.environ.get("APP_ENV", "development").lower()
        if _env == "production":
            self.app_base_url = os.environ.get(
                "APP_BASE_URL_PROD", "http://localhost:8900"
            ).rstrip("/")
        else:
            self.app_base_url = os.environ.get(
                "APP_BASE_URL_LOCAL", "http://localhost:8900"
            ).rstrip("/")

        if self.enabled:
            logger.info(
                f"Slack 알림 서비스 활성화됨 (env={_env}, base_url={self.app_base_url}, "
                f"grace={self._startup_grace_sec}s, max_per_window={self._max_alerts_per_window})"
            )
        else:
            logger.info("Slack 알림 비활성화 (SLACK_WEBHOOK_URL 미설정)")

    # ─── 설정 관리 ───

    def set_webhook_url(self, url: str):
        self.webhook_url = url
        self.enabled = bool(url)
        logger.info(f"Slack webhook URL {'설정됨' if self.enabled else '해제됨'}")

    def set_enabled(self, enabled: bool):
        """
        슬랙 알림 전송 기능의 전체 활성화 여부를 세팅함.

        Args:
            enabled (bool): 전환할 활성화 상태.
        """
        self.enabled = enabled and bool(self.webhook_url)
        logger.info(f"Slack 알림 {'활성화' if self.enabled else '비활성화'}")

    def update_settings(self, settings: dict):
        """설정 일괄 업데이트"""
        if "min_alert_level" in settings:
            level = settings["min_alert_level"]
            if level in self._level_priority:
                self.min_alert_level = level

        if "active_hours_start" in settings:
            v = settings["active_hours_start"]
            self.active_hours_start = int(v) if v is not None else None

        if "active_hours_end" in settings:
            v = settings["active_hours_end"]
            self.active_hours_end = int(v) if v is not None else None

        if "excluded_services" in settings:
            self.excluded_services = list(settings["excluded_services"])

        if "spike_threshold" in settings:
            self.spike_threshold = max(1, int(settings["spike_threshold"]))

        if "spike_window_sec" in settings:
            self.spike_window_sec = max(60, int(settings["spike_window_sec"]))

        if "critical_cooldown" in settings:
            self.critical_cooldown = max(10, int(settings["critical_cooldown"]))

        if "error_spike_cooldown" in settings:
            self.error_spike_cooldown = max(60, int(settings["error_spike_cooldown"]))

        if "startup_grace_sec" in settings:
            self._startup_grace_sec = max(0, int(settings["startup_grace_sec"]))

        if "max_alerts_per_window" in settings:
            self._max_alerts_per_window = max(1, int(settings["max_alerts_per_window"]))

        if "rate_window_sec" in settings:
            self._rate_window_sec = max(60, int(settings["rate_window_sec"]))

        if "circuit_open_sec" in settings:
            self._circuit_open_sec = max(60, int(settings["circuit_open_sec"]))

        if "noise_patterns_add" in settings:
            for p in settings["noise_patterns_add"]:
                if p.lower() not in [x.lower() for x in self._startup_noise_patterns]:
                    self._startup_noise_patterns.append(p)

        logger.info(
            f"Slack 알림 설정 업데이트: level={self.min_alert_level}, "
            f"hours={self.active_hours_start}-{self.active_hours_end}, "
            f"excluded={self.excluded_services}"
        )

    def get_config(self) -> dict:
        """
        현재 적용 중인 슬랙 서비스의 알림 조건값 및 필터 인자들을 반환함.
        """
        return {
            "enabled": self.enabled,
            "webhook_url_set": bool(self.webhook_url),
            "webhook_url_preview": (
                self.webhook_url[:30] + "..."
                if len(self.webhook_url) > 30
                else self.webhook_url
            ) if self.webhook_url else "",
            "min_alert_level": self.min_alert_level,
            "active_hours_start": self.active_hours_start,
            "active_hours_end": self.active_hours_end,
            "excluded_services": self.excluded_services,
            "critical_cooldown_sec": self.critical_cooldown,
            "spike_window_sec": self.spike_window_sec,
            "spike_threshold": self.spike_threshold,
            "spike_cooldown_sec": self.error_spike_cooldown,
            "startup_grace_sec": self._startup_grace_sec,
            "max_alerts_per_window": self._max_alerts_per_window,
            "rate_window_sec": self._rate_window_sec,
            "circuit_open_sec": self._circuit_open_sec,
        }

    def get_status(self) -> dict:
        """
        현재 시점의 알림 제한 동작(서킷 브레이커, 토큰 버킷 등) 상태를 모니터링하기 위해 반환함.
        """
        now = time.time()
        grace_remaining = max(0.0, self._startup_grace_sec - (now - self._start_time))

        # 속도 제한 창 내 전송 건수 계산
        while self._sent_timestamps and now - self._sent_timestamps[0] > self._rate_window_sec:
            self._sent_timestamps.popleft()

        return {
            "enabled": self.enabled,
            "is_in_active_hours": self._is_in_active_hours(),
            # 기동 유예
            "startup_grace_remaining_sec": round(grace_remaining),
            "in_startup_grace": grace_remaining > 0,
            # 에러 급증
            "error_window_size": len(self._error_window),
            "spike_threshold": self.spike_threshold,
            "last_spike_alert_ago_sec": round(now - self._last_spike_alert) if self._last_spike_alert > 0 else None,
            # 쿨다운
            "active_cooldowns": len(self._last_critical_alerts),
            # Circuit Breaker
            "circuit_state": self._circuit_state,
            "circuit_backoff_multiplier": self._circuit_backoff_multiplier,
            # Rate Limiter
            "rate_window_sent_count": len(self._sent_timestamps),
            "rate_limit_max": self._max_alerts_per_window,
            # 이력
            "recent_alerts": list(self._alert_history)[-10:],
        }

    # ─── 기동 유예 기간 ───

    def _is_in_startup_grace(self) -> bool:
        """서비스 시작 후 grace 기간 내인지 확인"""
        return (time.time() - self._start_time) < self._startup_grace_sec

    # ─── 기동 노이즈 패턴 필터 ───

    def _is_startup_noise(self, message: str) -> bool:
        """알려진 기동 노이즈 패턴인지 확인 (대소문자 무시)"""
        msg_lower = message.lower()
        return any(p in msg_lower for p in self._startup_noise_patterns)

    # ─── 필터 체크 ───

    def _is_in_active_hours(self) -> bool:
        """현재 시각이 활성 시간대인지 확인 (KST 기준)"""
        if self.active_hours_start is None or self.active_hours_end is None:
            return True

        now_kst = datetime.now(KST)
        current_hour = now_kst.hour

        if self.active_hours_start <= self.active_hours_end:
            return self.active_hours_start <= current_hour < self.active_hours_end
        else:
            return current_hour >= self.active_hours_start or current_hour < self.active_hours_end

    def _should_alert(self, log_entry: dict) -> bool:
        """해당 로그에 대해 알림을 보낼지 판단"""
        if not self.enabled:
            return False

        # [NEW] 기동 유예 기간 체크 (가장 먼저)
        if self._is_in_startup_grace():
            return False

        if not self._is_in_active_hours():
            return False

        # 서비스 제외 체크
        service = log_entry.get('service', 'unknown')
        if service in self.excluded_services:
            return False

        # 레벨 필터 체크
        level = log_entry.get('level', 'INFO')
        if self._level_priority.get(level, 0) < self._level_priority.get(self.min_alert_level, 0):
            return False

        # [NEW] 기동 노이즈 패턴 필터 (유예 기간 이후에도 계속 적용)
        message = log_entry.get('message', '')
        if self._is_startup_noise(message):
            return False

        return True

    # ─── Circuit Breaker ───

    def _check_circuit(self) -> bool:
        """True이면 알림 차단"""
        if self._circuit_state == "closed":
            return False

        now = time.time()
        if self._circuit_state == "open":
            elapsed = now - self._circuit_opened_at
            open_duration = self._circuit_open_sec * self._circuit_backoff_multiplier
            if elapsed >= open_duration:
                self._circuit_state = "half_open"
                logger.info(
                    f"[SlackCircuit] HALF_OPEN: {int(elapsed)}초 경과, 탐색 알림 1건 허용"
                )
                return False  # half_open: 1건 허용
            return True  # 아직 OPEN

        # half_open: 1건 허용하여 복구 테스트
        return False

    def _on_alert_sent(self):
        """알림 전송 성공 후 호출"""
        self._sent_timestamps.append(time.time())
        if self._circuit_state == "half_open":
            self._circuit_state = "closed"
            self._circuit_backoff_multiplier = 1
            logger.info("[SlackCircuit] CLOSED: 정상 복귀")

    def _on_rate_limit_hit(self):
        """속도 초과 감지 시 차단기 OPEN"""
        if self._circuit_state != "open":
            self._circuit_state = "open"
            self._circuit_opened_at = time.time()
            open_min = (self._circuit_open_sec * self._circuit_backoff_multiplier) // 60
            logger.warning(
                f"[SlackCircuit] OPEN: {self._rate_window_sec}초 창에서 알림 "
                f"{self._max_alerts_per_window}건 초과 → {open_min}분간 차단"
            )
            # 차단 진입 전 마지막 경고 1건 전송
            self._send_circuit_open_notice()
        else:
            # 반복 폭주: 차단 시간 2배 (최대 16배)
            self._circuit_backoff_multiplier = min(self._circuit_backoff_multiplier * 2, 16)
            self._circuit_opened_at = time.time()  # 타이머 리셋
            open_min = (self._circuit_open_sec * self._circuit_backoff_multiplier) // 60
            logger.warning(f"[SlackCircuit] 차단 시간 연장 → {open_min}분")

    def _send_circuit_open_notice(self):
        """차단기 진입 시 Slack에 경고 1건 전송"""
        if not self.enabled or not self.webhook_url:
            return
        open_min = (self._circuit_open_sec * self._circuit_backoff_multiplier) // 60
        now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
        payload = {
            "text": f"🚫 Slack 알림 일시 차단 ({open_min}분)",
            "attachments": [{
                "color": "#E74A3B",
                "blocks": [
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": "🚫 알림 폭주 감지 — 자동 차단", "emoji": True}
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text":
                            f"*{self._rate_window_sec // 60}분* 안에 알림이 *{self._max_alerts_per_window}건* 초과되어 "
                            f"향후 *{open_min}분*간 알림을 차단합니다.\n"
                            f"차단 후 자동 복귀됩니다.\n*감지 시각:* {now_kst}"}
                    },
                    {
                        "type": "context",
                        "elements": [{"type": "mrkdwn", "text": f"📋 <{self.app_base_url}/admin/logs|로그 모니터링 열기>"}]
                    }
                ]
            }]
        }
        # 차단기 상태를 일시 우회해서 전송
        try:
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                self.webhook_url, data=data,
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    self._record_alert("circuit_open", f"{open_min}분 차단")
        except Exception as e:
            logger.error(f"[SlackCircuit] 차단 알림 전송 실패: {e}")

    # ─── 속도 제한 ───

    def _is_rate_limited(self) -> bool:
        """전송 속도가 한계를 초과했는지 확인"""
        now = time.time()
        while self._sent_timestamps and now - self._sent_timestamps[0] > self._rate_window_sec:
            self._sent_timestamps.popleft()
        return len(self._sent_timestamps) >= self._max_alerts_per_window

    # ─── 메시지 전송 ───

    def send_message(self, payload: dict) -> bool:
        if not self.enabled:
            return False

        # 1. Circuit Breaker 체크
        if self._check_circuit():
            logger.debug("[SlackCircuit] 차단기 OPEN — 메시지 스킵")
            return False

        # 2. 속도 제한 체크
        if self._is_rate_limited():
            self._on_rate_limit_hit()
            return False

        # 3. 실제 전송
        try:
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                self.webhook_url,
                data=data,
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    self._on_alert_sent()
                    return True
                return False
        except urllib.error.URLError as e:
            logger.error(f"Slack 알림 전송 실패: {e}")
            return False
        except Exception as e:
            logger.error(f"Slack 알림 오류: {e}")
            return False

    def _record_alert(self, alert_type: str, message: str):
        """알림 이력 기록"""
        self._alert_history.append({
            "type": alert_type,
            "message": message[:100],
            "time": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
        })

    # ─── CRITICAL/ERROR 알림 ───

    def notify_critical_error(self, log_entry: dict):
        if not self._should_alert(log_entry):
            return

        msg_hash = log_entry.get('message', '')[:100]
        now = time.time()

        # 쿨다운 체크
        if msg_hash in self._last_critical_alerts:
            if now - self._last_critical_alerts[msg_hash] < self.critical_cooldown:
                return

        self._last_critical_alerts[msg_hash] = now

        # 오래된 쿨다운 정리
        expired = [k for k, v in self._last_critical_alerts.items() if now - v > 3600]
        for k in expired:
            del self._last_critical_alerts[k]

        service = log_entry.get('service', 'unknown')
        container = log_entry.get('container', 'unknown')
        message = log_entry.get('message', 'N/A')
        level = log_entry.get('level', 'CRITICAL')

        color = "#4A154B" if level == "CRITICAL" else "#E74A3B"
        emoji = "🚨" if level == "CRITICAL" else "❌"

        now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")

        payload = {
            "text": f"{emoji} [{level}] {service} 에러 발생",
            "attachments": [{
                "color": color,
                "blocks": [
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": f"{emoji} {level} 에러 발생", "emoji": True}
                    },
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": f"*서비스:*\n{service}"},
                            {"type": "mrkdwn", "text": f"*컨테이너:*\n{container}"},
                            {"type": "mrkdwn", "text": f"*레벨:*\n{level}"},
                            {"type": "mrkdwn", "text": f"*감지 시각:*\n{now_kst}"}
                        ]
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"```{message[:500]}```"}
                    },
                    {
                        "type": "context",
                        "elements": [{"type": "mrkdwn", "text": f"📋 <{self.app_base_url}/admin/logs|로그 모니터링 열기>"}]
                    }
                ]
            }]
        }

        success = self.send_message(payload)
        if success:
            self._record_alert("critical", f"[{service}] {message[:60]}")
            logger.info(f"Slack {level} 알림 전송: [{service}] {message[:60]}")

    # ─── 에러 급증 감지 ───

    def track_error(self, log_entry: dict):
        now = time.time()
        service = log_entry.get('service', 'unknown')
        message = log_entry.get('message', '')

        # 제외 서비스는 추적하지 않음
        if service in self.excluded_services:
            return

        # [NEW] 기동 노이즈 패턴은 급증 추적에서도 제외
        if self._is_startup_noise(message):
            return

        self._error_window.append((now, service, message))

        while self._error_window and (now - self._error_window[0][0]) > self.spike_window_sec:
            self._error_window.popleft()

        if len(self._error_window) >= self.spike_threshold:
            self._send_spike_alert(len(self._error_window))

    def _send_spike_alert(self, error_count: int):
        if not self.enabled or not self._is_in_active_hours():
            return

        # [NEW] 기동 유예 기간 중에는 급증 알림도 억제
        if self._is_in_startup_grace():
            return

        now = time.time()
        if now - self._last_spike_alert < self.error_spike_cooldown:
            return

        self._last_spike_alert = now

        service_counts = {}
        recent_msgs = []
        for _, svc, msg in self._error_window:
            service_counts[svc] = service_counts.get(svc, 0) + 1
            short_msg = msg[:80] + "..." if len(msg) > 80 else msg
            if short_msg not in recent_msgs and len(recent_msgs) < 3:
                recent_msgs.append(short_msg)

        service_summary = " | ".join([f"{s}: {c}건" for s, c in sorted(
            service_counts.items(), key=lambda x: -x[1]
        )])

        minutes = self.spike_window_sec // 60
        now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")

        payload = {
            "text": f"⚠️ 에러 급증 감지: {minutes}분간 {error_count}건",
            "attachments": [{
                "color": "#F6C23E",
                "blocks": [
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": "⚠️ 에러 급증 감지", "emoji": True}
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text":
                            f"최근 *{minutes}분* 동안 에러가 *{error_count}건* 발생\n"
                            f"임계치 ({self.spike_threshold}건) 초과"}
                    },
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": f"*서비스별:*\n{service_summary}"},
                            {"type": "mrkdwn", "text": f"*감지 시각:*\n{now_kst}"}
                        ]
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"*최근 에러:*\n" + "\n".join([f"• `{m}`" for m in recent_msgs])}
                    },
                    {
                        "type": "context",
                        "elements": [{"type": "mrkdwn", "text": f"📋 <{self.app_base_url}/admin/logs|로그 모니터링 열기>"}]
                    }
                ]
            }]
        }

        success = self.send_message(payload)
        if success:
            self._record_alert("spike", f"{minutes}분간 {error_count}건")
            logger.warning(f"Slack 에러 급증 알림: {minutes}분간 {error_count}건")

    # ─── 자동 복구 알림 ───

    def notify_auto_recovery(self, container_name: str, service: str, action: str, reason: str, success: bool):
        """자동 복구 실행 결과를 Slack으로 알림"""
        if not self.enabled:
            return

        emoji = "🔄" if success else "⚠️"
        color = "#1CC88A" if success else "#E74A3B"
        status = "성공" if success else "실패"
        now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")

        payload = {
            "text": f"{emoji} 자동 복구 {status}: {container_name}",
            "attachments": [{
                "color": color,
                "blocks": [
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": f"{emoji} 자동 복구 {status}", "emoji": True}
                    },
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": f"*컨테이너:*\n{container_name}"},
                            {"type": "mrkdwn", "text": f"*서비스:*\n{service}"},
                            {"type": "mrkdwn", "text": f"*조치:*\n{action}"},
                            {"type": "mrkdwn", "text": f"*시각:*\n{now_kst}"}
                        ]
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"*사유:*\n{reason}"}
                    }
                ]
            }]
        }

        success_send = self.send_message(payload)
        if success_send:
            self._record_alert("recovery", f"{action}: {container_name} ({status})")

    # ─── 메인 진입점 ───

    def check_and_alert(self, logs: list):
        """수집된 로그 배치를 검사하여 알림 판단"""
        for log in logs:
            level = log.get('level', 'INFO')

            if level in ('CRITICAL', 'ERROR'):
                # CRITICAL/ERROR → 개별 알림 (필터 조건 확인)
                if self._should_alert(log):
                    self.notify_critical_error(log)

                # 급증 추적: min_alert_level 이상인 경우만 카운트
                # 예) min_alert_level=CRITICAL이면 ERROR는 급증 추적에서도 제외
                if not self._is_in_startup_grace():
                    if self._level_priority.get(level, 0) >= self._level_priority.get(self.min_alert_level, 0):
                        self.track_error(log)

    # ─── 테스트 ───

    def send_test_message(self) -> dict:
        if not self.enabled:
            return {"success": False, "error": "SLACK_WEBHOOK_URL이 설정되지 않았습니다."}

        now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
        in_hours = self._is_in_active_hours()
        grace_remaining = max(0.0, self._startup_grace_sec - (time.time() - self._start_time))

        payload = {
            "text": "✅ Lookalike 모니터링 알림 테스트",
            "attachments": [{
                "color": "#1CC88A",
                "blocks": [
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": "✅ 알림 테스트 성공", "emoji": True}
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text":
                            f"Slack 알림이 정상 설정되었습니다.\n"
                            f"*시각:* {now_kst}\n"
                            f"*알림 레벨:* {self.min_alert_level} 이상\n"
                            f"*활성 시간:* {self.active_hours_start or '제한없음'}시 ~ {self.active_hours_end or '제한없음'}시\n"
                            f"*현재 활성 시간대:* {'✅ 예' if in_hours else '❌ 아니오'}\n"
                            f"*기동 유예 잔여:* {int(grace_remaining)}초\n"
                            f"*차단기 상태:* {self._circuit_state}"}
                    }
                ]
            }]
        }

        # 테스트 메시지는 circuit breaker / rate limiter 우회하여 전송
        try:
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                self.webhook_url, data=data,
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                success = resp.status == 200
        except Exception as e:
            logger.error(f"Slack 테스트 전송 실패: {e}")
            success = False

        return {"success": success, "error": None if success else "전송 실패"}


# ─── 싱글턴 ───
_notifier_instance: Optional[SlackNotifier] = None


def get_slack_notifier() -> SlackNotifier:
    global _notifier_instance
    if _notifier_instance is None:
        _notifier_instance = SlackNotifier()
    return _notifier_instance
