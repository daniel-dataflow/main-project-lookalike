# 모니터링 시스템 기술 스택 및 아키텍처

본 문서는 Lookalike 프로젝트의 어드민 인프라 모니터링 시스템에 사용된 기술 스택과 구현 방식을 설명합니다.

## 1. 아키텍처 개요

모니터링 시스템은 **Backend(데이터 수집 및 API)**와 **Frontend(시각화)**로 구성되어 있습니다.
시스템/컨테이너/DB 상태 정보를 수집하여 관리자가 직관적으로 파악할 수 있도록 대시보드 형태로 제공합니다.

## 2. Backend (데이터 수집 & API)

Python 기반의 **FastAPI** 서버가 시스템 및 서비스 상태를 실시간으로 수집하여 REST API로 제공합니다.

### 2.1. 주요 기술 라이브러리
| 구분 | 라이브러리 | 역할 및 설명 |
| :--- | :--- | :--- |
| **API 서버** | `FastAPI` | • 비동기 처리 지원을 통한 고성능 API 제공<br>• RESTful API 엔드포인트 구현 |
| **시스템 리소스** | `psutil` | • `CPU`, `Memory`, `Disk`, `Uptime` 등 호스트 OS의 시스템 메트릭 수집 |
| **컨테이너 관리** | `docker` SDK | • Docker 데몬(`unix:///var/run/docker.sock`)과 통신<br>• 실행 중인 컨테이너 목록, 상태(Up/Exited), 리소스(CPU/Mem) 조회<br>• *v7.1.0 이상 버전 사용 (호환성 개선)* |
| **DB 모니터링** | `psycopg2` | • PostgreSQL 활성 연결 수(Active Connections), 데이터베이스 용량 조회 |
| **DB 모니터링** | `pymongo` | • MongoDB 컬렉션 수, 데이터 스토리지 용량 조회 |
| **DB 모니터링** | `redis-py` | • Redis 메모리 사용량, 전체 키(Key) 개수 조회 (`INFO` 명령 활용) |

### 2.2. 데이터 수집 흐름
1.  **API 요청:** 프론트엔드에서 30초 주기로 상태 조회 API 호출 (`GET /api/admin/infra/status` 등)
2.  **실시간 조회:** 백엔드는 요청 시점에 즉시 `psutil`, `docker.from_env()`, DB 커넥션을 통해 현재 상태를 조회
3.  **응답:** 수집된 데이터를 JSON 형식으로 가공하여 반환

## 3. Frontend (시각화)

서버에서 제공하는 API 데이터를 사용자에게 직관적인 그래픽 인터페이스로 보여줍니다.

### 3.1. 주요 기술 요소
| 구분 | 기술/라이브러리 | 역할 및 설명 |
| :--- | :--- | :--- |
| **UI 프레임워크** | `Bootstrap 5` | • 반응형 그리드 시스템(Grid), 카드(Cards) UI, 배지(Badge) 활용<br>• 모바일/데스크탑 환경에 최적화된 레이아웃 제공 |
| **아이콘** | `FontAwesome` | • 각 서비스(Docker, DB, Server)를 직관적으로 표현하는 벡터 아이콘 사용 |
| **동적 데이터** | `JavaScript Fetch API` | • 비동기 데이터 통신 (`async/await`)<br>• `setInterval()`을 이용한 **30초 주기 자동 새로고침** 기능 구현 |
| **차트 시각화** | `Chart.js` | • (확장 기능) 시계열 데이터나 점유율 변화를 그래프로 시각화하는 기반 마련 |

### 3.2. 화면 구성
*   **시스템 리소스 패널:** CPU/메모리/디스크 사용률을 프로그레스(Progress) 바 형태로 시각화
*   **데이터베이스 상태 패널:** PostgreSQL, MongoDB, Redis의 연결 상태(Health) 및 주요 지표 표시
*   **Docker 컨테이너 리스트:** 실행 중인 모든 컨테이너의 상태(Running/Stopped)와 리소스 점유율 테이블 표시

## 4. 보안 및 접근 제어
*   **Admin 인증:** 어드민 페이지 접근 시 별도 인증 절차를 거쳐야 모니터링 데이터에 접근 가능
*   **Docker 소켓 권한:** 백엔드 컨테이너에 `/var/run/docker.sock`을 마운트하여 호스트 Docker 데몬에 안전하게 접근 (Read-only 권한 권장)
