let inquiryCurrentPage = 1;

document.addEventListener('DOMContentLoaded', function () {
    // script.js의 checkLoginStatus 완료 후 실행하기 위해 약간의 딜레이
    setTimeout(() => {
        initInquiryPage();
    }, 500);
});

/**
 * 페이지 진입 시 가장 먼저 호출되어 인증 쿠키 유효성을 /api/auth/me로 검증함.
 * 로그인 상태에 따라 '문의 내역 보기' 화면을 열거나, '로그인 필요' 단락을 노출시켜 접근 제어를 프론트엔드에서 분기함.
 * @returns {Promise<void>}
 */
async function initInquiryPage() {
    try {
        const resp = await fetch('/api/auth/me', { credentials: 'same-origin' });
        const data = await resp.json();

        if (data.success && data.user) {
            document.getElementById('requireLoginMsg').classList.add('d-none');
            document.getElementById('inquiryListSection').classList.remove('d-none');
            document.getElementById('writeInquiryBtn').classList.remove('d-none');
            loadInquiries();
        } else {
            document.getElementById('requireLoginMsg').classList.remove('d-none');
            document.getElementById('inquiryListSection').classList.add('d-none');
            document.getElementById('writeInquiryBtn').classList.add('d-none');
        }
    } catch (e) {
        document.getElementById('requireLoginMsg').classList.remove('d-none');
        document.getElementById('inquiryListSection').classList.add('d-none');
        document.getElementById('writeInquiryBtn').classList.add('d-none');
    }
}

/**
 * 현재 로그인한 사용자가 이전에 작성한 1:1 문의 게시글들을 페이지네이션 단위로 불러옴.
 * 상단에 답변 대기/완료 통계 배지를 동기화하여 유저가 자신의 CS 현황을 직관적으로 파악할 수 있게 도움.
 * @param {number} page 오프셋 기반 페이지 번호 (기본값 1)
 */
async function loadInquiries(page = 1) {
    inquiryCurrentPage = page;
    try {
        const resp = await fetch(`/api/inquiries?page=${page}&page_size=5`, { credentials: 'same-origin' });
        if (resp.status === 401) {
            document.getElementById('requireLoginMsg').classList.remove('d-none');
            document.getElementById('inquiryListSection').classList.add('d-none');
            return;
        }
        const data = await resp.json();

        const listEl = document.getElementById('inquiryList');
        const emptyEl = document.getElementById('emptyState');

        // 카운트 업데이트
        const total = data.total || 0;
        document.getElementById('totalCount').textContent = total;

        // comment_count 기반 상태 계산
        let allPending = 0, allAnswered = 0;
        if (data.items) {
            data.items.forEach(i => {
                if ((i.comment_count || 0) > 0) allAnswered++;
                else allPending++;
            });
        }
        document.getElementById('pendingCount').textContent = allPending;
        document.getElementById('answeredCount').textContent = allAnswered;

        if (!data.items || data.items.length === 0) {
            listEl.innerHTML = '';
            emptyEl.classList.remove('d-none');
            document.getElementById('paginationNav').classList.add('d-none');
            return;
        }

        emptyEl.classList.add('d-none');
        listEl.innerHTML = data.items.map(item => {
            const hasAnswer = (item.comment_count || 0) > 0;
            return `
            <div class="inquiry-item" onclick="viewInquiry(${item.inquiry_board_id})">
                <div class="d-flex justify-content-between align-items-start mb-2">
                    <h6 class="fw-bold mb-0" style="flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                        ${escapeHtml(item.title)}
                    </h6>
                    <span class="status-badge ${hasAnswer ? 'status-answered' : 'status-pending'} ms-2">
                        ${hasAnswer
                    ? '<i class="fas fa-check-circle"></i> 답변완료'
                    : '<i class="fas fa-clock"></i> 대기중'}
                    </span>
                </div>
                <p class="text-muted mb-2" style="font-size: 0.85rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                    ${escapeHtml(item.content || '')}
                </p>
                <div class="d-flex justify-content-between align-items-center">
                    <small class="text-muted">
                        <i class="far fa-clock me-1"></i>${formatDate(item.create_dt)}
                    </small>
                    <small class="text-muted">
                        <i class="far fa-eye me-1"></i>${item.view_count || 0}
                    </small>
                </div>
            </div>
            `;
        }).join('');

        // 페이지네이션
        renderPagination(data.total_pages, page);

    } catch (e) {
        console.error('문의 목록 로드 실패:', e);
    }
}

/**
 * 목록에서 특정 문의 항목을 클릭했을 때 트리거됨.
 * API로부터 원글(Post)과 하위 답변(Comments) 묶음을 받아온 뒤, DOM을 리스트 모드에서 상세 모드로 전환(Toggle)함.
 * 관리자가 남긴 답변을 확인하여 추가 응대가 이뤄졌는지 유저에게 고지하기 목적임.
 * @param {number} postId 조회할 문의글 식별자 ID
 * @returns {Promise<void>}
 */
async function viewInquiry(postId) {
    try {
        const resp = await fetch(`/api/inquiries/${postId}`, { credentials: 'same-origin' });
        if (!resp.ok) {
            if (typeof showToast === 'function') showToast('문의글을 불러올 수 없습니다.', 'error');
            return;
        }
        const data = await resp.json();
        const item = data.post;
        const comments = data.comments || [];
        const hasAnswer = comments.length > 0;

        document.getElementById('inquiryListSection').classList.add('d-none');
        document.getElementById('inquiryDetailSection').classList.remove('d-none');
        document.getElementById('writeInquiryBtn').classList.add('d-none');

        const card = document.getElementById('inquiryDetailCard');
        card.innerHTML = `
            <div class="detail-header">
                <div class="d-flex justify-content-between align-items-start mb-2">
                    <span class="status-badge ${hasAnswer ? 'status-answered' : 'status-pending'}">
                        ${hasAnswer
                ? '<i class="fas fa-check-circle"></i> 답변완료'
                : '<i class="fas fa-clock"></i> 대기중'}
                    </span>
                    ${!hasAnswer ? `
                    <div class="dropdown">
                        <button class="btn btn-sm btn-outline-light rounded-pill" data-bs-toggle="dropdown">
                            <i class="fas fa-ellipsis-v"></i>
                        </button>
                        <ul class="dropdown-menu dropdown-menu-end shadow border-0">
                            <li><a class="dropdown-item" href="#" onclick="openEditModal(${item.inquiry_board_id}, '${escapeHtml(item.title).replace(/'/g, "\\'")}', \`${escapeHtml(item.content || '').replace(/`/g, "\\`")}\`); return false;">
                                <i class="fas fa-edit me-2"></i>수정
                            </a></li>
                            <li><a class="dropdown-item text-danger" href="#" onclick="deleteInquiry(${item.inquiry_board_id}); return false;">
                                <i class="fas fa-trash me-2"></i>삭제
                            </a></li>
                        </ul>
                    </div>
                    ` : ''}
                </div>
                <h5 class="fw-bold mb-2">${escapeHtml(item.title)}</h5>
                <div class="d-flex gap-3" style="font-size: 0.8rem; opacity: 0.8;">
                    <span><i class="far fa-clock me-1"></i>${formatDate(item.create_dt)}</span>
                    <span><i class="far fa-eye me-1"></i>${item.view_count || 0}</span>
                </div>
            </div>
            <div class="detail-body">
                <div class="detail-content">${escapeHtml(item.content || '')}</div>
                ${hasAnswer ? comments.map(c => `
                <div class="answer-section">
                    <div class="d-flex align-items-center mb-3">
                        <div class="bg-success rounded-circle d-flex align-items-center justify-content-center me-2"
                            style="width: 32px; height: 32px;">
                            <i class="fas fa-headset text-white" style="font-size: 0.8rem;"></i>
                        </div>
                        <div>
                            <div class="fw-bold" style="font-size: 0.9rem;">관리자 답변</div>
                            <div class="text-muted" style="font-size: 0.75rem;">
                                ${escapeHtml(c.author_name || '관리자')} · ${formatDate(c.create_dt)}
                            </div>
                        </div>
                    </div>
                    <div class="answer-content">${escapeHtml(c.comment_text || '')}</div>
                </div>
                `).join('') : `
                <div class="text-center py-4 mt-3" style="opacity: 0.5;">
                    <i class="fas fa-hourglass-half fa-2x mb-2"></i>
                    <p class="mb-0" style="font-size: 0.85rem;">관리자 답변을 기다리고 있습니다...</p>
                </div>
                `}
            </div>
        `;

    } catch (e) {
        console.error('문의 상세 조회 실패:', e);
    }
}

function backToList() {
    document.getElementById('inquiryDetailSection').classList.add('d-none');
    document.getElementById('inquiryListSection').classList.remove('d-none');
    document.getElementById('writeInquiryBtn').classList.remove('d-none');
    loadInquiries(inquiryCurrentPage);
}

/**
 * 1:1 문의글 작성 HTML 폼 구조를 활성화하고 입력 필드로 포커스를 이동시킴.
 * 숨김/노출 토글을 통해 SPA(Single Page Application)와 같은 부드러운 화면 전환을 유도.
 */
function showWriteForm() {
    document.getElementById('writeFormCard').classList.remove('d-none');
    document.getElementById('writeInquiryBtn').classList.add('d-none');
    document.getElementById('inquiryTitle').focus();
}

function hideWriteForm() {
    document.getElementById('writeFormCard').classList.add('d-none');
    document.getElementById('writeInquiryBtn').classList.remove('d-none');
    document.getElementById('inquiryForm').reset();
}

document.addEventListener('DOMContentLoaded', function () {
    const form = document.getElementById('inquiryForm');
    if (form) {
        form.addEventListener('submit', async function (e) {
            e.preventDefault();

            const title = document.getElementById('inquiryTitle').value.trim();
            const content = document.getElementById('inquiryContent').value.trim();

            if (!title) {
                if (typeof showToast === 'function') showToast('제목을 입력해주세요.', 'error');
                return;
            }
            if (!content) {
                if (typeof showToast === 'function') showToast('내용을 입력해주세요.', 'error');
                return;
            }

            const btn = document.getElementById('submitInquiryBtn');
            const spinner = document.getElementById('inquirySpinner');
            const btnText = btn.querySelector('.btn-text');

            btn.disabled = true;
            spinner.classList.remove('d-none');
            btnText.classList.add('d-none');

            try {
                const resp = await fetch('/api/inquiries', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'same-origin',
                    body: JSON.stringify({ title, content }),
                });

                if (resp.ok) {
                    if (typeof showToast === 'function') showToast('문의가 등록되었습니다!', 'success');
                    hideWriteForm();
                    loadInquiries(1);
                } else {
                    const err = await resp.json();
                    if (typeof showToast === 'function') showToast(err.detail || '등록에 실패했습니다.', 'error');
                }
            } catch (e) {
                if (typeof showToast === 'function') showToast('서버 연결에 실패했습니다.', 'error');
            } finally {
                btn.disabled = false;
                spinner.classList.add('d-none');
                btnText.classList.remove('d-none');
            }
        });
    }
});

/**
 * 상세 뷰 내에서 '수정' 버튼 클릭 시 기입력된 제목/내용을 모달로 캐싱해 띄움.
 * 오타나 덧붙일 내용이 있을 때 사용자 편의를 높이기 위해 In-place 편집을 유도.
 * @param {number} id 원글 PK
 * @param {string} title 기존 제목
 * @param {string} content 기존 내용
 */
function openEditModal(id, title, content) {
    document.getElementById('editInquiryId').value = id;
    document.getElementById('editTitle').value = title;
    document.getElementById('editContent').value = content;
    new bootstrap.Modal(document.getElementById('editModal')).show();
}

document.addEventListener('DOMContentLoaded', function () {
    const editForm = document.getElementById('editForm');
    if (editForm) {
        editForm.addEventListener('submit', async function (e) {
            e.preventDefault();
            const id = document.getElementById('editInquiryId').value;
            const title = document.getElementById('editTitle').value.trim();
            const content = document.getElementById('editContent').value.trim();

            if (!title || !content) {
                if (typeof showToast === 'function') showToast('제목과 내용을 모두 입력해주세요.', 'error');
                return;
            }

            try {
                const resp = await fetch(`/api/inquiries/${id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'same-origin',
                    body: JSON.stringify({ title, content }),
                });

                if (resp.ok) {
                    if (typeof showToast === 'function') showToast('문의가 수정되었습니다.', 'success');
                    bootstrap.Modal.getInstance(document.getElementById('editModal')).hide();
                    viewInquiry(id);
                } else {
                    const err = await resp.json();
                    if (typeof showToast === 'function') showToast(err.detail || '수정에 실패했습니다.', 'error');
                }
            } catch (e) {
                if (typeof showToast === 'function') showToast('서버 연결에 실패했습니다.', 'error');
            }
        });
    }
});

/**
 * 등록된 문의글에 대한 영구 삭제를 서버에 요청.
 * 휴지통이나 임시 저장 개념 없이 (DB CASCADE 롤백) 즉시 제거 처리되므로 window.confirm의 방어 로직을 둠.
 * @param {number} id 삭제 타겟 식별자
 */
async function deleteInquiry(id) {
    if (!confirm('정말 삭제하시겠습니까?')) return;

    try {
        const resp = await fetch(`/api/inquiries/${id}`, {
            method: 'DELETE',
            credentials: 'same-origin',
        });

        if (resp.ok || resp.status === 204) {
            if (typeof showToast === 'function') showToast('문의가 삭제되었습니다.', 'success');
            backToList();
        } else {
            const err = await resp.json();
            if (typeof showToast === 'function') showToast(err.detail || '삭제에 실패했습니다.', 'error');
        }
    } catch (e) {
        if (typeof showToast === 'function') showToast('서버 연결에 실패했습니다.', 'error');
    }
}

// 페이지네이션
function renderPagination(totalPages, currentPage) {
    const navEl = document.getElementById('paginationNav');
    const pagEl = document.getElementById('pagination');

    if (totalPages <= 1) {
        navEl.classList.add('d-none');
        return;
    }

    navEl.classList.remove('d-none');
    let html = '';

    html += `<li class="page-item ${currentPage <= 1 ? 'disabled' : ''}">
        <a class="page-link" href="#" onclick="loadInquiries(${currentPage - 1}); return false;">‹</a>
    </li>`;

    for (let i = 1; i <= totalPages; i++) {
        html += `<li class="page-item ${i === currentPage ? 'active' : ''}">
            <a class="page-link" href="#" onclick="loadInquiries(${i}); return false;">${i}</a>
        </li>`;
    }

    html += `<li class="page-item ${currentPage >= totalPages ? 'disabled' : ''}">
        <a class="page-link" href="#" onclick="loadInquiries(${currentPage + 1}); return false;">›</a>
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
    if (diff < 604800000) return `${Math.floor(diff / 86400000)}일 전`;

    return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, '0')}.${String(d.getDate()).padStart(2, '0')}`;
}