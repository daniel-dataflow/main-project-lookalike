
import logging
import time
import json
import os
from datetime import datetime, timezone, timedelta
from collections import deque
from typing import Optional
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

# 한국 시간대 (UTC+9)
KST = timezone(timedelta(hours=9))


class SlackNotifier:
    """
    Slack Webhook 기반 알림 서비스

    기능:
    1. CRITICAL/ERROR 로그 발생 시 Slack 알림
    2. 에러 급증 감지 시 요약 알림
    3. 알림 레벨 필터 (CRITICAL만 / ERROR이상 / WARN이상)
    4. 활성 시간대 설정 (업무시간만 알림)
    5. 서비스별 제외 필터
    6. 쿨다운으로 알림 폭주 방지
    7. 전체 비활성화 토글
    """

    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or os.environ.get("SLACK_WEBHOOK_URL", "")
        self.enabled = bool(self.webhook_url)

        # ─── 알림 필터 설정 ───
        # 알림 대상 레벨: "CRITICAL", "ERROR", "WARN"
        # 해당 레벨 '이상'의 로그만 알림 (CRITICAL > ERROR > WARN > INFO)
        self.min_alert_level = "CRITICAL"  # 기본: CRITICAL만 알림

        # 활성 시간대 (KST 기준, None이면 24시간)
        self.active_hours_start: Optional[int] = None  # e.g. 9 (09:00)
        self.active_hours_end: Optional[int] = None     # e.g. 18 (18:00)

        # 알림 제외 서비스 목록
        self.excluded_services: list[str] = []

        # ─── 쿨다운 설정 (초) ───
        self.critical_cooldown = 60         # 같은 메시지 쿨다운
        self.error_spike_cooldown = 300     # 급증 알림 쿨다운

        # ─── 에러 급증 감지 ───
        self.spike_window_sec = 600         # 10분 윈도우
        self.spike_threshold = 15           # 임계치

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
        # APP_ENV=production → APP_BASE_URL_PROD
        # APP_ENV=development/local/기타 → APP_BASE_URL_LOCAL
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
            logger.info(f"Slack 알림 서비스 활성화됨 (env={_env}, base_url={self.app_base_url})")
        else:
            logger.info("Slack 알림 비활성화 (SLACK_WEBHOOK_URL 미설정)")

    # ─── 설정 관리 ───

    def set_webhook_url(self, url: str):
        self.webhook_url = url
        self.enabled = bool(url)
        logger.info(f"Slack webhook URL {'설정됨' if self.enabled else '해제됨'}")

    def set_enabled(self, enabled: bool):
        """알림 활성/비활성 토글"""
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

        logger.info(f"Slack 알림 설정 업데이트: level={self.min_alert_level}, "
                     f"hours={self.active_hours_start}-{self.active_hours_end}, "
                     f"excluded={self.excluded_services}")

    def get_config(self) -> dict:
        """현재 설정 반환"""
        return {
            "enabled": self.enabled,
            "webhook_url_set": bool(self.webhook_url),
            "webhook_url_preview": self.webhook_url[:30] + "..." if len(self.webhook_url) > 30 else self.webhook_url if self.webhook_url else "",
            "min_alert_level": self.min_alert_level,
            "active_hours_start": self.active_hours_start,
            "active_hours_end": self.active_hours_end,
            "excluded_services": self.excluded_services,
            "critical_cooldown_sec": self.critical_cooldown,
            "spike_window_sec": self.spike_window_sec,
            "spike_threshold": self.spike_threshold,
            "spike_cooldown_sec": self.error_spike_cooldown,
        }

    def get_status(self) -> dict:
        """현재 런타임 상태 반환"""
        now = time.time()
        return {
            "enabled": self.enabled,
            "is_in_active_hours": self._is_in_active_hours(),
            "error_window_size": len(self._error_window),
            "spike_threshold": self.spike_threshold,
            "last_spike_alert_ago_sec": round(now - self._last_spike_alert) if self._last_spike_alert > 0 else None,
            "active_cooldowns": len(self._last_critical_alerts),
            "recent_alerts": list(self._alert_history)[-10:],
        }

    # ─── 필터 체크 ───

    def _is_in_active_hours(self) -> bool:
        """현재 시각이 활성 시간대인지 확인 (KST 기준)"""
        if self.active_hours_start is None or self.active_hours_end is None:
            return True  # 시간 제한 없음 = 항상 활성

        now_kst = datetime.now(KST)
        current_hour = now_kst.hour

        if self.active_hours_start <= self.active_hours_end:
            # 예: 09~18
            return self.active_hours_start <= current_hour < self.active_hours_end
        else:
            # 예: 22~06 (야간)
            return current_hour >= self.active_hours_start or current_hour < self.active_hours_end

    def _should_alert(self, log_entry: dict) -> bool:
        """해당 로그에 대해 알림을 보낼지 판단"""
        if not self.enabled:
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

        return True

    # ─── 메시지 전송 ───

    def send_message(self, payload: dict) -> bool:
        if not self.enabled:
            return False

        try:
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                self.webhook_url,
                data=data,
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
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
        timestamp = log_entry.get('timestamp', datetime.utcnow().isoformat())

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

        self._error_window.append((now, service, message))

        while self._error_window and (now - self._error_window[0][0]) > self.spike_window_sec:
            self._error_window.popleft()

        if len(self._error_window) >= self.spike_threshold:
            self._send_spike_alert(len(self._error_window))

    def _send_spike_alert(self, error_count: int):
        if not self.enabled or not self._is_in_active_hours():
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
                        "text": {"type": "plain_text", "text": f"⚠️ 에러 급증 감지", "emoji": True}
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

                # 급증 추적 (필터와 별개로 항상 추적)
                self.track_error(log)

    # ─── 테스트 ───

    def send_test_message(self) -> dict:
        if not self.enabled:
            return {"success": False, "error": "SLACK_WEBHOOK_URL이 설정되지 않았습니다."}

        now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
        in_hours = self._is_in_active_hours()

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
                            f"*현재 활성 시간대:* {'✅ 예' if in_hours else '❌ 아니오'}"}
                    }
                ]
            }]
        }

        success = self.send_message(payload)
        return {"success": success, "error": None if success else "전송 실패"}


# ─── 싱글턴 ───
_notifier_instance: Optional[SlackNotifier] = None


def get_slack_notifier() -> SlackNotifier:
    global _notifier_instance
    if _notifier_instance is None:
        _notifier_instance = SlackNotifier()
    return _notifier_instance
