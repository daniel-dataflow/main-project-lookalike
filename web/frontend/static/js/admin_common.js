/* =========================================================================
   [admin_base.js]
========================================================================= */

// 최우선 실행: 렌더링 전에 상태 적용
(function () {
    if (localStorage.getItem('sidebarCollapsed') === 'true') {
        document.documentElement.className = 'sidebar-collapsed-init';
    }
})();

// ── (1) API 데이터 프리로드 ──
// HTML 파싱 시점에 즉시 fetch 시작 → DOMContentLoaded 전에 응답 도착
// 각 페이지 JS에서 window.__preload[key]를 await하여 중복 호출 방지
(function () {
    const preloadMap = {
        '/admin/infra': [
            { key: 'infraDashboard', url: '/api/admin/infra/dashboard' },
            { key: 'metricsStats', url: '/api/metrics/stats' },
            { key: 'metricsStream', url: '/api/metrics/stream' }
        ],
        '/admin/logs': [
            { key: 'logsDashboard', url: '/api/logs/dashboard' }
        ]
    };
    const path = location.pathname;
    const targets = preloadMap[path];
    if (targets) {
        window.__preload = {};
        targets.forEach(t => {
            window.__preload[t.key] = fetch(t.url).then(r => r.json()).catch(() => null);
        });
    }
})();

// ── (2) 네비게이션 프리페치 ──
// 사이드바 링크에 마우스를 올리면 해당 페이지 HTML을 미리 다운로드
// → 클릭 시 브라우저 캐시에서 즉시 로드되어 체감 전환 속도 향상
(function () {
    const prefetched = new Set();
    document.addEventListener('mouseover', function (e) {
        const link = e.target.closest('.nav-link[href^="/admin/"]');
        if (!link) return;
        const href = link.getAttribute('href');
        if (!href || href === location.pathname || prefetched.has(href)) return;
        prefetched.add(href);
        const el = document.createElement('link');
        el.rel = 'prefetch';
        el.href = href;
        document.head.appendChild(el);
    });
})();

// 서버에서 이미 권한을 검증하고 렌더링했으므로, 사용자 이름만 한 번 더 가져옵니다.
document.addEventListener('DOMContentLoaded', async function () {
    try {
        const meResp = await fetch('/api/auth/me', { credentials: 'same-origin' });
        const meData = await meResp.json();
        const name = (meData.success && meData.user && meData.user.name) ? meData.user.name : 'Admin';
        applyUserName(name);
    } catch (e) {
        // 무시
    }
});

function applyUserName(name) {
    const el1 = document.getElementById('adminUserName');
    const el2 = document.getElementById('adminTopName');
    if (el1) el1.textContent = name;
    if (el2) el2.textContent = name;
}

/**
 * 모바일 환경이나 화면이 좁을 때 어드민 LNB(좌측 사이드바)를 접거나 펼치는 토글 기능.
 * 뷰포트 영역을 최대로 확보하기 위해 사용되며, 상태는 로컬 스토리지에 영구 저장됨.
 */
function toggleSidebar() {
    const sidebar = document.getElementById('adminSidebar');
    const mainContent = document.getElementById('adminMainContent');

    sidebar.classList.toggle('collapsed');
    mainContent.classList.toggle('expanded');

    // 로컬 스토리지에 상태 저장
    localStorage.setItem('sidebarCollapsed', sidebar.classList.contains('collapsed'));
}

// 페이지 로드 시 저장된 사이드바 상태 복원
document.addEventListener('DOMContentLoaded', function () {
    const isCollapsed = localStorage.getItem('sidebarCollapsed') === 'true';
    const sidebar = document.getElementById('adminSidebar');
    const mainContent = document.getElementById('adminMainContent');

    // 초기화 클래스 제거
    document.documentElement.classList.remove('sidebar-collapsed-init');

    if (isCollapsed) {
        sidebar.classList.add('collapsed');
        mainContent.classList.add('expanded');
    }
});

/**
 * 어드민 전용 세션을 즉시 파기하고 로그인 페이지로 킥 아웃함.
 * 업무 완료 후 보안을 유지하기 위해 필수적인 로그아웃 절차.
 * @returns {Promise<void>}
 */
async function adminLogout() {
    try {
        await fetch('/api/auth/admin/logout', {
            method: 'POST',
            credentials: 'same-origin',
        });
    } catch (e) { }
    location.href = '/';
}


/* =========================================================================
   [admin_login.js]
========================================================================= */

/**
 * 어드민 로그인 페이지(/admin/login)의 폼 데이터 전송을 가로채어 API 서버 단으로 자격 검증을 요청함.
 * 어드민 계정은 사내 보안을 위해 OAuth에 의존하지 않고 별도의 Hash 기반 State를 지님.
 * @param {Event} e 폼 submit 이벤트 객체
 * @returns {Promise<void>}
 */
async function adminLogin(e) {
    e.preventDefault();

    const username = document.getElementById('adminUsername').value.trim();
    const password = document.getElementById('adminPassword').value.trim();
    if (!username || !password) return;

    const btn = document.getElementById('adminLoginBtn');
    const spinner = document.getElementById('adminLoginSpinner');
    const btnText = btn.querySelector('.btn-text');
    const errorEl = document.getElementById('adminAuthError');

    btn.disabled = true;
    spinner.classList.remove('d-none');
    btnText.classList.add('d-none');
    errorEl.style.display = 'none';

    try {
        const resp = await fetch('/api/auth/admin/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ username, password }),
        });

        if (resp.ok) {
            // 인증 성공 후 인프라 대시보드로 이동
            location.href = '/admin/infra';
        } else {
            const err = await resp.json();
            document.getElementById('adminAuthErrorMsg').textContent =
                err.detail || '인증에 실패했습니다.';
            errorEl.style.display = 'flex';
            document.getElementById('adminPassword').value = '';
            document.getElementById('adminPassword').focus();
        }
    } catch (e) {
        document.getElementById('adminAuthErrorMsg').textContent = '서버 연결에 실패했습니다.';
        errorEl.style.display = 'flex';
    } finally {
        btn.disabled = false;
        spinner.classList.add('d-none');
        btnText.classList.remove('d-none');
    }
}

/* =========================================================================
   [admin_dashboard.js]
========================================================================= */

document.addEventListener('DOMContentLoaded', function () {
    // API Chart
    const apiChartEl = document.getElementById('apiChart');
    if (apiChartEl) {
        const ctxApi = apiChartEl.getContext('2d');
        new Chart(ctxApi, {
            type: 'line',
            data: {
                labels: ['09:00', '10:00', '11:00', '12:00', '13:00', '14:00', '15:00'],
                datasets: [{
                    label: 'Requests',
                    data: [120, 132, 101, 134, 90, 105, 125],
                    borderColor: '#0d6efd',
                    tension: 0.4,
                    fill: false
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: { y: { beginAtZero: true, grid: { display: false } } }
            }
        });
    }

    // Log Chart
    const logChartEl = document.getElementById('logChart');
    if (logChartEl) {
        const ctxLog = logChartEl.getContext('2d');
        new Chart(ctxLog, {
            type: 'doughnut',
            data: {
                labels: ['Success', 'Fail'],
                datasets: [{
                    data: [92, 8],
                    backgroundColor: ['#198754', '#dc3545'],
                    borderWidth: 0
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                cutout: '70%'
            }
        });
    }
});