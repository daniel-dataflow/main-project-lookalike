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
