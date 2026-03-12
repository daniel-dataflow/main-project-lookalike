let adminCurrentPage = 1;
    let adminCurrentFilter = '';

    document.addEventListener('DOMContentLoaded', function () {
        loadAdminInquiries();
        initAnswerForm();
    });

    // 문의 목록 로드 (관리자)
    async function loadAdminInquiries(page = 1) {
        adminCurrentPage = page;
        try {
            let url = `/api/inquiries/admin/list?page=${page}&page_size=10`;
            if (adminCurrentFilter) {
                url += `&status_filter=${adminCurrentFilter}`;
            }

            const resp = await fetch(url, { credentials: 'same-origin' });
            if (!resp.ok) {
                console.error('관리자 문의 목록 조회 실패:', resp.status);
                return;
            }
            const data = await resp.json();

            // 카운트 업데이트
            document.getElementById('adminTotalCount').textContent = data.total || 0;
            let pending = 0, answered = 0;
            if (data.items) {
                data.items.forEach(i => {
                    if ((i.comment_count || 0) > 0) answered++;
                    else pending++;
                });
            }
            // 전체를 로드했을 때만 카운트 업데이트
            if (!adminCurrentFilter) {
                document.getElementById('adminPendingCount').textContent = pending;
                document.getElementById('adminAnsweredCount').textContent = answered;
            }
            document.getElementById('adminResultInfo').textContent = `총 ${data.total}건`;

            const listEl = document.getElementById('adminInquiryList');
            const emptyEl = document.getElementById('adminEmptyState');

            if (!data.items || data.items.length === 0) {
                listEl.innerHTML = '';
                emptyEl.classList.remove('d-none');
                return;
            }

            emptyEl.classList.add('d-none');
            listEl.innerHTML = data.items.map(item => {
                const hasAnswer = (item.comment_count || 0) > 0;
                return `
            <tr class="inquiry-row" onclick="viewAdminInquiry(${item.inquiry_board_id})">
                <!-- [데스크탑] 번호 -->
                <td class="text-center text-muted d-none d-md-table-cell">${item.inquiry_board_id}</td>
                
                <!-- 제목 (모바일에서는 여기에 모든 정보 표시) -->
                <td>
                    <!-- [모바일] 상단: 번호 + 상태 뱃지 -->
                    <div class="d-flex justify-content-between align-items-center mb-2 d-md-none">
                        <span class="badge bg-light text-secondary border">#${item.inquiry_board_id}</span>
                        <span class="admin-status-badge ${hasAnswer ? 'admin-status-answered' : 'admin-status-pending'}">
                            ${hasAnswer ? '답변완료' : '대기중'}
                        </span>
                    </div>

                    <!-- 제목 텍스트 -->
                    <div class="fw-semibold text-break mb-1" style="max-width: 100%;">
                        ${escapeHtml(item.title)}
                    </div>

                    <!-- [모바일] 하단 정보: 작성자, 날짜, 버튼 -->
                    <div class="d-md-none text-muted small mt-3 d-flex justify-content-between align-items-center">
                        <div>
                            <span class="me-2"><i class="far fa-user me-1"></i>${escapeHtml(item.author_name || '-')}</span>
                            <span><i class="far fa-clock me-1"></i>${formatDate(item.create_dt)}</span>
                        </div>
                        <button class="btn btn-sm btn-outline-dark rounded-pill px-3 ms-2"
                            onclick="event.stopPropagation(); viewAdminInquiry(${item.inquiry_board_id});">
                            ${hasAnswer ? '보기' : '답변'}
                        </button>
                    </div>
                </td>
                
                <!-- [데스크탑] 작성자 -->
                <td class="d-none d-md-table-cell">
                    <span class="text-muted" style="font-size: 0.85rem;">${escapeHtml(item.author_name || item.author_id || '-')}</span>
                </td>
                
                <!-- [데스크탑] 상태 -->
                <td class="text-center d-none d-md-table-cell">
                    <span class="admin-status-badge ${hasAnswer ? 'admin-status-answered' : 'admin-status-pending'}">
                        ${hasAnswer
                        ? '<i class="fas fa-check-circle"></i> 답변완료'
                        : '<i class="fas fa-clock"></i> 대기중'}
                    </span>
                </td>
                
                <!-- [데스크탑] 작성일 -->
                <td class="text-center text-muted d-none d-md-table-cell" style="font-size: 0.8rem;">
                    ${formatDate(item.create_dt)}
                </td>
                
                <!-- [데스크탑] 관리 -->
                <td class="text-center d-none d-md-table-cell">
                    <button class="btn btn-sm btn-outline-dark rounded-pill px-3"
                        onclick="event.stopPropagation(); viewAdminInquiry(${item.inquiry_board_id});">
                        ${hasAnswer ? '보기' : '답변'}
                    </button>
                </td>
            </tr>
            `;
            }).join('');

            // 페이지네이션
            renderAdminPagination(data.total_pages, page);

        } catch (e) {
            console.error('관리자 문의 목록 로드 실패:', e);
        }
    }

    // 상태 필터
    function filterInquiries(status) {
        adminCurrentFilter = status;

        document.querySelectorAll('#statusFilter .nav-link').forEach(btn => {
            btn.classList.remove('active');
            if (btn.dataset.filter === status) btn.classList.add('active');
        });

        loadAdminInquiries(1);
    }

    // 문의 상세 보기 (관리자)
    async function viewAdminInquiry(postId) {
        try {
            const resp = await fetch(`/api/inquiries/admin/${postId}`, { credentials: 'same-origin' });
            if (!resp.ok) return;
            const data = await resp.json();
            const item = data.post;
            const comments = data.comments || [];
            const hasAnswer = comments.length > 0;

            // 목록 숨기고 상세 보기
            document.getElementById('adminInquiryListSection').classList.add('d-none');
            document.querySelectorAll('.row.g-3.mb-4')[0].classList.add('d-none');
            document.querySelector('.bg-white.rounded-4.shadow-sm.p-3.mb-4').classList.add('d-none');
            document.getElementById('adminDetailSection').classList.remove('d-none');

            // 데이터 채우기
            const statusEl = document.getElementById('adminDetailStatus');
            statusEl.className = `admin-status-badge ${hasAnswer ? 'admin-status-answered' : 'admin-status-pending'}`;
            statusEl.innerHTML = hasAnswer
                ? '<i class="fas fa-check-circle"></i> 답변완료'
                : '<i class="fas fa-clock"></i> 대기중';

            document.getElementById('adminDetailTitle').textContent = item.title;
            document.getElementById('adminDetailAuthor').innerHTML =
                `<i class="far fa-user me-1"></i>${escapeHtml(item.author_name || item.author_id || '-')}`;
            document.getElementById('adminDetailDate').textContent = formatDate(item.create_dt);
            document.getElementById('adminDetailContent').textContent = item.content || '';
            document.getElementById('answerInquiryId').value = postId;

            // 기존 답변 (댓글)
            const existingSection = document.getElementById('existingAnswerSection');
            const formSection = document.getElementById('answerFormSection');

            if (hasAnswer) {
                existingSection.classList.remove('d-none');
                formSection.classList.add('d-none');
                // 최신 댓글 표시
                const latestComment = comments[comments.length - 1];
                document.getElementById('existingAnswer').textContent = latestComment.comment_text || '';
                document.getElementById('existingAnswerMeta').innerHTML =
                    `<i class="far fa-user me-1"></i>${escapeHtml(latestComment.author_name || '관리자')} · ${formatDate(latestComment.create_dt)}`;
            } else {
                existingSection.classList.add('d-none');
                formSection.classList.remove('d-none');
                document.getElementById('answerContent').value = '';
            }

        } catch (e) {
            console.error('관리자 문의 상세 조회 실패:', e);
        }
    }

    function showRewriteForm() {
        const existing = document.getElementById('existingAnswer').textContent;
        document.getElementById('answerContent').value = existing;
        document.getElementById('existingAnswerSection').classList.add('d-none');
        document.getElementById('answerFormSection').classList.remove('d-none');
        document.getElementById('answerContent').focus();
    }

    function adminBackToList() {
        document.getElementById('adminDetailSection').classList.add('d-none');
        document.getElementById('adminInquiryListSection').classList.remove('d-none');
        document.querySelectorAll('.row.g-3.mb-4')[0].classList.remove('d-none');
        document.querySelector('.bg-white.rounded-4.shadow-sm.p-3.mb-4').classList.remove('d-none');
        loadAdminInquiries(adminCurrentPage);
    }

    // 답변 폼
    function initAnswerForm() {
        const form = document.getElementById('answerForm');
        if (!form) return;

        form.addEventListener('submit', async function (e) {
            e.preventDefault();

            const inquiryId = document.getElementById('answerInquiryId').value;
            const answer = document.getElementById('answerContent').value.trim();

            if (!answer) {
                alert('답변 내용을 입력해주세요.');
                return;
            }

            const btn = document.getElementById('submitAnswerBtn');
            const spinner = document.getElementById('answerSpinner');
            const btnText = btn.querySelector('.btn-text');

            btn.disabled = true;
            spinner.classList.remove('d-none');
            btnText.classList.add('d-none');

            try {
                const resp = await fetch(`/api/inquiries/admin/${inquiryId}/answer`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'same-origin',
                    body: JSON.stringify({ answer }),
                });

                if (resp.ok) {
                    alert('답변이 등록되었습니다!');
                    viewAdminInquiry(inquiryId);
                } else {
                    const err = await resp.json();
                    alert(err.detail || '답변 등록에 실패했습니다.');
                }
            } catch (e) {
                alert('서버 연결에 실패했습니다.');
            } finally {
                btn.disabled = false;
                spinner.classList.add('d-none');
                btnText.classList.remove('d-none');
            }
        });
    }

    // 페이지네이션
    function renderAdminPagination(totalPages, currentPage) {
        const pagEl = document.getElementById('adminPagination');

        if (totalPages <= 1) {
            pagEl.innerHTML = '';
            return;
        }

        let html = '';
        html += `<li class="page-item ${currentPage <= 1 ? 'disabled' : ''}">
        <a class="page-link" href="#" onclick="loadAdminInquiries(${currentPage - 1}); return false;">‹</a>
    </li>`;

        for (let i = 1; i <= totalPages; i++) {
            html += `<li class="page-item ${i === currentPage ? 'active' : ''}">
            <a class="page-link" href="#" onclick="loadAdminInquiries(${i}); return false;">${i}</a>
        </li>`;
        }

        html += `<li class="page-item ${currentPage >= totalPages ? 'disabled' : ''}">
        <a class="page-link" href="#" onclick="loadAdminInquiries(${currentPage + 1}); return false;">›</a>
    </li>`;

        pagEl.innerHTML = html;
    }

    // 유틸
    function escapeHtml(str) {
        const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
        return str.replace(/[&<>"']/g, c => map[c]);
    }

    function formatDate(dtStr) {
        if (!dtStr) return '';
        const d = new Date(dtStr);
        const now = new Date();
        const diff = now - d;

        if (diff < 60000) return '방금 전';
        if (diff < 3600000) return `${Math.floor(diff / 60000)}분 전`;
        if (diff < 86400000) return `${Math.floor(diff / 3600000)}시간 전`;

        return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, '0')}.${String(d.getDate()).padStart(2, '0')}`;
    }