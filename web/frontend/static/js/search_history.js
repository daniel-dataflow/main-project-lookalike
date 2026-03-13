document.addEventListener('DOMContentLoaded', async function () {
    const historyLoading = document.getElementById('historyLoading');
    const loginRequired = document.getElementById('loginRequired');
    const emptyHistory = document.getElementById('emptyHistory');
    const historyContent = document.getElementById('historyContent');
    const loadMoreArea = document.getElementById('loadMoreArea');
    const loadMoreBtn = document.getElementById('loadMoreBtn');
    const deleteAllBtn = document.getElementById('deleteAllBtn');
    const filterBtns = document.querySelectorAll('.filter-btn');

    let allHistory = [];
    let currentOffset = 0;
    const pageSize = 20;
    let totalCount = 0;
    let currentFilter = 'all';

    // 1. 로그인 확인
    let isLoggedIn = false;
    try {
        const authResp = await fetch('/api/auth/me');
        const authData = await authResp.json();
        isLoggedIn = authData.success && authData.user;
    } catch (e) {
        console.error('인증 확인 실패:', e);
    }

    if (!isLoggedIn) {
        historyLoading.classList.add('d-none');
        loginRequired.classList.remove('d-none');
        return;
    }

    // 2. 히스토리 로드
    await loadHistory();

    // 3. 필터 버튼
    filterBtns.forEach(btn => {
        btn.addEventListener('click', function () {
            filterBtns.forEach(b => b.classList.remove('active'));
            this.classList.add('active');
            currentFilter = this.dataset.filter;
            renderHistory();
        });
    });

    // 4. 더 보기
    loadMoreBtn.addEventListener('click', async function () {
        currentOffset += pageSize;
        await loadHistory(true);
    });

    // 5. 전체 삭제
    deleteAllBtn.addEventListener('click', async function (e) {
        e.preventDefault();
        if (!confirm('모든 검색 히스토리를 삭제하시겠습니까?')) return;

        try {
            const resp = await fetch('/api/search/history', { method: 'DELETE' });
            const data = await resp.json();

            if (resp.ok && data.success) {
                allHistory = [];
                totalCount = 0;
                renderHistory();
                showToast('검색 히스토리가 삭제되었습니다.');
            } else {
                alert(data.detail || '삭제에 실패했습니다.');
            }
        } catch (err) {
            console.error(err);
            alert('서버 오류가 발생했습니다.');
        }
    });

    /**
     * 서버로부터 누적된 검색 사용자의 히스토리 데이터를 비동기로 Paging(Offset) 스트리밍함.
     * 첫 로드 시 혹은 더 보기 버튼 클릭 시 호출되며, UI 멈춤 방지를 위해 로딩 스피너 제어가 동반됨.
     * @param {boolean} append 기존 데이터에 추가 여부
     * @returns {Promise<void>}
     */
    async function loadHistory(append = false) {
        if (!append) {
            historyLoading.classList.remove('d-none');
            historyContent.classList.add('d-none');
        }

        try {
            const resp = await fetch(`/api/search/history?limit=${pageSize}&offset=${currentOffset}`);

            if (resp.status === 401) {
                historyLoading.classList.add('d-none');
                loginRequired.classList.remove('d-none');
                return;
            }

            const data = await resp.json();

            if (!data.success) throw new Error('히스토리 로드 실패');

            totalCount = data.total;

            if (append) {
                allHistory = allHistory.concat(data.history);
            } else {
                allHistory = data.history;
            }

            renderHistory();

        } catch (err) {
            console.error('히스토리 로드 실패:', err);
            historyLoading.classList.add('d-none');
            emptyHistory.classList.remove('d-none');
        }
    }

    /**
     * 로드된 `allHistory` 배열을 기준 필터(오늘, 일주일)에 맞춰 클라이언트 단에서 필터링 후 가상 DOM 노드로 렌더함.
     * 서버에 반복 요청을 보내지 않고 기확보된 In-memory 리스트를 정제해 성능 저하를 방지함.
     */
    function renderHistory() {
        historyLoading.classList.add('d-none');

        // 필터 적용
        let filtered = allHistory;
        const now = new Date();
        const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const weekAgo = new Date(todayStart.getTime() - 7 * 24 * 60 * 60 * 1000);

        if (currentFilter === 'today') {
            filtered = allHistory.filter(item => new Date(item.create_dt) >= todayStart);
        } else if (currentFilter === 'week') {
            filtered = allHistory.filter(item => new Date(item.create_dt) >= weekAgo);
        }

        if (filtered.length === 0) {
            historyContent.classList.add('d-none');
            loadMoreArea.classList.add('d-none');
            emptyHistory.classList.remove('d-none');
            return;
        }

        emptyHistory.classList.add('d-none');

        // 날짜별 그룹핑
        const groups = {};
        filtered.forEach(item => {
            const dt = new Date(item.create_dt);
            const dateKey = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
            if (!groups[dateKey]) groups[dateKey] = [];
            groups[dateKey].push(item);
        });

        // 렌더링
        let html = '';
        const sortedDates = Object.keys(groups).sort().reverse();

        sortedDates.forEach(dateKey => {
            const items = groups[dateKey];
            const dateLabel = formatDateLabel(dateKey);
            const count = items.length;

            html += `
                <div class="mb-5">
                    <div class="d-flex align-items-center mb-3">
                        <i class="far fa-calendar text-secondary me-2"></i>
                        <h6 class="fw-bold mb-0">${dateLabel} <span class="text-secondary">${count}개</span></h6>
                    </div>
                    <div class="row row-cols-2 row-cols-md-3 g-3">`;

            items.forEach(item => {
                const thumbUrl = item.thumbnail_url || 'https://placehold.co/150x150/f8f9fa/999?text=No+Image';
                const resultText = item.result_count > 0 ? `유사 상품 ${item.result_count}개` : '결과 없음';
                const searchText = item.search_text ? `<div class="text-truncate small text-muted">${escapeHtml(item.search_text)}</div>` : '';
                const categoryBadge = item.category ? `<span class="badge bg-light text-dark small">${escapeHtml(item.category)}</span>` : '';
                const genderBadge = item.gender ? `<span class="badge bg-light text-secondary small">${translateGender(item.gender)}</span>` : '';

                html += `
                    <div class="col">
                        <div class="history-card bg-white shadow-sm" style="cursor: pointer; border-radius: 12px; overflow: hidden;"
                             onclick="showDetail(${item.log_id})">
                            <img src="${thumbUrl}" alt="검색 이미지"
                                class="history-img" style="width: 100%; height: 140px; object-fit: contain; background-color: #f8f9fa;"
                                onerror="this.src='https://placehold.co/150x150/f8f9fa/999?text=No+Image'">
                            <div class="p-2 text-center">
                                ${searchText}
                                <div class="d-flex justify-content-center gap-1 mb-1">
                                    ${genderBadge}
                                    ${categoryBadge}
                                </div>
                                <div class="text-secondary small">${resultText}</div>
                            </div>
                        </div>
                    </div>`;
            });

            html += `
                    </div>
                </div>`;
        });

        historyContent.innerHTML = html;
        historyContent.classList.remove('d-none');

        // 더 보기 버튼
        if (allHistory.length < totalCount) {
            loadMoreArea.classList.remove('d-none');
        } else {
            loadMoreArea.classList.add('d-none');
        }
    }

    function formatDateLabel(dateKey) {
        const dt = new Date(dateKey + 'T00:00:00');
        const now = new Date();
        const todayStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
        const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000);
        const yesterdayStr = `${yesterday.getFullYear()}-${String(yesterday.getMonth() + 1).padStart(2, '0')}-${String(yesterday.getDate()).padStart(2, '0')}`;

        if (dateKey === todayStr) return '오늘';
        if (dateKey === yesterdayStr) return '어제';
        return `${dt.getMonth() + 1}월 ${dt.getDate()}일`;
    }

    function showToast(msg) {
        const container = document.getElementById('toastContainer');
        if (!container) return;
        const toast = document.createElement('div');
        toast.className = 'toast show align-items-center text-white bg-dark border-0 mb-2';
        toast.innerHTML = `<div class="d-flex"><div class="toast-body">${msg}</div></div>`;
        container.appendChild(toast);
        setTimeout(() => toast.remove(), 3000);
    }
});

function translateGender(gender) {
    if (!gender) return '';
    const g = gender.toLowerCase();
    if (g === 'man' || g === 'men') return '남성';
    if (g === 'woman' || g === 'women') return '여성';
    if (g === 'unisex') return '공용';
    return gender;
}

function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    const div = document.createElement('div');
    div.textContent = String(str);
    return div.innerHTML;
}

/**
 * 특정 검색 로그 기록 카드를 눌렀을 때 팝업 모달을 띄우고 DB의 상세 결과를 렌더링함.
 * 사용자가 이전에 어떤 결과 상품들을 확인했었는지 복기할 수 있도록 지원하는 UX 기능.
 * @param {number|string} logId 백엔드에서 생성된 검색기록 테이블 PK
 */
async function showDetail(logId) {
    const modal = new bootstrap.Modal(document.getElementById('detailModal'));
    const modalBody = document.getElementById('detailModalBody');

    modalBody.innerHTML = '<div class="text-center py-3"><div class="spinner-border text-secondary" role="status"></div></div>';
    modal.show();

    try {
        const resp = await fetch(`/api/search/history/${logId}`);
        const data = await resp.json();

        if (!data.success) throw new Error('상세 조회 실패');

        let html = '';

        // 썸네일
        if (data.thumbnail_url) {
            html += `<div class="text-center mb-3">
                <img src="${data.thumbnail_url}" alt="검색 이미지"
                    style="max-height: 200px; border-radius: 8px;"
                    onerror="this.style.display='none'">
            </div>`;
        }

        // 검색 정보
        html += '<div class="mb-3 small text-muted">';
        if (data.search_text) html += `<div>검색어: ${data.search_text}</div>`;
        if (data.gender) html += `<div>성별: ${translateGender(data.gender)}</div>`;
        if (data.category) html += `<div>카테고리: ${data.category}</div>`;
        if (data.create_dt) html += `<div>검색 시간: ${new Date(data.create_dt).toLocaleString('ko-KR')}</div>`;
        html += '</div>';

        // 검색 결과 상품 목록
        if (data.results && data.results.length > 0) {
            html += '<div class="row row-cols-1 row-cols-md-2 row-cols-lg-3 g-3">';
            data.results.forEach(item => {
                const price = item.price ? item.price.toLocaleString() : '-';
                html += `
                    <div class="col">
                        <div class="card h-100 border-0 shadow-sm">
                            <img src="${item.image_url}" class="card-img-top" alt="${item.product_name}"
                                style="height: 350px; object-fit: cover; object-position: top;" onerror="this.src='https://placehold.co/300x400?text=No+Image'">
                            <div class="card-body p-2">
                                <small class="text-muted">${item.brand}</small>
                                <div class="fw-bold small text-truncate">${item.product_name}</div>
                                <div class="d-flex justify-content-between align-items-center mt-1">
                                    <span class="text-danger fw-bold small">${price}원</span>
                                </div>
                                <div class="text-muted" style="font-size: 0.7rem;">${item.mall_name}</div>
                            </div>
                        </div>
                    </div>`;
            });
            html += '</div>';
        } else {
            html += '<p class="text-muted text-center">검색 결과가 없습니다.</p>';
        }

        modalBody.innerHTML = html;

    } catch (err) {
        console.error(err);
        modalBody.innerHTML = '<p class="text-danger text-center">상세 정보를 불러오는데 실패했습니다.</p>';
    }
}