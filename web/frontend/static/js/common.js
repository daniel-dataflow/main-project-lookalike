/* =========================================================================
   [base.js]
========================================================================= */

document.write(new Date().getFullYear())
document.addEventListener('DOMContentLoaded', function () {
    const currentPath = window.location.pathname;
    const navLinks = document.querySelectorAll('.navbar-bottom .nav-link');

    navLinks.forEach(link => {
        const href = link.getAttribute('href');

        // 현재 경로와 일치하는 링크 찾기
        if (currentPath === href ||
            (currentPath === '/' && href === '/') ||
            (currentPath.startsWith('/recent') && href === '/recent') ||
            (currentPath.startsWith('/likes') && href === '/likes') ||
            (currentPath.startsWith('/mypage') && href === '/mypage')) {

            // 활성화 스타일 적용
            link.classList.remove('text-secondary');
            link.classList.add('text-dark', 'active');

            // 아이콘을 solid로 변경
            const icon = link.querySelector('i');
            if (icon) {
                icon.classList.remove('far');
                icon.classList.add('fas');
            }
        } else {
            // 비활성화 스타일
            link.classList.remove('text-dark', 'active');
            link.classList.add('text-secondary');

            // 아이콘을 regular로 변경
            const icon = link.querySelector('i');
            if (icon && !icon.classList.contains('fa-home')) {
                icon.classList.remove('fas');
                icon.classList.add('far');
            }
        }
    });
});


/* =========================================================================
   [script.js]
========================================================================= */

/**
 * Lookalike - 인증 및 세션 관리 JavaScript
 * 소셜 로그인(구글/네이버/카카오) + 이메일 로그인/회원가입
 * Redis 세션 기반 (httpOnly 쿠키)
 */

// ============================================
// 전역 상태
// ============================================
let currentUser = null;

// ============================================
// 초기화
// ============================================
document.addEventListener('DOMContentLoaded', function () {
    checkLoginStatus();
    checkOAuthProviders();
    initLoginForm();
    initSignupForm();
    initPasswordStrength();

    // URL에 에러 파라미터가 있으면 처리
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('error') === 'oauth_failed') {
        showToast('소셜 로그인에 실패했습니다. 다시 시도해주세요.', 'error');
        window.history.replaceState({}, document.title, window.location.pathname);
    }
});


// ============================================
// 로그인 상태 확인 (/api/auth/me)
// ============================================
async function checkLoginStatus() {
    try {
        const resp = await fetch('/api/auth/me', { credentials: 'same-origin' });
        const data = await resp.json();

        if (data.success && data.user) {
            currentUser = data.user;
            updateUIForLoggedIn(data.user);
        } else {
            currentUser = null;
            updateUIForLoggedOut();
        }
    } catch (e) {
        console.log('세션 확인 실패:', e);
        updateUIForLoggedOut();
    }
}


// ============================================
// OAuth 제공자 활성화 확인
// ============================================
async function checkOAuthProviders() {
    try {
        const resp = await fetch('/api/auth/oauth/providers');
        const data = await resp.json();

        const btnGoogle = document.getElementById('btnGoogle');
        const btnNaver = document.getElementById('btnNaver');
        const btnKakao = document.getElementById('btnKakao');

        if (btnGoogle && !data.google) {
            btnGoogle.classList.add('disabled');
            btnGoogle.removeAttribute('href');
            btnGoogle.title = 'Google 로그인이 설정되지 않았습니다';
        }
        if (btnNaver && !data.naver) {
            btnNaver.classList.add('disabled');
            btnNaver.removeAttribute('href');
            btnNaver.title = '네이버 로그인이 설정되지 않았습니다';
        }
        if (btnKakao && !data.kakao) {
            btnKakao.classList.add('disabled');
            btnKakao.removeAttribute('href');
            btnKakao.title = '카카오 로그인이 설정되지 않았습니다';
        }
    } catch (e) {
        console.log('OAuth 제공자 확인 실패:', e);
    }
}


// ============================================
// UI 업데이트: 로그인 상태
// ============================================
function updateUIForLoggedIn(user) {
    const authArea = document.getElementById('authArea');
    if (!authArea) return;

    const profileImg = user.profile_image ||
        `https://ui-avatars.com/api/?name=${encodeURIComponent(user.name || 'U')}&background=0d6efd&color=fff&size=64`;
    const providerIcon = getProviderIcon(user.provider);

    authArea.innerHTML = `
        <div class="dropdown">
            <button class="user-profile-btn dropdown-toggle" type="button" 
                    data-bs-toggle="dropdown" aria-expanded="false" id="userDropdownBtn">
                <img src="${profileImg}" alt="프로필" onerror="this.src='https://ui-avatars.com/api/?name=U&background=0d6efd&color=fff&size=64'">
                <span class="user-name">${providerIcon} ${user.name || '사용자'}</span>
            </button>
            <ul class="dropdown-menu dropdown-menu-end shadow border-0" style="border-radius: 10px;">
                <li class="px-3 py-2 border-bottom">
                    <div class="fw-bold small">${user.name || '사용자'}</div>
                    <div class="text-muted" style="font-size: 0.75rem;">${user.email || ''}</div>
                </li>
                <li><a class="dropdown-item py-2" href="/mypage"><i class="far fa-user me-2"></i>마이페이지</a></li>
                <li><hr class="dropdown-divider"></li>
                <li><a class="dropdown-item py-2 text-danger" href="#" onclick="handleLogout(event)">
                    <i class="fas fa-sign-out-alt me-2"></i>로그아웃</a></li>
            </ul>
        </div>
    `;
}

function updateUIForLoggedOut() {
    const authArea = document.getElementById('authArea');
    if (!authArea) return;

    authArea.innerHTML = `
        <button class="btn btn-dark btn-sm rounded-pill px-3" data-bs-toggle="modal"
            data-bs-target="#authModal" id="loginBtn">
            <i class="fas fa-sign-in-alt me-1"></i> 로그인
        </button>
    `;
}

function getProviderIcon(provider) {
    switch (provider) {
        case 'google': return '<i class="fab fa-google" style="font-size:0.7rem; color:#EA4335;"></i>';
        case 'naver': return '<span style="font-size:0.6rem; color:#03C75A; font-weight:900;">N</span>';
        case 'kakao': return '<span style="font-size:0.6rem; color:#FEE500; font-weight:900;">K</span>';
        default: return '';
    }
}


// ============================================
// 이메일 로그인 처리
// ============================================
function initLoginForm() {
    const form = document.getElementById('loginForm');
    if (!form) return;

    form.addEventListener('submit', async function (e) {
        e.preventDefault();
        clearErrors('login');

        const email = document.getElementById('loginEmail').value.trim();
        const password = document.getElementById('loginPassword').value;

        // 유효성 검사
        if (!email) return showFieldError('loginEmailError', '이메일을 입력해주세요');
        if (!isValidEmail(email)) return showFieldError('loginEmailError', '올바른 이메일 형식이 아닙니다');
        if (!password) return showFieldError('loginPasswordError', '비밀번호를 입력해주세요');

        setLoading('login', true);

        try {
            const resp = await fetch('/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({ email, password }),
            });

            const data = await resp.json();

            if (data.success) {
                showToast(`${data.user.name}님, 환영합니다!`, 'success');
                currentUser = data.user;
                updateUIForLoggedIn(data.user);
                closeModal();
                form.reset();
            } else {
                showFieldError('loginError', data.message || '로그인에 실패했습니다');
            }
        } catch (err) {
            showFieldError('loginError', '서버 연결에 실패했습니다');
        } finally {
            setLoading('login', false);
        }
    });
}


// ============================================
// 이메일 회원가입 처리
// ============================================
function initSignupForm() {
    const form = document.getElementById('signupForm');
    if (!form) return;

    form.addEventListener('submit', async function (e) {
        e.preventDefault();
        clearErrors('signup');

        const name = document.getElementById('signupName').value.trim();
        const email = document.getElementById('signupEmail').value.trim();
        const password = document.getElementById('signupPassword').value;
        const passwordConfirm = document.getElementById('signupPasswordConfirm').value;

        // 유효성 검사
        if (!name) return showFieldError('signupNameError', '이름을 입력해주세요');
        if (!email) return showFieldError('signupEmailError', '이메일을 입력해주세요');
        if (!isValidEmail(email)) return showFieldError('signupEmailError', '올바른 이메일 형식이 아닙니다');
        if (!password || password.length < 4) return showFieldError('signupPasswordError', '비밀번호는 4자리 이상이어야 합니다');
        if (password !== passwordConfirm) return showFieldError('signupPasswordConfirmError', '비밀번호가 일치하지 않습니다');

        setLoading('signup', true);

        try {
            const resp = await fetch('/api/auth/register', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({ name, email, password, password_confirm: passwordConfirm }),
            });

            const data = await resp.json();

            if (resp.status === 201 && data.success) {
                showToast(`${data.user.name}님, 회원가입을 축하합니다! 🎉`, 'success');
                currentUser = data.user;
                updateUIForLoggedIn(data.user);
                closeModal();
                form.reset();
            } else if (resp.status === 409) {
                showFieldError('signupEmailError', data.detail || '이미 사용 중인 이메일입니다');
            } else {
                showFieldError('signupError', data.detail || data.message || '회원가입에 실패했습니다');
            }
        } catch (err) {
            showFieldError('signupError', '서버 연결에 실패했습니다');
        } finally {
            setLoading('signup', false);
        }
    });
}


// ============================================
// 로그아웃 처리
// ============================================
async function handleLogout(e) {
    if (e) e.preventDefault();

    try {
        await fetch('/api/auth/logout', {
            method: 'POST',
            credentials: 'same-origin',
        });

        currentUser = null;
        updateUIForLoggedOut();
        showToast('로그아웃 되었습니다', 'info');
        window.location.href = "/";
    } catch (err) {
        showToast('로그아웃 처리 중 오류가 발생했습니다', 'error');
    }
}


// ============================================
// 비밀번호 강도 표시
// ============================================
function initPasswordStrength() {
    const passwordInput = document.getElementById('signupPassword');
    const strengthBar = document.getElementById('passwordStrength');
    if (!passwordInput || !strengthBar) return;

    passwordInput.addEventListener('input', function () {
        const val = this.value;
        strengthBar.className = 'password-strength';

        if (val.length === 0) {
            strengthBar.style.width = '0';
            return;
        }

        let score = 0;
        if (val.length >= 4) score++;
        if (val.length >= 8) score++;
        if (/[A-Z]/.test(val)) score++;
        if (/[0-9]/.test(val)) score++;
        if (/[^A-Za-z0-9]/.test(val)) score++;

        if (score <= 2) {
            strengthBar.classList.add('weak');
        } else if (score <= 3) {
            strengthBar.classList.add('medium');
        } else {
            strengthBar.classList.add('strong');
        }
    });
}


// ============================================
// 유틸리티 함수
// ============================================

/** 이메일 유효성 검사 */
function isValidEmail(email) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

/** 비밀번호 보기/숨기기 토글 */
function togglePassword(inputId, btn) {
    const input = document.getElementById(inputId);
    const icon = btn.querySelector('i');
    if (input.type === 'password') {
        input.type = 'text';
        icon.classList.replace('fa-eye', 'fa-eye-slash');
    } else {
        input.type = 'password';
        icon.classList.replace('fa-eye-slash', 'fa-eye');
    }
}

/** 필드별 에러 표시 */
function showFieldError(elementId, message) {
    const el = document.getElementById(elementId);
    if (el) {
        el.textContent = message;
        el.classList.add('show');
    }
}

/** 에러 초기화 */
function clearErrors(prefix) {
    document.querySelectorAll(`#${prefix}Form .form-error, #${prefix}Error`).forEach(el => {
        el.textContent = '';
        el.classList.remove('show');
    });
}

/** 로딩 상태 설정 */
function setLoading(type, isLoading) {
    const btn = document.getElementById(`${type}SubmitBtn`);
    const spinner = document.getElementById(`${type}Spinner`);
    const btnText = btn ? btn.querySelector('.btn-text') : null;

    if (btn) btn.disabled = isLoading;
    if (spinner) spinner.classList.toggle('d-none', !isLoading);
    if (btnText) btnText.classList.toggle('d-none', isLoading);
}

/** 모달 닫기 */
function closeModal() {
    const modal = bootstrap.Modal.getInstance(document.getElementById('authModal'));
    if (modal) modal.hide();
}

/** 토스트 알림 표시 */
function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast-custom toast-${type}`;
    toast.innerHTML = `
        <div class="d-flex align-items-center justify-content-between">
            <span>${getToastIcon(type)} ${message}</span>
            <button class="btn-close btn-close-white ms-2" style="font-size: 0.6rem;" 
                    onclick="this.parentElement.parentElement.remove()"></button>
        </div>
    `;
    container.appendChild(toast);

    // 3초 후 자동 제거
    setTimeout(() => {
        toast.style.animation = 'slideOut 0.3s ease forwards';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

function getToastIcon(type) {
    switch (type) {
        case 'success': return '<i class="fas fa-check-circle me-1"></i>';
        case 'error': return '<i class="fas fa-exclamation-circle me-1"></i>';
        case 'info': return '<i class="fas fa-info-circle me-1"></i>';
        default: return '';
    }
}


/* =========================================================================
   [likes.js]
========================================================================= */

document.addEventListener('DOMContentLoaded', async function () {
    const container = document.getElementById('productsContainer');
    const emptyState = document.getElementById('emptyState');
    const loading = document.getElementById('loading');

    try {
        const response = await fetch('/api/products/likes');
        const data = await response.json();

        if (loading) loading.classList.add('d-none');

        if (data.success && data.products && data.products.length > 0) {
            data.products.forEach(product => {
                const price = product.lowest_price ? product.lowest_price.toLocaleString() : '-';
                const card = `
                    <div class="col">
                        <a href="/product/${product.product_id}" class="text-decoration-none">
                            <div class="card h-100 border-0 shadow-sm hover-shadow">
                                <img src="${product.img_hdfs_path || 'https://placehold.co/300x400?text=No+Image'}" 
                                     class="card-img-top" alt="${product.prod_name}"
                                     style="height: 350px; object-fit: cover; object-position: top;">
                                <div class="card-body">
                                    <small class="text-muted">${product.brand_name || ''}</small>
                                    <h6 class="card-title text-truncate fw-bold">${product.prod_name}</h6>
                                    <div class="d-flex justify-content-between align-items-center">
                                        <span class="text-danger fw-bold">${price}원</span>
                                        <small class="text-muted">${product.mall_name || ''}</small>
                                    </div>
                                </div>
                            </div>
                        </a>
                    </div>
                `;
                if (container) container.innerHTML += card;
            });
        }
    } catch (error) {
        console.error('좋아요 목록 조회 실패:', error);
        if (loading) loading.classList.add('d-none');
        if (emptyState) emptyState.classList.remove('d-none');
    }
});

/* =========================================================================
   [mypage.js]
========================================================================= */

function togglePassword(inputId, btn) {
    const input = document.getElementById(inputId);
    const icon = btn.querySelector('i');

    if (!input || !icon) return;
    if (input.type === 'password') {
        input.type = 'text';
        icon.classList.remove('fa-eye-slash');
        icon.classList.add('fa-eye');
    } else {
        input.type = 'password';
        icon.classList.remove('fa-eye');
        icon.classList.add('fa-eye-slash');
    }
}

document.addEventListener('DOMContentLoaded', async function () {
    const modal = document.getElementById('editProfileModal');
    const form = document.getElementById('editProfileForm');
    const editLink = document.getElementById('editProfileLink');
    const passwordChangeItem = document.getElementById('passwordChangeItem');
    const withdrawLink = document.getElementById('withdrawLink');

    let originalUser = null;

    // 페이지 로드 시 사용자 정보 확인
    try {
        const resp = await fetch('/api/auth/me');
        const data = await resp.json();

        if (data.success && data.user) {
            originalUser = data.user;

            // 1. 소셜 로그인 유저 체크
            if (data.user.provider !== 'email') {
                // 소셜 유저는 비밀번호 변경 항목 자체를 숨김
                if (passwordChangeItem) passwordChangeItem.style.display = 'none';
            }
        }
    } catch (e) {
        console.error('사용자 정보 로드 실패:', e);
    }

    // 회원탈퇴 클릭
    if (withdrawLink) {
        withdrawLink.addEventListener('click', async function (e) {
            e.preventDefault();
            if (!confirm('정말로 탈퇴하시겠습니까? 탈퇴 후 복구할 수 없습니다.')) return;

            try {
                const resp = await fetch(`/api/auth/users/${originalUser.user_id}`, {
                    method: 'DELETE'
                });
                const result = await resp.json();

                if (resp.ok) {
                    alert('탈퇴 처리되었습니다. 이용해 주셔서 감사합니다.');
                    window.location.href = "/";
                } else {
                    alert(result.detail || '탈퇴 처리에 실패했습니다.');
                }
            } catch (err) {
                console.error(err);
                alert('서버 오류가 발생했습니다.');
            }
        });
    }

    // 저장 버튼 클릭
    if (form) {
        form.addEventListener('submit', async function (e) {
            e.preventDefault();

            const currentPassword = document.getElementById('currentPassword').value;
            const newPassword = document.getElementById('newPassword').value;
            const newPasswordConfirm = document.getElementById('newPasswordConfirm').value;

            // 유효성 검사
            if (!currentPassword) return alert('현재 비밀번호를 입력해주세요.');
            if (!newPassword || newPassword.length < 4) return alert('새 비밀번호는 4자리 이상이어야 합니다.');
            if (newPassword !== newPasswordConfirm) {
                const mismatchMsg = document.getElementById('passwordMismatch');
                if (mismatchMsg) mismatchMsg.classList.remove('d-none');
                return;
            } else {
                const mismatchMsg = document.getElementById('passwordMismatch');
                if (mismatchMsg) mismatchMsg.classList.add('d-none');
            }

            // 업데이트할 데이터 구성
            const updateData = {
                current_password: currentPassword,
                new_password: newPassword
            };

            // 로딩 표시
            const btn = document.getElementById('saveProfileBtn');
            if (!btn) return;
            const spinner = btn.querySelector('.spinner-border');
            const btnText = btn.querySelector('.btn-text');

            btn.disabled = true;
            if (spinner) spinner.classList.remove('d-none');
            if (btnText) btnText.classList.add('d-none');

            try {
                const resp = await fetch(`/api/auth/users/${originalUser.user_id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(updateData)
                });

                const result = await resp.json();

                if (resp.ok) {
                    alert('비밀번호가 변경되었습니다. 다시 로그인해주세요.');
                    window.location.href = "/"; // 로그아웃 처리 효과를 위해 홈으로 이동
                } else {
                    alert(result.detail || '비밀번호 변경에 실패했습니다.');
                }
            } catch (err) {
                console.error(err);
                alert('서버 오류가 발생했습니다.');
            } finally {
                btn.disabled = false;
                if (spinner) spinner.classList.add('d-none');
                if (btnText) btnText.classList.remove('d-none');
            }
        });
    }
});

/* =========================================================================
   [recent.js]
========================================================================= */

document.addEventListener('DOMContentLoaded', async function () {
    const container = document.getElementById('productsContainer');
    const emptyState = document.getElementById('emptyState');
    const loading = document.getElementById('loading');

    try {
        const response = await fetch('/api/products/recent-views');
        const data = await response.json();

        if (loading) loading.classList.add('d-none');

        if (data.success && data.products && data.products.length > 0) {
            data.products.forEach(product => {
                const price = product.lowest_price ? product.lowest_price.toLocaleString() : '-';
                const card = `
                    <div class="col">
                        <a href="/product/${product.product_id}" class="text-decoration-none">
                            <div class="card h-100 border-0 shadow-sm hover-shadow">
                                <img src="${product.img_hdfs_path || 'https://placehold.co/300x400?text=No+Image'}" 
                                     class="card-img-top" alt="${product.prod_name}"
                                     style="height: 350px; object-fit: cover; object-position: top;">
                                <div class="card-body">
                                    <small class="text-muted">${product.brand_name || ''}</small>
                                    <h6 class="card-title text-truncate fw-bold">${product.prod_name}</h6>
                                    <div class="d-flex justify-content-between align-items-center">
                                        <span class="text-danger fw-bold">${price}원</span>
                                        <small class="text-muted">${product.mall_name || ''}</small>
                                    </div>
                                </div>
                            </div>
                        </a>
                    </div>
                `;
                if (container) container.innerHTML += card;
            });
        }
    } catch (error) {
        console.error('최근 본 상품 조회 실패:', error);
        if (loading) loading.classList.add('d-none');
        if (emptyState) emptyState.classList.remove('d-none');
    }
});

/* =========================================================================
   [teams.js]
========================================================================= */

/* particles */
(function () {
    const colors = ['#d4522a', '#2563a8', '#2d8a5e', '#b07d2c', '#e8824a'];
    const wrap = document.getElementById('lkParticles');
    for (let i = 0; i < 18; i++) {
        const el = document.createElement('div');
        el.className = 'lk-particle';
        const s = Math.random() * 8 + 3;
        el.style.cssText = `width:${s}px;height:${s}px;left:${Math.random() * 100}%;background:${colors[i % colors.length]};--d:${Math.random() * 13 + 9}s;--dl:${Math.random() * 9}s;`;
        wrap.appendChild(el);
    }
})();

/* scroll reveal */
const io = new IntersectionObserver(en => { en.forEach(e => { if (e.isIntersecting) e.target.classList.add('on'); }); }, { threshold: .08 });
document.querySelectorAll('.rv,.rvl,.rvr').forEach(el => io.observe(el));

/* tab switching */
document.querySelectorAll('.pipe-tab').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.pipe-tab').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.pipe-panel').forEach(p => p.classList.add('hidden'));
        btn.classList.add('active');
        const panel = document.getElementById('tab-' + btn.dataset.tab);
        if (panel) panel.classList.remove('hidden');
    });
});

const so = new IntersectionObserver(en => {
    en.forEach(e => {
        if (e.isIntersecting) {
            e.target.querySelectorAll('.stat-num').forEach((n, i) => {
                setTimeout(() => {
                    n.style.transition = 'transform .38s cubic-bezier(.34,1.56,.64,1)';
                    n.style.transform = 'scale(1.16)';
                    setTimeout(() => { n.style.transform = 'scale(1)'; }, 380);
                }, i * 90);
            });
            so.unobserve(e.target);
        }
    });
}, { threshold: .5 });
document.querySelectorAll('.stat-strip').forEach(el => so.observe(el));