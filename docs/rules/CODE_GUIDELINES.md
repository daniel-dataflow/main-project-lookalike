# 팀 코딩 및 문서화 컨벤션 가이드 (Web 부분)

본 문서는 `web/` 디렉토리(Backend Python, Frontend JavaScript) 작업 시 유지보수성과 코드 일관성을 확보하기 위한 작성 기준을 정의함. 모든 신규 작성 및 리팩토링 시 본 가이드를 준수할 것.

---

## 1. 공통 원칙

*   **의도 중심 주석 (Why > What)**
    *   코드가 '어떻게(How)' 동작하는지는 변수명과 함수명으로 자체 설명(Self-documenting)되어야 함.
    *   주석은 해당 비즈니스 로직을 '왜(Why)' 그렇게 구현했는지, 기획 상의 예외 처리, 특정 우회 기법(Workaround) 도입 이유 등을 설명하는 데 집중할 것.
*   **어조 및 형식**
    *   불필요한 감정적 표현, 장황한 서술형 문장 지양.
    *   명사형 종결, 개조식 작성 등 명확하고 간결한 문체 사용.
*   **구조화**
    *   복잡한 로직은 단계별로 주석 번호를 매겨 흐름을 파악하기 쉽게 배치.

---

## 2. Python (Backend) 가이드

*   **패러다임**: 
    *   API Router(`routers/`): FastAPI 표준에 맞춰 전역 함수 형태로 엔드포인트 구성.
    *   Business Logic(`services/`): 상태 관리 및 의존성 주입의 용이성을 위해 **클래스(Class) 기반**으로 모듈화.
*   **타입 힌팅 (Type Hinting)**: 모든 함수의 매개변수와 반환값에 타입 힌트 필수 지정.
*   **Docstring 포맷**: **Google Style** 준수.

### 모듈 구조 및 예시

```python
"""
상품 검색 관련 비즈니스 로직 처리 모듈.
- ML 엔진 API 호출 및 ES Vector 검색 전략 통합 관리.
"""
from typing import Optional, List

class SearchService:
    """
    유사 상품 검색 서비스.
    ML 엔진의 예측 결과(유사도 점수)와 내부 DB를 결합(Hydration)하여 최종 응답 반환.
    """

    def search_products(self, query: str, limit: int = 6) -> List[dict]:
        """
        텍스트 기반 상품 검색 실행.

        Args:
            query (str): 사용자 검색어. 빈 문자열일 경우 전체 조회(또는 예외 처리).
            limit (int, optional): 최대 반환 항목 수. 기본값 6.

        Returns:
            List[dict]: 검색된 상품 상세 정보 목록. 결과가 없으면 빈 리스트 반환.

        Raises:
            ValueError: 필수 파라미터가 누락된 경우 발생.
        """
        pass
```

---

## 3. JavaScript (Frontend) 가이드

*   **패러다임**: ES6+ 문법 사용. 무분별한 전역 변수 지양, 이벤트 위임(Event Delegation) 적극 활용.
*   **Docstring 포맷**: **JSDoc** 표준 준수. (IDE의 타입 추론 및 자동완성 지원 목적)

### 함수 작성 예시

```javascript
/**
 * YOLO 서버에 사용자가 업로드한 이미지를 전송하고 객체 바운딩 박스를 반환.
 * 
 * @param {File} imageFile - 업로드 폼에서 추출한 이미지 파일 객체
 * @returns {Promise<Array>} बा운딩 박스 객체 배열. [{x1: number, y1: number, x2: number, y2: number, label: string}]
 * @throws {Error} 네트워크 통신 실패 또는 API 에러 응답 시
 */
async function fetchBboxFromImage(imageFile) {
    // 1. FormData 생성 및 이미지 마운트
    const formData = new FormData();
    formData.append('image', imageFile);

    // 2. API 전송 및 응답 처리
    const response = await fetch('/api/search/detect', {
        method: 'POST',
        body: formData
    });
    
    // ...
}
```

---

## 4. 리팩토링 체크리스트

코드를 수정하거나 리뷰할 때 다음 사항을 점검:
1. 클래스/함수 상단에 Docstring이 명확하게 작성되었는가?
2. 매개변수와 반환값의 타입이 명시(힌팅/JSDoc)되었는가?
3. 비즈니스 로직 상의 특이사항이나 제한조건이 주석으로 잘 드러나 있는가?
4. 무의미한 주석(예: `i++ // i를 1 증가시킴`)은 제거되었는가?
