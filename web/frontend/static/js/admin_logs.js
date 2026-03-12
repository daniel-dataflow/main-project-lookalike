let autoRefreshInterval = null;
    let logModal = null;
    let trendChart = null;

    document.addEventListener('DOMContentLoaded', () => {
        logModal = new bootstrap.Modal(document.getElementById('logDetailModal'));
        initTrendChart();
        refreshAll();

        document.querySelectorAll('input[name="serviceFilter"]').forEach(el => el.addEventListener('change', fetchLogs));
        document.querySelectorAll('input[name="levelFilter"]').forEach(el => el.addEventListener('change', fetchLogs));
        document.getElementById('keywordSearch').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') fetchLogs();
        });
        const manualBtn = document.getElementById('btnManualRefresh');
        document.getElementById('autoRefreshSwitch').addEventListener('change', (e) => {
            if (e.target.checked) {
                startAutoRefresh();
                manualBtn.disabled = true;
            } else {
                stopAutoRefresh();
                manualBtn.disabled = false;
            }
        });
    });

    function startAutoRefresh() {
        if (autoRefreshInterval) clearInterval(autoRefreshInterval);
        autoRefreshInterval = setInterval(() => refreshAll(false), 10000);
    }
    function stopAutoRefresh() {
        if (autoRefreshInterval) { clearInterval(autoRefreshInterval); autoRefreshInterval = null; }
    }

    async function refreshAll(showLoading = true) {
        // [성능 최적화] stats/trend/top-errors/service-health를 단일 dashboard API로 대체
        // ES msearch로 4개 쿼리를 1번 왕복 처리 + 30초 캐싱
        await Promise.all([
            fetchDashboard(),      // stats + trend + top-errors + service-health 통합
            fetchLogs(showLoading),
            fetchPipelineStatus(),
            fetchAlertStatus(),
            fetchRecoveryStatus()
        ]);
    }

    // [성능 최적화] 통합 대시보드 API (ES msearch 1회 왕복)
    async function fetchDashboard() {
        try {
            // [성능] 프리로드된 데이터가 있으면 재사용 (초기 로딩 시)
            let data;
            if (window.__preload?.logsDashboard) {
                data = await window.__preload.logsDashboard;
                window.__preload.logsDashboard = null; // 1회 사용 후 폐기
            }
            if (!data) {
                const resp = await fetch('/api/logs/dashboard');
                data = await resp.json();
            }

            // --- stats 적용 ---
            const levels = data.stats?.by_level || {};
            const criticalCnt = levels['CRITICAL'] || 0;
            const errorCnt = levels['ERROR'] || 0;
            const warnCnt = levels['WARN'] || 0;
            const infoCnt = levels['INFO'] || 0;
            const total = criticalCnt + errorCnt + warnCnt + infoCnt;
            setText('statCritical', criticalCnt);
            setText('statError', errorCnt);
            setText('statWarn', warnCnt);
            setText('statInfo', infoCnt);
            setText('statTotal', total);
            const badgeThroughput = document.getElementById('badgeThroughput');
            if (badgeThroughput) {
                badgeThroughput.innerHTML = `<i class="fas fa-tachometer-alt me-1"></i>수집: ${(total / 60).toFixed(0)}건/분`;
            }

            // --- trend 차트 적용 ---
            if (trendChart && data.trend) {
                _applyTrend(data.trend);
            }

            // --- top-errors 적용 ---
            if (data.top_errors) {
                _applyTopErrors(data.top_errors);
            }

            // --- service-health 적용 ---
            if (data.service_health) {
                _applyServiceHealth(data.service_health);
            }

        } catch (e) {
            console.error('Dashboard fetch error:', e);
            // 폴백: 개별 API 호출
            await Promise.all([fetchStats(), fetchTrend(), fetchTopErrors(), fetchServiceHealth()]);
        }
    }

    // ── 트렌드 차트 ──
    function initTrendChart() {
        const ctx = document.getElementById('trendChart').getContext('2d');
        trendChart = new Chart(ctx, {
            type: 'bar',
            data: { labels: [], datasets: [] },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                scales: {
                    x: { stacked: true, ticks: { maxTicksLimit: 12, font: { size: 10 } } },
                    y: { stacked: true, beginAtZero: true }
                },
                plugins: { legend: { position: 'top', labels: { usePointStyle: true, padding: 15 } } }
            }
        });
    }

    async function fetchTrend() {
        try {
            const resp = await fetch('/api/logs/trend');
            const data = await resp.json();
            _applyTrend(data.trend || []);
        } catch (e) { console.error('Trend error:', e); }
    }

    function _applyTrend(trend) {
        const labels = trend.map(t => {
            const d = new Date(t.time);
            return `${d.getHours().toString().padStart(2, '0')}:00`;
        });
        trendChart.data.labels = labels;
        trendChart.data.datasets = [
            { label: 'ERROR', data: trend.map(t => t.ERROR), backgroundColor: 'rgba(231,74,59,0.85)', borderRadius: 2 },
            { label: 'WARN', data: trend.map(t => t.WARN), backgroundColor: 'rgba(246,194,62,0.85)', borderRadius: 2 },
            { label: 'INFO', data: trend.map(t => t.INFO), backgroundColor: 'rgba(54,185,204,0.35)', borderRadius: 2 }
        ];
        trendChart.update('none'); // [성능] 애니메이션 비활성화
        checkErrorSpike(trend);
    }

    function checkErrorSpike(trend) {
        const banner = document.getElementById('alertBanner');
        if (trend.length < 3) { banner.className = 'd-none'; return; }

        const last = trend[trend.length - 1];
        const prevErrors = trend.slice(0, -1).map(t => t.ERROR);
        const avgError = prevErrors.reduce((a, b) => a + b, 0) / prevErrors.length;

        if (last.ERROR > 5 && last.ERROR > avgError * 3) {
            banner.className = 'alert alert-danger alert-banner d-flex align-items-center mb-3';
            banner.innerHTML = `
                <i class="fas fa-radiation-alt me-2 fs-5"></i>
                <div>
                    <strong>⚠️ 에러 급증 감지!</strong>
                    최근 1시간 에러 <strong>${last.ERROR}건</strong> — 평균 대비 <strong>${(last.ERROR / Math.max(avgError, 1)).toFixed(1)}배</strong> 증가.
                    즉시 ERROR 로그를 확인해주세요.
                </div>
            `;
        } else {
            banner.className = 'd-none';
        }
    }

    async function fetchServiceHealth() {
        try {
            const resp = await fetch('/api/logs/service-health');
            const data = await resp.json();
            _applyServiceHealth(data.services || []);
        } catch (e) { console.error('Health error:', e); }
    }

    function _applyServiceHealth(services) {
        const container = document.getElementById('serviceHealthMap');
        if (!services || !services.length) {
            container.innerHTML = '<span class="text-muted small">데이터 없음</span>';
            return;
        }
        container.innerHTML = services.map(svc => {
            const tooltip = `${svc.total}건 중 에러 ${svc.errors}건 (${svc.error_rate}%)`;
            return `
                <div class="d-flex align-items-center gap-1" title="${tooltip}" style="cursor:default;">
                    <span class="health-dot ${svc.status}"></span>
                    <span class="small fw-bold">${svc.service}</span>
                    ${svc.errors > 0 ? `<span class="badge bg-danger" style="font-size:0.65rem;">${svc.errors}</span>` : ''}
                </div>
            `;
        }).join('');
    }

    async function fetchTopErrors() {
        try {
            const resp = await fetch('/api/logs/top-errors');
            const data = await resp.json();
            _applyTopErrors(data.top_errors || []);
        } catch (e) { console.error('Top errors error:', e); }
    }

    function _applyTopErrors(top_errors) {
        const container = document.getElementById('topErrorsContainer');
        if (!top_errors || !top_errors.length) {
            container.innerHTML = '<div class="text-center text-muted py-3"><i class="fas fa-check-circle me-2 text-success"></i>최근 24시간 에러 없음</div>';
            return;
        }
        container.innerHTML = top_errors.map((err, idx) => {
            const rankClass = idx === 0 ? 'rank-1' : idx === 1 ? 'rank-2' : idx === 2 ? 'rank-3' : 'rank-other';
            const lastSeen = err.last_seen ? new Date(err.last_seen).toLocaleString('ko-KR') : '-';
            const services = err.services.map(s => `<span class="badge bg-light text-dark border me-1">${s}</span>`).join('');
            const shortMsg = err.message.length > 120 ? err.message.substring(0, 120) + '...' : err.message;
            return `
                <div class="d-flex align-items-start gap-3 py-2 ${idx < top_errors.length - 1 ? 'border-bottom' : ''}">
                    <div class="error-rank ${rankClass}">${idx + 1}</div>
                    <div class="flex-grow-1 min-width-0">
                        <div class="font-monospace small text-break mb-1">${shortMsg}</div>
                        <div class="d-flex flex-wrap align-items-center gap-2">
                            <span class="badge bg-danger">${err.count}회</span>
                            ${services}
                            <span class="text-muted" style="font-size:0.7rem;">마지막: ${lastSeen}</span>
                        </div>
                    </div>
                </div>
            `;
        }).join('');
    }

    // ── 통계 ──
    async function fetchStats() {
        try {
            const resp = await fetch('/api/logs/stats');
            const data = await resp.json();
            const levels = data.by_level || {};
            const criticalCnt = levels['CRITICAL'] || 0;
            const errorCnt = levels['ERROR'] || 0;
            const warnCnt = levels['WARN'] || 0;
            const infoCnt = levels['INFO'] || 0;
            const total = criticalCnt + errorCnt + warnCnt + infoCnt;

            setText('statCritical', criticalCnt);
            setText('statError', errorCnt);
            setText('statWarn', warnCnt);
            setText('statInfo', infoCnt);
            setText('statTotal', total);

            const badgeThroughput = document.getElementById('badgeThroughput');
            if (badgeThroughput) {
                const tpm = (total / 60).toFixed(0);
                badgeThroughput.innerHTML = `<i class="fas fa-tachometer-alt me-1"></i>수집: ${tpm}건/분`;
            }
        } catch (e) { console.error('Stats error:', e); }
    }

    function setText(id, text) { const el = document.getElementById(id); if (el) el.innerText = text; }

    // ── 파이프라인 상태 ──
    async function fetchPipelineStatus() {
        try {
            const resp = await fetch('/api/logs/pipeline-status');
            const data = await resp.json();
            const badgeStorage = document.getElementById('badgeStorage');
            if (badgeStorage && data.elasticsearch && data.elasticsearch.store_size !== undefined) {
                const gb = (data.elasticsearch.store_size / (1024 * 1024 * 1024)).toFixed(2);
                badgeStorage.innerHTML = `<i class="fas fa-hdd me-1"></i>저장소: ${gb} GB`;
            }
        } catch (e) { console.error(e); }
    }

    // ── 로그 스트림 ──
    async function fetchLogs(showLoading = true) {
        const tableBody = document.getElementById('logTableBody');
        const mobileList = document.getElementById('mobileLogList');

        if (showLoading === true) {
            tableBody.innerHTML = '<tr><td colspan="5" class="text-center py-4 text-muted"><i class="fas fa-spinner fa-spin me-2"></i>로딩 중...</td></tr>';
            mobileList.innerHTML = '<div class="text-center py-5 text-muted"><i class="fas fa-spinner fa-spin fa-2x"></i></div>';
        }

        const service = document.querySelector('input[name="serviceFilter"]:checked').value;
        const level = document.querySelector('input[name="levelFilter"]:checked').value;
        const keyword = document.getElementById('keywordSearch').value.trim();

        let url = `/api/logs/stream?size=100`;
        if (service !== 'ALL') url += `&service=${service}`;
        if (level !== 'ALL') url += `&level=${level}`;
        if (keyword) url += `&keyword=${encodeURIComponent(keyword)}`;

        try {
            const resp = await fetch(url);
            const data = await resp.json();

            if (!data.logs || data.logs.length === 0) {
                tableBody.innerHTML = '<tr><td colspan="5" class="text-center py-4 text-muted">검색 결과가 없습니다.</td></tr>';
                mobileList.innerHTML = '<div class="text-center py-5 text-muted">검색 결과가 없습니다.</div>';
                return;
            }
            renderTable(data.logs, tableBody);
            renderMobileCards(data.logs, mobileList);
        } catch (e) {
            console.error("Log load failed", e);
            tableBody.innerHTML = '<tr><td colspan="5" class="text-center py-4 text-danger">실패</td></tr>';
        }
    }

    function renderTable(logs, tbody) {
        tbody.innerHTML = '';
        logs.forEach(log => {
            const timeStr = new Date(log.timestamp).toLocaleString('ko-KR');
            let rowClass = "", badgeClass = "bg-secondary";

            if (log.level === 'CRITICAL') { rowClass = "table-dark"; badgeClass = "bg-dark"; }
            else if (log.level === 'ERROR') { rowClass = "table-danger"; badgeClass = "bg-danger"; }
            else if (log.level === 'WARN') { rowClass = "table-warning"; badgeClass = "bg-warning text-dark"; }
            else if (log.level === 'INFO') { badgeClass = "bg-info text-dark"; }

            const tr = document.createElement('tr');
            if (rowClass) tr.classList.add(rowClass);
            tr.style.cursor = 'pointer';
            tr.innerHTML = `
                <td class="text-nowrap small text-muted">${timeStr}</td>
                <td><span class="badge ${badgeClass}">${log.level}</span></td>
                <td><span class="fw-bold small">${log.service}</span></td>
                <td class="small text-truncate" style="max-width:150px;" title="${log.container}">${log.container}</td>
                <td class="text-break small font-monospace">${log.message}</td>
            `;
            tr.onclick = () => showLogDetail(log);
            tbody.appendChild(tr);
        });
    }

    function renderMobileCards(logs, container) {
        container.innerHTML = '';
        logs.forEach(log => {
            const timeStr = new Date(log.timestamp).toLocaleString('ko-KR');
            let levelClass = "level-info", badgeClass = "bg-info text-dark";
            if (log.level === 'CRITICAL') { levelClass = "level-critical"; badgeClass = "bg-dark"; }
            else if (log.level === 'ERROR') { levelClass = "level-error"; badgeClass = "bg-danger"; }
            else if (log.level === 'WARN') { levelClass = "level-warn"; badgeClass = "bg-warning text-dark"; }

            const card = document.createElement('div');
            card.className = `log-card ${levelClass}`;
            card.innerHTML = `
                <div style="padding:12px 16px;">
                    <div class="d-flex justify-content-between align-items-center mb-1">
                        <div>
                            <span class="badge ${badgeClass} me-2">${log.level}</span>
                            <span class="fw-bold small text-dark">${log.service}</span>
                            <span class="text-muted small mx-1">•</span>
                            <span class="text-muted small text-truncate d-inline-block" style="max-width:100px;vertical-align:bottom;">${log.container}</span>
                        </div>
                    </div>
                    <div class="mb-2"><span class="small text-secondary">${timeStr}</span></div>
                    <div class="text-dark small text-break" style="display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;font-family:monospace;">${log.message}</div>
                </div>
            `;
            card.onclick = () => showLogDetail(log);
            container.appendChild(card);
        });
    }

    function showLogDetail(log) {
        const badge = document.getElementById('modalLevelBadge');
        badge.className = 'badge';
        badge.innerText = log.level;
        if (log.level === 'CRITICAL') badge.classList.add('bg-dark');
        else if (log.level === 'ERROR') badge.classList.add('bg-danger');
        else if (log.level === 'WARN') badge.classList.add('bg-warning', 'text-dark');
        else badge.classList.add('bg-info', 'text-dark');

        document.getElementById('modalTimestamp').innerText = new Date(log.timestamp).toLocaleString('ko-KR');
        document.getElementById('modalService').innerText = log.service;
        document.getElementById('modalContainer').innerText = log.container;
        document.getElementById('modalMessage').innerText = log.message;
        logModal.show();
    }

    // ── Slack 알림 설정 ──
    async function fetchAlertStatus() {
        try {
            const [configResp, statusResp] = await Promise.all([
                fetch('/api/logs/alerts/config'),
                fetch('/api/logs/alerts/status')
            ]);
            const config = await configResp.json();
            const status = await statusResp.json();

            // 상태 배지
            const badge = document.getElementById('slackStatusBadge');
            const sw = document.getElementById('slackEnabledSwitch');
            if (config.enabled) {
                badge.className = 'badge bg-success'; badge.innerText = '활성';
                sw.checked = true;
            } else {
                badge.className = 'badge bg-secondary'; badge.innerText = '비활성';
                sw.checked = false;
            }

            // 설정값 반영
            if (config.webhook_url_preview) {
                document.getElementById('slackWebhookUrl').placeholder = config.webhook_url_preview + ' (설정됨)';
            }
            if (config.min_alert_level) {
                document.getElementById('slackMinLevel').value = config.min_alert_level;
            }
            const startSel = document.getElementById('slackHoursStart');
            const endSel = document.getElementById('slackHoursEnd');
            startSel.value = config.active_hours_start !== null && config.active_hours_start !== undefined ? String(config.active_hours_start) : '';
            endSel.value = config.active_hours_end !== null && config.active_hours_end !== undefined ? String(config.active_hours_end) : '';
            document.getElementById('slackSpikeThreshold').value = config.spike_threshold || 15;

            // [NEW] 폭주 방지 설정값 반영
            if (config.startup_grace_sec !== undefined)
                document.getElementById('slackStartupGrace').value = Math.round(config.startup_grace_sec / 60);
            if (config.max_alerts_per_window !== undefined)
                document.getElementById('slackMaxAlerts').value = config.max_alerts_per_window;
            if (config.circuit_open_sec !== undefined)
                document.getElementById('slackCircuitOpenMin').value = Math.round(config.circuit_open_sec / 60);

            // 제외 서비스 체크박스
            const excluded = config.excluded_services || [];
            document.querySelectorAll('#excludeServicesGroup input').forEach(cb => {
                cb.checked = excluded.includes(cb.value);
            });

            // [NEW] Circuit Breaker 상태 배지
            const circuitBadge = document.getElementById('slackCircuitBadge');
            const circuitState = status.circuit_state || 'closed';
            if (circuitState === 'closed') {
                circuitBadge.className = 'badge bg-success'; circuitBadge.innerText = '정상 (CLOSED)';
            } else if (circuitState === 'open') {
                circuitBadge.className = 'badge bg-danger'; circuitBadge.innerText = '차단 중 (OPEN)';
            } else {
                circuitBadge.className = 'badge bg-warning text-dark'; circuitBadge.innerText = '복귀 시도 (HALF-OPEN)';
            }

            // [NEW] 기동 유예 잔여 시간
            const graceInfo = document.getElementById('slackGraceInfo');
            const graceRemain = status.startup_grace_remaining_sec || 0;
            if (graceRemain > 0) {
                const m = Math.floor(graceRemain / 60), s = graceRemain % 60;
                graceInfo.innerHTML = `<span class="text-warning"><i class="fas fa-hourglass-half me-1"></i>유예 잔여 ${m}분 ${s}초</span>`;
            } else {
                graceInfo.innerHTML = '<span class="text-success"><i class="fas fa-check me-1"></i>유예 종료</span>';
            }

            // 런타임 상태
            const winInfo = document.getElementById('slackWindowInfo');
            const parts = [];
            if (status.error_window_size > 0) parts.push(`에러윈도우: ${status.error_window_size}건`);
            if (status.active_cooldowns > 0) parts.push(`쿨다운: ${status.active_cooldowns}건`);
            if (!status.is_in_active_hours) parts.push('⏸ 비활성 시간대');
            if (status.rate_window_sent_count > 0) parts.push(`${status.rate_window_sent_count}/${status.rate_limit_max}건 전송됨`);
            winInfo.innerHTML = parts.length ? `<i class="fas fa-info-circle me-1"></i>${parts.join(' | ')}` : '';

            const histEl = document.getElementById('slackAlertHistory');
            if (status.recent_alerts && status.recent_alerts.length > 0) {
                const last = status.recent_alerts[status.recent_alerts.length - 1];
                histEl.innerHTML = `<i class="fas fa-bell me-1"></i>마지막 알림: ${last.type} — ${last.time}`;
            } else {
                histEl.innerHTML = '';
            }
        } catch (e) { console.error('Alert config error:', e); }
    }

    async function toggleSlackEnabled() {
        const enabled = document.getElementById('slackEnabledSwitch').checked;
        try {
            await fetch('/api/logs/alerts/config', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: enabled })
            });
            fetchAlertStatus();
        } catch (e) { console.error(e); }
    }

    async function saveSlackConfig() {
        const url = document.getElementById('slackWebhookUrl').value.trim();
        const level = document.getElementById('slackMinLevel').value;
        const startVal = document.getElementById('slackHoursStart').value;
        const endVal = document.getElementById('slackHoursEnd').value;
        const threshold = parseInt(document.getElementById('slackSpikeThreshold').value) || 15;
        const graceMin = parseInt(document.getElementById('slackStartupGrace').value) || 15;
        const maxAlerts = parseInt(document.getElementById('slackMaxAlerts').value) || 10;
        const circuitMin = parseInt(document.getElementById('slackCircuitOpenMin').value) || 30;
        const excluded = [];
        document.querySelectorAll('#excludeServicesGroup input:checked').forEach(cb => excluded.push(cb.value));

        const body = {
            min_alert_level: level,
            active_hours_start: startVal !== '' ? parseInt(startVal) : null,
            active_hours_end: endVal !== '' ? parseInt(endVal) : null,
            excluded_services: excluded,
            spike_threshold: threshold,
            startup_grace_sec: graceMin * 60,
            max_alerts_per_window: maxAlerts,
            circuit_open_sec: circuitMin * 60
        };
        if (url) body.webhook_url = url;

        try {
            const resp = await fetch('/api/logs/alerts/config', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const data = await resp.json();
            alert(data.message);
            fetchAlertStatus();
        } catch (e) { alert('저장 실패: ' + e.message); }
    }

    async function testSlackAlert() {
        try {
            const resp = await fetch('/api/logs/alerts/test', { method: 'POST' });
            if (resp.ok) { alert('✅ 테스트 메시지 전송 성공!'); }
            else { const d = await resp.json(); alert('❌ 실패: ' + (d.detail || '오류')); }
        } catch (e) { alert('❌ 실패: ' + e.message); }
    }

    // ── 자동 복구 설정 ──
    async function fetchRecoveryStatus() {
        try {
            const [configResp, statusResp] = await Promise.all([
                fetch('/api/logs/recovery/config'),
                fetch('/api/logs/recovery/status')
            ]);
            const config = await configResp.json();
            const status = await statusResp.json();

            const badge = document.getElementById('recoveryStatusBadge');
            const sw = document.getElementById('recoveryEnabledSwitch');
            if (config.enabled) {
                badge.className = 'badge bg-success'; badge.innerText = '활성';
                sw.checked = true;
            } else {
                badge.className = 'badge bg-secondary'; badge.innerText = '비활성';
                sw.checked = false;
            }

            document.getElementById('recoveryThreshold').value = config.restart_threshold || 10;
            document.getElementById('recoveryWindow').value = (config.error_window_sec || 300) / 60;
            document.getElementById('recoveryMaxPerHour').value = config.max_restarts_per_hour || 3;

            // 재시작 이력
            const histContainer = document.getElementById('recoveryHistoryContainer');
            const history = status.restart_history || [];
            if (history.length > 0) {
                histContainer.innerHTML = `
                    <div class="small fw-bold mb-1"><i class="fas fa-history me-1"></i>재시작 이력 (최근)</div>
                    <div class="table-responsive" style="max-height:150px;overflow-y:auto;">
                        <table class="table table-sm table-bordered mb-0" style="font-size:0.78rem;">
                            <thead class="table-light"><tr><th>시각</th><th>서비스</th><th>사유</th><th>결과</th></tr></thead>
                            <tbody>${history.map(h => `
                                <tr class="${h.success ? '' : 'table-danger'}">
                                    <td class="text-nowrap">${h.time}</td>
                                    <td>${h.service}</td>
                                    <td class="text-break">${h.reason}</td>
                                    <td>${h.success ? '✅' : '❌'}</td>
                                </tr>
                            `).join('')}</tbody>
                        </table>
                    </div>
                `;
            } else {
                histContainer.innerHTML = '<div class="small text-muted"><i class="fas fa-check-circle me-1 text-success"></i>재시작 이력 없음</div>';
            }
        } catch (e) { console.error('Recovery error:', e); }
    }

    async function toggleRecoveryEnabled() {
        const enabled = document.getElementById('recoveryEnabledSwitch').checked;
        try {
            await fetch('/api/logs/recovery/config', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: enabled })
            });
            fetchRecoveryStatus();
        } catch (e) { console.error(e); }
    }

    async function saveRecoveryConfig() {
        const body = {
            restart_threshold: parseInt(document.getElementById('recoveryThreshold').value) || 10,
            error_window_sec: (parseInt(document.getElementById('recoveryWindow').value) || 5) * 60,
            max_restarts_per_hour: parseInt(document.getElementById('recoveryMaxPerHour').value) || 3
        };
        try {
            const resp = await fetch('/api/logs/recovery/config', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const data = await resp.json();
            alert(data.message);
            fetchRecoveryStatus();
        } catch (e) { alert('저장 실패: ' + e.message); }
    }

    window.addEventListener('beforeunload', () => { if (autoRefreshInterval) clearInterval(autoRefreshInterval); });
    function saveRecoveryConfig() {
        // ... (생략 없이 원래 코드 유지 - multi_replace의 경우 대체 구문만 수정하므로 EndLine에 맞게 앞뒤 맥락 포함) ...
    }

    // ─── 로그 복사 및 다운로드 기능 ───
    async function copyLogDetail() {
        const level = document.getElementById('modalLevelBadge').innerText;
        const timestamp = document.getElementById('modalTimestamp').innerText;
        const service = document.getElementById('modalService').innerText;
        const container = document.getElementById('modalContainer').innerText;
        const message = document.getElementById('modalMessage').innerText;

        const copyText = `[${timestamp}] [${level}] [${service}] ${container}\n${message}`;

        try {
            await navigator.clipboard.writeText(copyText);
            alert("로그 내용이 복사되었습니다.");
        } catch (err) {
            console.error('Failed to copy text: ', err);
            alert("복사에 실패했습니다.");
        }
    }

    function downloadLogs() {
        const service = document.querySelector('input[name="serviceFilter"]:checked').value;
        const level = document.querySelector('input[name="levelFilter"]:checked').value;
        const keyword = document.getElementById('keywordSearch').value.trim();

        let url = `/api/logs/download?size=10000`;
        if (service !== 'ALL') url += `&service=${service}`;
        if (level !== 'ALL') url += `&level=${level}`;
        if (keyword) url += `&keyword=${encodeURIComponent(keyword)}`;

        // 브라우저를 통해 스트리밍 텍스트 다운로드 실행
        window.location.href = url;
    }

    // ─── 과거 에러 로그 강제 초기화 (Purge) ───
    async function purgeAllLogs() {
        if (!confirm("⚠️ 정말로 기록된 모든 컨테이너 에러 로그를 삭제하시겠습니까?\n이 작업은 되돌릴 수 없으며 대시보드가 초기 상태로 돌아갑니다.")) {
            return;
        }

        try {
            const resp = await fetch('/api/logs/purge', {
                method: 'DELETE'
            });
            const data = await resp.json();

            if (resp.ok && data.success) {
                alert(`✅ 초기화 완료!\n삭제된 로그 건수: ${data.deleted_count}건\n대시보드를 새로고침합니다.`);

                // 프론트엔드 캐시 무효화 및 화면 0으로 리셋 처리
                if (window.__preload) {
                    window.__preload.logsDashboard = null;
                }

                // [성능] 애니메이션 없이 즉각 트렌드 차트 데이터를 통째로 날림
                if (trendChart) {
                    trendChart.data.labels = [];
                    trendChart.data.datasets = [];
                    trendChart.update('none');
                }

                // 캐시가 비워진 새로운 데이터를 가져오기 위해 0.5초 대기 후 강제 갱신
                setTimeout(() => {
                    refreshAll();
                    fetchAlertStatus();
                    fetchRecoveryStatus();
                }, 500);
            } else {
                alert(`❌ 초기화 실패: ${data.detail || data.error || '알 수 없는 오류'}`);
            }
        } catch (err) {
            console.error('Purge error:', err);
            alert(`❌ 초기화 요청에 실패했습니다: ${err.message}`);
        }
    }