let autoRefreshInterval = null;
let cpuChart = null;
let memChart = null;
const CHART_COLORS = [
    '#3366CC', '#22B573', '#FF8C1A', '#E63946', '#8E44AD', '#0EA5A0', '#D63384'
];

// 백엔드로부터 정확한 서비스 표시 이름이 넘어오므로 프론트엔드 매핑은 제거합니다.

document.addEventListener('DOMContentLoaded', () => {
    initCharts();
    refreshData();

    // Auto refresh
    const switchEl = document.getElementById('autoRefreshSwitch');
    const manualBtn = document.getElementById('btnManualRefresh');
    if (switchEl.checked) startAutoRefresh();

    switchEl.addEventListener('change', (e) => {
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
    autoRefreshInterval = setInterval(refreshData, 30000); // 30초마다
}

function stopAutoRefresh() {
    if (autoRefreshInterval) clearInterval(autoRefreshInterval);
    autoRefreshInterval = null;
}

function getProgressColor(percent) {
    if (percent < 60) return 'bg-success';
    if (percent < 80) return 'bg-warning';
    return 'bg-danger';
}

function initCharts() {
    const cpuCtx = document.getElementById('cpuChart').getContext('2d');
    const memCtx = document.getElementById('memChart').getContext('2d');

    // 시간 포맷 (ISO → '오후 4:13')
    function fmtTime(iso) {
        const t = iso.endsWith('Z') ? iso : iso + 'Z';
        return new Date(t).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
    }
    // 차트 외부에서도 사용할 수 있도록 저장
    window._fmtTime = fmtTime;

    // hover 시 해당 라인만 강조, 나머지 fade 처리
    const hoverPlugin = {
        id: 'hoverHighlight',
        beforeDatasetsDraw(chart) {
            const active = chart.getActiveElements();
            if (active.length > 0) {
                const activeIdx = active[0].datasetIndex;
                chart.data.datasets.forEach((ds, i) => {
                    ds.borderColor = i === activeIdx
                        ? ds._originalColor
                        : ds._originalColor + '20';  // 비활성: 12% 투명도 (확실히 fade)
                    ds.borderWidth = i === activeIdx ? 3.5 : 0.8;
                });
            } else {
                chart.data.datasets.forEach(ds => {
                    ds.borderColor = ds._originalColor;
                    ds.borderWidth = 1.5;
                });
            }
        }
    };

    const sharedOptions = (yConfig) => ({
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        scales: {
            x: {
                display: true,
                ticks: {
                    maxTicksLimit: 6,
                    maxRotation: 0,
                    font: { size: 10 },
                    color: '#aaa',
                    callback: (_, idx) => fmtTime(cpuChart?.data?.labels?.[idx] || memChart?.data?.labels?.[idx] || '')
                },
                grid: { display: false }
            },
            y: yConfig
        },
        elements: {
            point: { radius: 0, hoverRadius: 3, hoverBorderWidth: 2 },
            line: { borderJoinStyle: 'round' }
        },
        plugins: {
            legend: {
                position: 'bottom',
                labels: {
                    boxWidth: 10, boxHeight: 2,
                    padding: 15,
                    font: { size: 11 },
                    usePointStyle: false
                }
            },
            tooltip: {
                backgroundColor: 'rgba(0,0,0,0.8)',
                titleFont: { size: 12 },
                bodyFont: { size: 11 },
                padding: 10,
                cornerRadius: 6,
                mode: 'index',
                intersect: false,
                filter: item => item.parsed.y !== null && item.parsed.y !== undefined
            }
        }
    });

    cpuChart = new Chart(cpuCtx, {
        type: 'line',
        data: { labels: [], datasets: [] },
        options: sharedOptions({
            beginAtZero: true,
            suggestedMax: 50,
            ticks: {
                stepSize: 10,
                callback: v => v + '%',
                font: { size: 10 },
                color: '#aaa'
            },
            grid: { color: 'rgba(0,0,0,0.04)', drawBorder: false }
        }),
        plugins: [hoverPlugin]
    });

    memChart = new Chart(memCtx, {
        type: 'line',
        data: { labels: [], datasets: [] },
        options: sharedOptions({
            beginAtZero: true,
            ticks: {
                callback: v => (v >= 1000 ? (v / 1024).toFixed(1) + ' GB' : v.toFixed(0) + ' MB'),
                font: { size: 10 },
                color: '#aaa'
            },
            grid: { color: 'rgba(0,0,0,0.04)', drawBorder: false }
        }),
        plugins: [hoverPlugin]
    });
}

/**
 * 메인 갱신 함수: 인프라 상태(CPU/Memory), Elasticsearch 통계값, 컨테이너 스트림을 동시에 병렬 취합함.
 * I/O 대기를 최소화하고 렌더링 블로킹을 방지하기 위해 Promise.all 로 비동기 요청을 묶었음.
 * @returns {Promise<void>}
 */
async function refreshData() {
    // [성능 최적화] Phase 1: 빠른 API 먼저 렌더링 (~100ms)
    // 통합 API (system+DB) + ES 메트릭을 병렬 호출
    await Promise.all([
        loadInfraDashboard(),  // system+DB 통합 (1회 왕복)
        fetchStats(),
        fetchStream()
    ]);
    document.getElementById('lastUpdate').textContent = new Date().toLocaleTimeString('ko-KR');

    // [성능 최적화] Phase 2: Docker API 비동기 후속 로딩
    // await 없음 → 페이지 블로킹 않고 Docker 정보만 나중에 채워짐
    loadDockerStatus();
}

// ── Docker 마지막 체크 시각 조회 ──
async function loadDockerStatus() {
    try {
        const resp = await fetch('/api/admin/docker/containers');
        const data = await resp.json();
        const checkedEl = document.getElementById('dockerCheckedAt');
        if (data.checked_at && checkedEl) {
            const t = new Date(data.checked_at);
            checkedEl.textContent = `Docker 마지막 체크: ${t.toLocaleTimeString('ko-KR')}`;
        }
    } catch (e) {
        console.error('Docker 상태 조회 실패:', e);
    }
}

/**
 * 호스트 OS 자원 상태와 종속 DB(Postgres, Mongo, Redis) 헬스 체크를 서버로부터 통합 수신.
 * 각각을 개별로 폴링할 때 발생하는 네트워크 오버헤드를 아끼고 단일 응답으로 UI 컴포넌트를 전부 매핑하기 위함.
 * @returns {Promise<void>}
 */
async function loadInfraDashboard() {
    try {
        // [성능] 프리로드된 데이터가 있으면 재사용 (초기 로딩 시)
        let data;
        if (window.__preload?.infraDashboard) {
            data = await window.__preload.infraDashboard;
            window.__preload.infraDashboard = null; // 1회 사용 후 폐기
        }
        if (!data) {
            const response = await fetch('/api/admin/infra/dashboard');
            data = await response.json();
        }

        // System 데이터 적용
        if (data.system) {
            applySystemStatus(data.system);
        }
        // Database 데이터 적용
        if (data.database) {
            applyDatabaseStatus(data.database);
        }
    } catch (error) {
        console.error('인프라 대시보드 조회 실패:', error);
        // 폴백: 개별 호출
        await Promise.all([loadSystemStatus(), loadDatabaseStatus()]);
    }
}

// ── 시스템 상태 (기존 인프라 모니터링) ──
async function loadSystemStatus() {
    try {
        const response = await fetch('/api/admin/system/status');
        const data = await response.json();
        applySystemStatus(data);
    } catch (error) {
        console.error('시스템 상태 조회 실패:', error);
    }
}

function applySystemStatus(data) {
    // CPU
    document.getElementById('cpuPercent').textContent = `${data.cpu_percent.toFixed(1)}%`;
    document.getElementById('cpuProgress').style.width = `${data.cpu_percent}%`;
    document.getElementById('cpuProgress').className = `progress-bar ${getProgressColor(data.cpu_percent)}`;

    let cpuInfo = [];
    if (data.cpu_freq_current > 0) cpuInfo.push(`${(data.cpu_freq_current / 1000).toFixed(2)}GHz`);
    if (data.cpu_cores_physical > 0 && data.cpu_cores_logical > 0) cpuInfo.push(`${data.cpu_cores_physical}C/${data.cpu_cores_logical}T`);
    document.getElementById('cpuDetail').textContent = cpuInfo.join(' | ') || 'CPU 정보 없음';

    // 메모리
    const memoryUsedGB = (data.memory_used / 1024 / 1024 / 1024).toFixed(1);
    const memoryTotalGB = (data.memory_total / 1024 / 1024 / 1024).toFixed(1);
    document.getElementById('memoryPercent').textContent = `${data.memory_percent.toFixed(1)}%`;
    document.getElementById('memoryProgress').style.width = `${data.memory_percent}%`;
    document.getElementById('memoryProgress').className = `progress-bar ${getProgressColor(data.memory_percent)}`;
    document.getElementById('memoryDetail').textContent = `${memoryUsedGB}GB / ${memoryTotalGB}GB`;

    // 디스크
    const diskUsedGB = (data.disk_used / 1024 / 1024 / 1024).toFixed(1);
    const diskTotalGB = (data.disk_total / 1024 / 1024 / 1024).toFixed(1);
    const diskFreeGB = ((data.disk_total - data.disk_used) / 1024 / 1024 / 1024).toFixed(1);
    document.getElementById('diskPercent').textContent = `${data.disk_percent.toFixed(1)}%`;
    document.getElementById('diskProgress').style.width = `${data.disk_percent}%`;
    document.getElementById('diskProgress').className = `progress-bar ${getProgressColor(data.disk_percent)}`;
    document.getElementById('diskDetail').textContent = `${diskUsedGB}GB / ${diskTotalGB}GB`;
    document.getElementById('diskTotal').textContent = `${diskTotalGB} GB`;
    document.getElementById('diskFree').textContent = `${diskFreeGB} GB`;

    // 업타임
    const days = Math.floor(data.uptime_seconds / 86400);
    const hours = Math.floor((data.uptime_seconds % 86400) / 3600);
    document.getElementById('uptime').textContent = `${days}일 ${hours}시간`;
}

// ── 데이터베이스 상태 ──
async function loadDatabaseStatus() {
    try {
        const response = await fetch('/api/admin/database/status');
        const data = await response.json();
        applyDatabaseStatus(data);
    } catch (error) {
        console.error('데이터베이스 상태 조회 실패:', error);
    }
}

function applyDatabaseStatus(data) {
    // PostgreSQL
    if (data.postgresql.status === 'healthy') {
        document.getElementById('pgStatus').className = 'badge bg-success';
        document.getElementById('pgStatus').textContent = '정상';
        document.getElementById('pgConnections').textContent = data.postgresql.active_connections;
        document.getElementById('pgSize').textContent = `${data.postgresql.database_size_mb} MB`;
    } else {
        document.getElementById('pgStatus').className = 'badge bg-danger';
        document.getElementById('pgStatus').textContent = '오류';
    }

    // MongoDB
    if (data.mongodb.status === 'healthy') {
        document.getElementById('mongoStatus').className = 'badge bg-success';
        document.getElementById('mongoStatus').textContent = '정상';
        document.getElementById('mongoCollections').textContent = data.mongodb.collections;
        document.getElementById('mongoSize').textContent = `${data.mongodb.data_size_mb} MB`;
    } else {
        document.getElementById('mongoStatus').className = 'badge bg-danger';
        document.getElementById('mongoStatus').textContent = '오류';
    }

    // Redis
    if (data.redis.status === 'healthy') {
        document.getElementById('redisStatus').className = 'badge bg-success';
        document.getElementById('redisStatus').textContent = '정상';
        document.getElementById('redisMemory').textContent = `${data.redis.used_memory_mb} MB`;
        document.getElementById('redisKeys').textContent = data.redis.total_keys.toLocaleString();
    } else {
        document.getElementById('redisStatus').className = 'badge bg-danger';
        document.getElementById('redisStatus').textContent = '오류';
    }
}

// ── 메트릭 통계 (Elasticsearch) ──
async function fetchStats() {
    try {
        // [성능] 프리로드된 데이터가 있으면 재사용
        let data;
        if (window.__preload?.metricsStats) {
            data = await window.__preload.metricsStats;
            window.__preload.metricsStats = null;
        }
        if (!data) {
            const resp = await fetch('/api/metrics/stats');
            data = await resp.json();
        }

        let totalCpu = 0, totalMem = 0, count = 0;
        let maxMem = 0, maxMemService = '-';
        let maxCpu = 0, maxCpuService = '-';

        for (const [svc, stats] of Object.entries(data)) {
            totalCpu += stats.avg_cpu;
            totalMem += stats.avg_mem;
            count++;

            if (stats.max_mem_mb > maxMem) {
                maxMem = stats.max_mem_mb;
                maxMemService = svc;
            }

            let cpuVal = stats.max_cpu || stats.avg_cpu;
            if (cpuVal > maxCpu) {
                maxCpu = cpuVal;
                maxCpuService = svc;
            }
        }

        if (count > 0) {
            document.getElementById('avgCpu').innerText = (totalCpu / count).toFixed(1) + '%';
            document.getElementById('avgMem').innerText = (totalMem / count).toFixed(1) + '%';

            document.getElementById('maxMemVal').innerText = maxMem.toFixed(0) + ' MB';
            document.getElementById('maxMemDetail').innerText = '사용 1위: ' + maxMemService;

            if (document.getElementById('maxCpuVal')) {
                document.getElementById('maxCpuVal').innerText = maxCpu.toFixed(1) + '%';
                document.getElementById('maxCpuDetail').innerText = '사용 1위: ' + maxCpuService;
            }
        }
    } catch (e) {
        console.error("Stats error", e);
    }
}

// ── 메트릭 스트림 (Elasticsearch) ──
async function fetchStream() {
    try {
        // [성능] 프리로드된 데이터가 있으면 재사용
        let data;
        if (window.__preload?.metricsStream) {
            data = await window.__preload.metricsStream;
            window.__preload.metricsStream = null;
        }
        if (!data) {
            const resp = await fetch('/api/metrics/stream?limit=200');
            data = await resp.json();
        }
        const logs = data.metrics.reverse();

        if (!logs.length) return;

        updateCharts(logs);
        updateTable(logs);
    } catch (e) {
        console.error("Stream error", e);
    }
}

/**
 * 각각의 도커 컨테이너가 최근 뱉어낸 시계열 메트릭(CPU, Memory)을 Elasticsearch에서 긁어와 차트 위젯에 주입함.
 * 컨테이너 간의 자원 경합이나 특정 서비스의 메모리 누수 버그를 실시간 그래프 교차 분석으로 찾게 도와줌.
 * @param {Array} logs 백엔드 응답을 배열로 변환한 데이터
 */
function updateCharts(logs) {
    const uniqueTimes = [...new Set(logs.map(l => l.timestamp))].sort();
    const services = [...new Set(logs.map(l => l.service))];

    // [성능 최적화] Map 1회 빌드 → O(1) 조회 (기존 Array.find O(N×M) 제거)
    const dataMap = new Map();
    logs.forEach(l => dataMap.set(`${l.service}|${l.timestamp}`, l));

    const datasetsCpu = [];
    const datasetsMem = [];

    services.forEach((svc, idx) => {
        const color = CHART_COLORS[idx % CHART_COLORS.length];
        const dataCpu = [];
        const dataMem = [];

        uniqueTimes.forEach(t => {
            const entry = dataMap.get(`${svc}|${t}`);
            if (entry) {
                dataCpu.push(entry.cpu_percent);
                dataMem.push(entry.memory_usage / 1024 / 1024);
            } else {
                dataCpu.push(null);
                dataMem.push(null);
            }
        });

        datasetsCpu.push({
            label: svc, borderColor: color, backgroundColor: color,
            _originalColor: color,
            data: dataCpu, borderWidth: 1.5, tension: 0.4, fill: false
        });
        datasetsMem.push({
            label: svc, borderColor: color, backgroundColor: color,
            _originalColor: color,
            data: dataMem, borderWidth: 1.5, tension: 0.4, fill: false
        });
    });

    cpuChart.data.labels = uniqueTimes;
    cpuChart.data.datasets = datasetsCpu;
    cpuChart.update('none'); // [성능] 애니메이션 비활성화

    memChart.data.labels = uniqueTimes;
    memChart.data.datasets = datasetsMem;
    memChart.update('none'); // [성능] 애니메이션 비활성화
}

function updateTable(logs) {
    const latestMap = {};
    logs.forEach(log => { latestMap[log.container] = log; });

    const tbody = document.getElementById('metricsTableBody');
    tbody.innerHTML = '';

    Object.values(latestMap).sort((a, b) => a.service.localeCompare(b.service)).forEach(log => {
        const tr = document.createElement('tr');
        const ts = log.timestamp.endsWith('Z') ? log.timestamp : log.timestamp + 'Z';
        const timeStr = new Date(ts).toLocaleString('ko-KR', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
        const memMB = (log.memory_usage / 1024 / 1024).toFixed(1);
        let limitStr = "";
        if (log.memory_limit && log.memory_limit > 0) {
            const limitMB = (log.memory_limit / 1024 / 1024).toFixed(1);
            limitStr = ` / ${limitMB}`;
        }

        let cpuClass = "";
        if (log.cpu_percent > 80) cpuClass = "text-danger fw-bold";
        else if (log.cpu_percent > 50) cpuClass = "text-warning fw-bold";

        tr.innerHTML = `
                <td><span class="badge bg-light text-dark border">${log.service}</span></td>
                <td class="small">${log.container}</td>
                <td class="${cpuClass} text-end pe-4">${log.cpu_percent.toFixed(2)} %</td>
                <td class="text-end pe-4">${log.memory_percent.toFixed(2)} %</td>
                <td class="text-end pe-4">${memMB}${limitStr} MB</td>
                <td class="text-muted small text-end pe-4">${timeStr}</td>
            `;
        tbody.appendChild(tr);
    });
}

// 페이지 언로드 시 인터벌 정리
window.addEventListener('beforeunload', () => {
    if (autoRefreshInterval) clearInterval(autoRefreshInterval);
});