# 프론트엔드 (UI) 아키텍처 및 에셋(Asset) 관리 명세서

> **문서 목적**: FastAPI Jinja2 템플릿 기반으로 렌더링되는 Lookalike 웹 서비스의 프론트엔드 환경 및 CSS/JS 에셋 관리 체계를 명세함. 유지보수 단계에서 템플릿 상속 구조와 정적 파일 배치 규칙을 일관되게 적용하기 위해 작성됨.

---

## 1. 프론트엔드 아키텍처 개요

Lookalike 웹 클라이언트는 SPA(Single Page Application) 대신 **SSR(Server-Side Rendering)** 방식의 뼈대를 따르며, FastAPI의 `Jinja2Templates`를 통해 HTML 템플릿을 서빙하고 사용자 인터랙션이 동적으로 필요한 부분들만 AJAX/Fetch API로 비동기 통신을 수행하는 하이브리드 MPA(Multi-Page Application) 구조를 갖는다.

### 1-1. 서비스 영역의 분리
웹 페이지의 목적에 따라 **[사용자 서비스 영역]**과 **[관리자 서비스 영역]**으로 완전히 이원화하여 렌더링 및 에셋을 적용한다.
- 서로간의 스타일과 코드가 충돌(Conflict)하는 것을 원천 차단.
- 관리자 페이지 렌더링 시 무거운 사용자 라이브러리 로드를 제거하여 성능을 최적화.

---

## 2. 템플릿 상속 구조 (Jinja2)

페이지별 중복되는 `<head>` 선언부, 네비게이션(GNB), 푸터(Footer), 인증 모달, 공통 `<script>` 로드를 방지하기 위해 최상위 부모 템플릿을 정의하고 자식 템플릿이 이를 `{% extends %}` 확장하는 구조를 가진다.

### 2-1. 사용자 서비스 템플릿 계층도
`base.html`을 최상위 부모로 하며, 일반 사용자가 접근하는 모든 화면이 이를 상속받는다.

```text
web/frontend/templates/
├── base.html                 (User 공통 레이아웃 뼈대)
│   ├── index.html            (메인 홈 및 이미지/텍스트 객체 탐지 검색)
│   ├── inquiry.html          (고객 문의 게시판)
│   ├── search_history.html   (나의 검색 내역 조회)
│   ├── product_detail.html   (개별 상품 상세보기)
│   ├── mypage.html           (마이페이지)
│   ├── likes.html            (관심 상품/좋아요 목록)
│   ├── recent.html           (모바일 최적화 최근 본 상품)
│   ├── recent_viewed.html    (데스크탑/공통 최근 본 상품)
│   ├── team.html/teams.html  (팀 소개 및 인사말 페이지)
│   ├── privacy.html          (개인정보처리방침)
│   ├── terms.html            (이용약관)
│   ├── error.html            (공통 에러 표시 화면)
│   └── search_results.html   (검색 결과 조각 뷰 파편)
```

### 2-2. 관리자 서비스 템플릿 계층도
`admin_base.html`을 최상위 부모로 설정하여, 우측 메인 컨텐츠 영역만 페이지 단위로 교체되는 좌측 고정 사이드바(Sidebar) Layout을 제공한다.

```text
web/frontend/templates/
├── admin_base.html           (Admin 공통 레이아웃 뼈대, 사이드바 포함)
│   ├── admin_infra.html      (인프라 모니터링: 서버 하드웨어 상태)
│   ├── admin_logs.html       (로그 모니터링: 컨테이너 실시간 로그 스트리밍)
│   ├── admin_inquiry.html    (어드민용 고객 문의 응대 및 관리)
│   ├── admin_dashboard.html  (관리자 통합 대시보드 화면)
│   └── admin_batch.html      (배치 작업/DAG 모니터링 상태)
└── admin_login.html          (※ 관리자 화면 로그인 폼: 템플릿 상속 제외 단독 화먼용)
```

---

## 3. 정적 에셋 (CSS / JS) 관리 규칙

인라인 기반으로 HTML 내부에 혼재되어 있던 코드를 유지보수성과 브라우저 캐싱(Caching) 성능 향상을 위해 완전히 모듈화하여 분리하였다. 

**분리/통합 대원칙 (Hybrid CSS & JS)**:
1. **CSS(스타일)**: 용량이 크지 않고 페이지 이동 간 깜빡임을 최소화하기 위해 '전체 공통 파일'로 최대한 병합한다. (병렬 로드 최소화)
2. **JS(스크립트)**: 브라우저 파싱 부하를 줄이기 위해, "모든 페이지에 공통 상주하는 가벼운 이벤트 로직"과 "특정 페이지에서만 방대한 자원을 소모하는 무거운 로직"을 분리하여 임포트(Import)한다.

### 3-1. CSS 병합 구조 (디렉토리: `web/frontend/static/css/`)
| 파일명 | 용도 및 범위 | 상속 연결점 |
|---|---|---|
| **`style.css`** | 사용자 서비스에 필요한 전체 스타일 병합 (기존 teams, recent, inquiry 등의 파편화 된 css 전부 흡수) | `base.html` `<head>` 영역 |
| **`admin_style.css`** | 관리자 전용 대시보드 및 서비스 전체 스타일 병합 (기존 admin_logs, admin_inquiry 등의 파편 흡수) | `admin_base.html` `<head>` 영역 |

### 3-2. JS 이원화 구조 (디렉토리: `web/frontend/static/js/`)
**[1] 공통 스크립트 (병합)**
| 파일명 | 용도 및 범위 | 상속 연결점 |
|---|---|---|
| **`common.js`** | 네비게이션 제어, 소셜 로그인 모달, 좋아요/최근본상품 등 사용자 전역 이벤트 처리 (기존 base, script, mypage 등 파편 흡수) | `base.html` 하단 |
| **`admin_common.js`** | 사이드바 렌더링 컨트롤, Admin 권한 검증, 프리로드/프리페치 로직 (기존 admin_base 파편 흡수) | `admin_base.html` 상/하단 |

**[2] 기능별 독립 스크립트 (페이지 한정)**
> 페이지에 접속했을 때만 불러오는 고립된 로직(Isolation Logic)으로, 다른 파일과의 변수명/ID 충돌(Conflict)이 허용된다.

| 파일명 | 연결 페이지 | 주요 기능 |
|---|---|---|
| **`index.js`** | `index.html` | YOLO 객체 탐지 API 호출, 캔버스(Canvas) 드래그 기반 BBox Area Crop, 비동기 검색 요청 |
| **`inquiry.js`** | `inquiry.html` | 문의 글쓰기, 비동기 페이지네이션 처리 등 게시판 전용 컨트롤 |
| **`search_history.js`** | `search_history.html` | 과거 검색 이력 페이지네이션 및 캐싱, 히스토리 삭제 파이프라인 |
| **`admin_logs.js`** | `admin_logs.html` | SSE(Server-Sent Events)를 이용한 대규모 버퍼 로그 실시간 스트리밍 렌더링 및 자동 스크롤 |
| **`admin_infra.js`** | `admin_infra.html` | Chart.js 렌더링, 주기적 폴링(Polling)을 통한 리소스 자원 비동기 상태 갱신 |
| **`admin_inquiry.js`** | `admin_inquiry.html` | 접수된 문의글 답변 등록(Reply) 및 어드민 전담 삭제 권한 처리 |

---

## 4. 정적 파일 개발 가이드라인 (Developer Note)
- HTML 구조를 변경할 때는 `web/frontend/templates/` 디렉토리 파일들을 다루되, JS에 정의된 DOM의 `id`나 `class` 셀렉터가 누락되지 않았는지 주의한다.
- 디자인 및 색상을 수정할 때는 페이지와 관계없이 **반드시 `style.css` (또는 어드민은 `admin_style.css`) 단일 파일을 열고 수정**하여 전체 시스템이 영향을 받도록 통제한다.
- 새로운 페이지를 추가할 경우, `base.html`을 `{% extends %}` 하고, 페이지 전용 복잡한 연산 JS가 들어갈 경우에만 `static/js/` 위치에 새로운 파일을 생성한 후 `{% block extra_js %}` 블록에서 임포트시킨다.
