document.addEventListener('DOMContentLoaded', function () {
    // URL 파라미터 확인 - 로그인 필요 메시지
    const urlParams = new URLSearchParams(window.location.search);
    const error = urlParams.get('error');

    if (error === 'login_required') {
        alert('로그인이 필요한 페이지입니다. 로그인 후 이용해주세요.');
        // URL에서 파라미터 제거
        window.history.replaceState({}, document.title, window.location.pathname);
    }

    const uploadArea = document.getElementById('uploadArea');
    const fileInput = document.getElementById('imageUpload');
    const placeholder = document.getElementById('uploadPlaceholder');
    const preview = document.getElementById('uploadPreview');
    const previewImage = document.getElementById('previewImage');
    const previewFileName = document.getElementById('previewFileName');
    const removeImageBtn = document.getElementById('removeImageBtn');
    const searchBtn = document.getElementById('searchBtn');
    const searchInput = document.getElementById('searchInput');
    const searchLoading = document.getElementById('searchLoading');
    const searchResults = document.getElementById('searchResults');
    const resultsContainer = document.getElementById('resultsContainer');

    let selectedFile = null;
    let selectedGender = null;
    let selectedClothes = null;
    let selectedBbox = null;    // 현재 선택된 박스 좌표
    let hasBboxes = false;      // 자동 탐지에서 박스가 나왔는지
    let currentMode = 'auto';   // 'auto' or 'manual'
    let originalImageSize = { w: 0, h: 0 };
    let originalImageObj = null;

    // ==========================================
    // 자동 탐지(YOLO) 시나리오용 헬퍼 함수
    // ==========================================

    /**
     * YOLO 서버가 뱉는 라벨('top', '하의' 등)을 클라이언트의 검색 필터 기준인 Category 상수값으로 정규화함.
     * 자연어 모델 결과를 파서블한 DB Enum 필드로 맞춰주기 위한 전처리 과정임.
     * @param {string} label 비전 모델 추론 결과 클래스명
     * @returns {string|null} 매핑된 정규 분류값('top', 'bottom', 'outer')
     */
    function mapYoloLabelToCategory(label) {
        const normalized = String(label || '').trim().toLowerCase();
        if (!normalized) return null;
        if (normalized === 'top' || normalized === '상의') return 'top';
        if (normalized === 'bottom' || normalized === '하의') return 'bottom';
        if (normalized === 'outer' || normalized === '아우터') return 'outer';
        return null;
    }

    /**
     * 사용자가 직접 Category 버튼을 클릭하거나, 
     * 자동 탐지된 바운딩 박스를 클릭했을 때 활성화된 카테고리를 동기화함.
     * UI 버튼 상태(active)와 전역 상태(selectedClothes)를 일치시켜 직관적인 검색 조건을 유지.
     * @param {string} categoryValue 버튼 클릭 또는 매핑으로부터 주입되는 분류값
     */
    function applyCategorySelection(categoryValue) {
        if (!categoryValue) return;
        selectedClothes = categoryValue;
        categoryBtns.forEach(b => {
            const isTarget = b.dataset.category === categoryValue;
            b.classList.toggle('active', isTarget);
            b.classList.toggle('btn-primary', isTarget);
            b.classList.toggle('btn-outline-secondary', !isTarget);
        });
        document.getElementById('categoryError').classList.add('d-none');
        console.log('[YOLO->CATEGORY] applyCategorySelection:', {
            categoryValue,
            selectedClothes
        });
    }

    // 성별 선택
    const genderBtns = document.querySelectorAll('.gender-btn');
    genderBtns.forEach(btn => {
        btn.addEventListener('click', function () {
            if (selectedGender === this.dataset.gender) {
                // 이미 선택된 버튼 클릭 시 해제
                this.classList.remove('active', 'btn-primary');
                this.classList.add('btn-outline-secondary');
                selectedGender = null;
            } else {
                // 다른 버튼 선택 시
                genderBtns.forEach(b => {
                    b.classList.remove('active', 'btn-primary');
                    b.classList.add('btn-outline-secondary');
                });
                this.classList.add('active', 'btn-primary');
                this.classList.remove('btn-outline-secondary');
                selectedGender = this.dataset.gender;
            }
            document.getElementById('categoryError').classList.add('d-none');
        });
    });

    // 옷 종류 선택
    const categoryBtns = document.querySelectorAll('.category-btn');
    categoryBtns.forEach(btn => {
        btn.addEventListener('click', function () {
            if (selectedClothes === this.dataset.category) {
                // 이미 선택된 버튼 클릭 시 해제
                this.classList.remove('active', 'btn-primary');
                this.classList.add('btn-outline-secondary');
                selectedClothes = null;
            } else {
                // 다른 버튼 선택 시
                categoryBtns.forEach(b => {
                    b.classList.remove('active', 'btn-primary');
                    b.classList.add('btn-outline-secondary');
                });
                this.classList.add('active', 'btn-primary');
                this.classList.remove('btn-outline-secondary');
                selectedClothes = this.dataset.category;
            }
            document.getElementById('categoryError').classList.add('d-none');
        });
    });

    // 검색어 입력창에서 엔터키 누르면 검색
    searchInput.addEventListener('keypress', function (e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            searchBtn.click();
        }
    });

    // 업로드 영역 클릭 (이미지가 없을 때만 파일 선택기 표시)
    uploadArea.addEventListener('click', function (e) {
        // 수동 모드로 드래그 중이거나 드래그가 끝난 직후면 무시
        if (currentMode === 'manual' && e.target.closest('#manualBboxContainer')) {
            return;
        }

        // 이미지가 이미 있고 (미리보기 화면 활성화), 클릭한 대상이 박스 지정/삭제 요소가 아니더라도 
        // 이미지 자체나 내부를 누른 거면 무시하게 만들기
        if (selectedFile && !e.target.closest('#uploadPlaceholder')) {
            return;
        }

        fileInput.click();
    });

    // 드래그 앤 드롭
    uploadArea.addEventListener('dragover', function (e) {
        e.preventDefault();
        uploadArea.style.borderColor = '#0d6efd';
    });

    uploadArea.addEventListener('dragleave', function () {
        uploadArea.style.borderColor = '#dee2e6';
    });

    uploadArea.addEventListener('drop', function (e) {
        e.preventDefault();
        uploadArea.style.borderColor = '#dee2e6';
        const file = e.dataTransfer.files[0];
        if (file && file.type.startsWith('image/')) {
            handleFileSelect(file);
        }
    });

    // 파일 선택
    fileInput.addEventListener('change', function () {
        if (this.files && this.files[0]) {
            handleFileSelect(this.files[0]);
        }
    });

    const bboxContainer = document.getElementById('bboxContainer');
    const manualBboxContainer = document.getElementById('manualBboxContainer');
    const instruction = document.getElementById('bboxInstruction');

    // 탐지 모드 전환 토글
    document.querySelectorAll('input[name="cropMode"]').forEach(radio => {
        radio.addEventListener('change', function () {
            currentMode = this.value;
            selectedBbox = null; // 모드 변경 시 박스 선택 초기화

            if (currentMode === 'auto') {
                bboxContainer.classList.remove('d-none');
                manualBboxContainer.classList.add('d-none');

                // 기존 박스들 스타일 리셋
                document.querySelectorAll('.bbox-overlay').forEach(el => {
                    el.style.border = '2px solid #0d6efd';
                    el.style.backgroundColor = 'rgba(13, 110, 253, 0.1)';
                    const l = el.querySelector('span');
                    if (l) l.classList.replace('bg-success', 'bg-primary');
                    if (l) l.innerText = l.dataset.originalText;
                });

                if (hasBboxes) {
                    instruction.classList.remove('d-none');
                    instruction.innerHTML = '<i class="fas fa-hand-pointer"></i> 네모 부분(옷)을 클릭하여 검색 부위를 지정하세요!';
                } else {
                    instruction.classList.add('d-none');
                }
            } else {
                bboxContainer.classList.add('d-none');
                manualBboxContainer.classList.remove('d-none');
                document.getElementById('manualBox').classList.add('d-none');

                instruction.classList.remove('d-none');
                instruction.innerHTML = '<i class="fas fa-mouse-pointer"></i> 이미지 위를 드래그하여 원하는 부위를 직접 상자로 지정하세요.';
            }
        });
    });

    /**
     * 이미지 뷰어의 실제 CSS 크기(width/height) 대비 원본 이미지(natural Width)가 
     * object-fit 속성에 의해 어느 정도 Scale 다운되었고, 어딘가 여백(Offset)이 생겼는지 역산함.
     * 마우스 드래그 좌표계를 실제 이미지의 1:1 픽셀 공간 좌표계로 환산하기 위해 꼭 필요한 행렬 변환임.
     * @returns {Object|null} 오프셋(x,y), 렌더링된 크기, 스케일 비율 객체
     */
    function getRenderedImageRect() {
        if (!originalImageObj) return null;

        const wrapper = document.getElementById('imageWrapper');
        const containerW = wrapper.clientWidth;
        const containerH = wrapper.clientHeight;
        const imgW = originalImageSize.w;
        const imgH = originalImageSize.h;

        // object-fit: contain의 경우 화면에 그려지는 비율 계산
        const scaleBase = Math.min(containerW / imgW, containerH / imgH);
        const scale = Math.min(scaleBase, 1.0); // 원본보다 커지지 않게 제한

        const renderedW = imgW * scale;
        const renderedH = imgH * scale;

        // flex-center로 인해 가운데 정렬되므로 여백은 양분됨
        const offsetX = (containerW - renderedW) / 2;
        const offsetY = (containerH - renderedH) / 2;

        return {
            offsetX, offsetY, renderedW, renderedH, scale
        };
    }

    // 수동 박스 드래그 영역 이벤트
    let isDraggingBox = false;
    let startX, startY;
    const manualBox = document.getElementById('manualBox');

    manualBboxContainer.addEventListener('touchstart', function (e) {
        if (currentMode !== 'manual') return;
        const touch = e.touches[0];
        const rect = manualBboxContainer.getBoundingClientRect();
        let clickX = touch.clientX - rect.left;
        let clickY = touch.clientY - rect.top;

        const imgRect = getRenderedImageRect();
        if (!imgRect) return;

        // 모바일 스크롤 방지를 위해, 사진(실제 렌더링된 영역) 위에서 터치될 때만 스크롤을 막고 바깥 여백은 스크롤 허용
        if (clickX >= imgRect.offsetX && clickX <= imgRect.offsetX + imgRect.renderedW &&
            clickY >= imgRect.offsetY && clickY <= imgRect.offsetY + imgRect.renderedH) {
            e.preventDefault();
        }
    }, { passive: false });

    manualBboxContainer.addEventListener('pointerdown', function (e) {
        if (currentMode !== 'manual') return;
        const rect = manualBboxContainer.getBoundingClientRect();
        let clickX = e.clientX - rect.left;
        let clickY = e.clientY - rect.top;

        const imgRect = getRenderedImageRect();
        if (!imgRect) return;

        // 클릭위치가 실제 렌더링된 사진 공간 밖이면 네모 박스를 그리지 않는다 (여백은 여백 그대로 둠)
        if (clickX < imgRect.offsetX || clickX > imgRect.offsetX + imgRect.renderedW ||
            clickY < imgRect.offsetY || clickY > imgRect.offsetY + imgRect.renderedH) {
            return;
        }

        // 클릭위치가 실제 렌더링된 사진 범위를 벗어날 경우 제한
        clickX = Math.max(imgRect.offsetX, Math.min(clickX, imgRect.offsetX + imgRect.renderedW));
        clickY = Math.max(imgRect.offsetY, Math.min(clickY, imgRect.offsetY + imgRect.renderedH));

        startX = clickX;
        startY = clickY;

        isDraggingBox = true;
        manualBox.classList.remove('d-none');
        manualBox.style.left = startX + 'px';
        manualBox.style.top = startY + 'px';
        manualBox.style.width = '0px';
        manualBox.style.height = '0px';
        selectedBbox = null;
    });

    manualBboxContainer.addEventListener('pointermove', function (e) {
        if (!isDraggingBox || currentMode !== 'manual') return;
        const rect = manualBboxContainer.getBoundingClientRect();
        let currentX = e.clientX - rect.left;
        let currentY = e.clientY - rect.top;

        const imgRect = getRenderedImageRect();
        if (!imgRect) return;

        // 현재 커서도 사진 범위 내로 제한
        currentX = Math.max(imgRect.offsetX, Math.min(currentX, imgRect.offsetX + imgRect.renderedW));
        currentY = Math.max(imgRect.offsetY, Math.min(currentY, imgRect.offsetY + imgRect.renderedH));

        const width = Math.abs(currentX - startX);
        const height = Math.abs(currentY - startY);
        const left = Math.min(currentX, startX);
        const top = Math.min(currentY, startY);

        manualBox.style.left = left + 'px';
        manualBox.style.top = top + 'px';
        manualBox.style.width = width + 'px';
        manualBox.style.height = height + 'px';
    });

    manualBboxContainer.addEventListener('pointerup', function (e) {
        if (!isDraggingBox || currentMode !== 'manual') return;
        isDraggingBox = false;

        const rect = manualBboxContainer.getBoundingClientRect();
        let endX = e.clientX - rect.left;
        let endY = e.clientY - rect.top;

        const imgRect = getRenderedImageRect();
        if (!imgRect) return;

        endX = Math.max(imgRect.offsetX, Math.min(endX, imgRect.offsetX + imgRect.renderedW));
        endY = Math.max(imgRect.offsetY, Math.min(endY, imgRect.offsetY + imgRect.renderedH));

        const left = Math.min(endX, startX);
        const top = Math.min(endY, startY);
        const width = Math.abs(endX - startX);
        const height = Math.abs(endY - startY);

        // 박스가 너무 작으면 무시
        if (width < 20 || height < 20) {
            manualBox.classList.add('d-none');
            selectedBbox = null;
            return;
        }

        // 원본 이미지 기준의 실제 픽셀 좌표로 변환하여 저장
        selectedBbox = {
            x1: Math.floor((left - imgRect.offsetX) / imgRect.scale),
            y1: Math.floor((top - imgRect.offsetY) / imgRect.scale),
            w: Math.floor(width / imgRect.scale),
            h: Math.floor(height / imgRect.scale)
        };
    });

    // 박스 밖으로 드래그가 나갈 경우 취소 처리 
    manualBboxContainer.addEventListener('pointerleave', function (e) {
        if (isDraggingBox) {
            isDraggingBox = false;
            const w = parseFloat(manualBox.style.width);
            const h = parseFloat(manualBox.style.height);
            if (w < 20 || h < 20) {
                manualBox.classList.add('d-none');
                selectedBbox = null;
            } else {
                const imgRect = getRenderedImageRect();
                const left = parseFloat(manualBox.style.left);
                const top = parseFloat(manualBox.style.top);

                selectedBbox = {
                    x1: Math.floor((left - imgRect.offsetX) / imgRect.scale),
                    y1: Math.floor((top - imgRect.offsetY) / imgRect.scale),
                    w: Math.floor(w / imgRect.scale),
                    h: Math.floor(h / imgRect.scale)
                };
            }
        }
    });

    // 이미지 삭제 버튼
    removeImageBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        selectedFile = null;
        selectedBbox = null;
        hasBboxes = false;
        originalImageObj = null;
        fileInput.value = '';

        preview.classList.add('d-none');
        placeholder.classList.remove('d-none');
        uploadArea.style.borderColor = '#dee2e6';
        uploadArea.style.borderStyle = 'dashed';
        uploadArea.style.aspectRatio = '1 / 1';
        uploadArea.parentElement.classList.remove('expanded');
        previewImage.src = '';
        previewFileName.textContent = '';
        document.getElementById('bboxContainer').innerHTML = '';
        document.getElementById('bboxInstruction').classList.add('d-none');
    });

    /**
     * 파일 업로드 시 파일 포맷, 용량을 1차 검증하고, FileReader를 이용해 Blob 프리뷰 이미지를 로드.
     * ML 비전 추론 모델로 자동 검사하기 위해 렌더링 준비 후 곧바로 `detectApparel` 함수를 파이프라이닝.
     * @param {File} file 업로드된 이미지 객체
     */
    function handleFileSelect(file) {
        // 파일 형식 체크
        const allowedTypes = ['image/jpeg', 'image/png', 'image/jpg'];
        if (!allowedTypes.includes(file.type)) {
            alert('JPG, PNG 파일만 업로드 가능합니다.');
            return;
        }

        // 파일 크기 체크 (10MB)
        if (file.size > 10 * 1024 * 1024) {
            alert('파일 크기는 10MB 이하여야 합니다.');
            return;
        }

        selectedFile = file;
        selectedBbox = null;
        hasBboxes = false;

        // 미리보기 표시
        const reader = new FileReader();
        reader.onload = function (e) {
            const img = new Image();
            img.onload = function () {
                originalImageSize.w = img.naturalWidth;
                originalImageSize.h = img.naturalHeight;
                originalImageObj = img;

                previewImage.src = e.target.result;
                previewFileName.textContent = file.name + ' (' + formatFileSize(file.size) + ')';
                placeholder.classList.add('d-none');
                preview.classList.remove('d-none');
                uploadArea.style.borderColor = '#198754';
                uploadArea.style.borderStyle = 'solid';
                uploadArea.style.aspectRatio = '3 / 4';
                uploadArea.parentElement.classList.add('expanded');

                // ML Engine 객체 탐지 실행
                detectApparel(file);
            };
            img.src = e.target.result;
        };
        reader.readAsDataURL(file);

        // 검색 버튼 활성화
        searchBtn.disabled = false;
    }

    /**
     * YOLO 서버에 사용자가 업로드한 이미지를 전송하고 객체 바운딩 박스를 반환 및 화면에 렌더링함.
     * 검색 시나리오 중 이미지 내의 상품 위치를 특정하기 위해 수행되는 선행 작업.
     * 
     * @param {File} file - 업로드 폼에서 추출한 렌더링될 이미지 파일 객체.
     * @returns {Promise<void>} 비동기 작업이며, 결과는 직접 DOM에 렌더링되어 별도 반환값 없음.
     * @throws {Error} 네트워크 통신 실패 또는 API 에러 응답 시 (콘솔에 출력).
     */
    async function detectApparel(file) {
        const formData = new FormData();
        formData.append('image', file);

        const loading = document.getElementById('detectLoading');
        const bboxContainer = document.getElementById('bboxContainer');
        const instruction = document.getElementById('bboxInstruction');

        loading.classList.remove('d-none');
        bboxContainer.innerHTML = '';
        instruction.classList.add('d-none');

        try {
            const response = await fetch('/api/search/detect', {
                method: 'POST',
                body: formData
            });

            if (!response.ok) throw new Error('객체 탐지 실패');

            const data = await response.json();

            if (data.success && data.boxes && data.boxes.length > 0) {
                hasBboxes = true;
                instruction.classList.remove('d-none');
                drawBboxes(data.boxes);
            } else {
                hasBboxes = false;
            }
        } catch (e) {
            console.error('객체 탐지 오류:', e);
        } finally {
            loading.classList.add('d-none');
        }
    }

    /**
     * YOLO 서버로부터 리턴받은 수치(Bounding Box 좌표 리스트)들을 DOM 위의 투명 오버레이 박스로 그려냄.
     * 이미지가 리사이징(Contain)된 비율 스케일을 역산해 본래 위치에 마스킹되도록 함.
     * @param {Array} boxes 백엔드 응답(JSON)의 탐지 결과 리스트
     */
    function drawBboxes(boxes) {
        const container = document.getElementById('bboxContainer');
        container.innerHTML = '';
        container.style.pointerEvents = 'auto'; // Make boxes clickable

        const imgRect = getRenderedImageRect();
        if (!imgRect) return;

        boxes.forEach(box => {
            // YOLO 라벨을 카테고리 값으로 미리 정규화해 둔다.
            box.normalizedCategory = mapYoloLabelToCategory(box.label);

            // 원래 좌표 자체의 한도 보정 (백엔드에서 이미지 밖 좌표를 준 경우 방어)
            const originalX1 = Math.max(0, Math.min(box.x1, originalImageSize.w));
            const originalY1 = Math.max(0, Math.min(box.y1, originalImageSize.h));

            const originalMaxX = Math.min(box.x1 + box.w, originalImageSize.w);
            const originalMaxY = Math.min(box.y1 + box.h, originalImageSize.h);
            const originalW = originalMaxX - originalX1;
            const originalH = originalMaxY - originalY1;

            // 렌더링된 사진 공간 내에서의 위치(픽셀)로 변환
            const leftPos = imgRect.offsetX + (originalX1 * imgRect.scale);
            const topPos = imgRect.offsetY + (originalY1 * imgRect.scale);
            const renderedWidth = originalW * imgRect.scale;
            const renderedHeight = originalH * imgRect.scale;

            // 퍼센트 단위로 변환 (컨테이너 전체 기준)
            const wrapperW = document.getElementById('imageWrapper').clientWidth;
            const wrapperH = document.getElementById('imageWrapper').clientHeight;

            const leftPct = (leftPos / wrapperW) * 100;
            const topPct = (topPos / wrapperH) * 100;
            const widthPct = (renderedWidth / wrapperW) * 100;
            const heightPct = (renderedHeight / wrapperH) * 100;

            const div = document.createElement('div');
            div.className = 'bbox-overlay';
            div.style.position = 'absolute';
            div.style.border = '2px solid #0d6efd';
            div.style.backgroundColor = 'rgba(13, 110, 253, 0.1)';
            div.style.cursor = 'pointer';
            div.style.left = leftPct + '%';
            div.style.top = topPct + '%';
            div.style.width = widthPct + '%';
            div.style.height = heightPct + '%';
            div.style.transition = 'all 0.2s';

            // 라벨 추가
            const label = document.createElement('span');
            label.className = 'badge bg-primary position-absolute';
            label.style.top = '-20px';
            label.style.left = '-2px';
            label.innerText = box.label;
            label.dataset.originalText = box.label; // 원래 텍스트 저장
            div.appendChild(label);

            div.addEventListener('mouseenter', () => {
                if (selectedBbox !== box) div.style.backgroundColor = 'rgba(13, 110, 253, 0.3)';
            });
            div.addEventListener('mouseleave', () => {
                if (selectedBbox !== box) div.style.backgroundColor = 'rgba(13, 110, 253, 0.1)';
            });
            div.addEventListener('click', (e) => {
                e.stopPropagation();
                // 선택 취소 및 새로운 거 선택
                if (selectedBbox === box) {
                    selectedBbox = null;
                    div.style.border = '2px solid #0d6efd';
                    div.style.backgroundColor = 'rgba(13, 110, 253, 0.1)';
                    label.classList.replace('bg-success', 'bg-primary');
                } else {
                    selectedBbox = box;
                    document.querySelectorAll('.bbox-overlay').forEach(el => {
                        el.style.border = '2px solid #0d6efd';
                        el.style.backgroundColor = 'rgba(13, 110, 253, 0.1)';
                        const l = el.querySelector('span');
                        if (l) l.classList.replace('bg-success', 'bg-primary');
                    });
                    div.style.border = '3px solid #198754';
                    div.style.backgroundColor = 'rgba(25, 135, 84, 0.3)';
                    label.classList.replace('bg-primary', 'bg-success');
                    label.innerText = '선택됨';

                    // YOLO 박스 선택은 해당 라벨의 카테고리 선택으로 간주한다.
                    const mappedCategory = box.normalizedCategory || mapYoloLabelToCategory(label.dataset.originalText || box.label);
                    console.log('[YOLO->CATEGORY] bbox click:', {
                        rawLabel: box.label,
                        normalizedCategory: box.normalizedCategory,
                        mappedCategory
                    });
                    applyCategorySelection(mappedCategory);
                }
            });

            container.appendChild(div);
        });
    }

    function formatFileSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / 1048576).toFixed(1) + ' MB';
    }

    // 검색 실행
    searchBtn.addEventListener('click', async function () {
        const searchText = searchInput.value.trim();

        // 검색 조건 검증: 이미지 또는 텍스트 중 하나는 필수
        if (!selectedFile && !searchText) {
            alert('이미지를 업로드하거나 검색어를 입력해주세요.');
            return;
        }

        // 카테고리 검증: 선택사항으로 변경됨 (경고창 생략)
        document.getElementById('categoryError').classList.add('d-none');

        // 로딩 표시
        searchLoading.classList.remove('d-none');
        searchResults.classList.add('d-none');
        searchBtn.disabled = true;
        searchBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span> 검색 중...';

        try {
            const formData = new FormData();

            // 이미지가 있을 때만 추가 (크롭 여부 확인)
            if (selectedFile) {
                if (currentMode === 'auto') {
                    // YOLO 자동 모드일 경우: 박스가 있는데 사용자가 아예 선택하지 않은 경우 방지
                    if (hasBboxes && !selectedBbox) {
                        alert('검색될 의류 영역(박스)을 선택해 주세요. (또는 수동 모드로 전환하여 직접 지정하세요)');
                        searchLoading.classList.add('d-none');
                        searchBtn.disabled = false;
                        searchBtn.innerHTML = '<i class="fas fa-search me-2"></i> 검색 시작하기';
                        return;
                    }
                } else if (currentMode === 'manual') {
                    // 수동 드래그 모드일 경우: 영역을 지정하지 않았다면 에러
                    if (!selectedBbox) {
                        alert('수동 드래그 모드입니다. 마우스로 드래그하여 옷 영역을 지정해 주세요.');
                        searchLoading.classList.add('d-none');
                        searchBtn.disabled = false;
                        searchBtn.innerHTML = '<i class="fas fa-search me-2"></i> 검색 시작하기';
                        return;
                    }
                }

                if (selectedBbox && originalImageObj) {
                    // 캔버스에 그 파트만 잘라서 Blob으로 만듦
                    const canvas = document.getElementById('cropCanvas');
                    canvas.width = selectedBbox.w;
                    canvas.height = selectedBbox.h;
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(originalImageObj,
                        selectedBbox.x1, selectedBbox.y1, selectedBbox.w, selectedBbox.h,
                        0, 0, selectedBbox.w, selectedBbox.h);

                    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.95));
                    formData.append('image', blob, 'cropped_' + selectedFile.name);
                } else {
                    // 원본 전송
                    formData.append('image', selectedFile);
                }
            }

            if (searchText) {
                formData.append('search_text', searchText);
            }

            // 성별, 옷종류 정보를 각각 전달
            if (selectedGender) {
                formData.append('gender', selectedGender);
            }
            // category 우선순위:
            // 1) 사용자가 직접 선택한 category 버튼
            // 2) YOLO 자동 박스 선택 라벨(Top/Bottom/Outer) 매핑
            if (selectedClothes) {
                formData.append('category', selectedClothes);
            } else if (selectedBbox) {
                const mapped = selectedBbox.normalizedCategory || mapYoloLabelToCategory(selectedBbox.label);
                if (mapped) {
                    applyCategorySelection(mapped); // UI도 동기화
                    formData.append('category', mapped);
                }
            }
            console.log('[SEARCH REQUEST] payload states:', {
                currentMode,
                selectedClothes,
                selectedBbox,
                selectedGender,
                hasCategoryInFormData: formData.has('category')
            });

            const response = await fetch('/api/search/by-image', {
                method: 'POST',
                body: formData,
            });

            const data = await response.json();

            if (!response.ok) {
                if (response.status === 401) {
                    alert('로그인이 필요합니다. 로그인 후 다시 시도해주세요.');
                    return;
                }
                throw new Error(data.detail || '검색에 실패했습니다.');
            }

            // 결과 표시
            displayResults(data);

        } catch (err) {
            console.error('검색 오류:', err);
            alert(err.message || '검색 중 오류가 발생했습니다.');
        } finally {
            searchLoading.classList.add('d-none');
            searchBtn.disabled = false;
            searchBtn.innerHTML = '<i class="fas fa-search me-2"></i> 검색 시작하기';
        }
    });

    function displayResults(data) {
        resultsContainer.innerHTML = '';

        if (!data.results || data.results.length === 0) {
            resultsContainer.innerHTML = `
                <div class="col-12 text-center py-5">
                    <i class="fas fa-search text-muted mb-3" style="font-size: 3rem;"></i>
                    <p class="text-muted">유사한 상품을 찾지 못했습니다.</p>
                </div>`;
            searchResults.classList.remove('d-none');
            return;
        }

        data.results.forEach(function (item, index) {
            const priceFormatted = item.price.toLocaleString();

            const card = document.createElement('div');
            card.className = 'col';
            card.innerHTML = `
                <a href="/product/${item.product_id}" class="text-decoration-none">
                    <div class="card h-100 product-card position-relative overflow-hidden shadow-sm">
                        <img src="${item.image_url}" class="card-img-top" alt="${item.product_name}"
                            style="height: 350px; object-fit: cover; object-position: top;" onerror="this.src='https://placehold.co/300x400?text=No+Image'">
                        <div class="card-body">
                            <small class="text-muted d-block mb-1">${item.brand}</small>
                            <h6 class="card-title text-truncate fw-bold text-dark">${item.product_name}</h6>
                            <div class="d-flex align-items-center mt-2">
                                <span class="text-danger fw-bold fs-6 me-2">${priceFormatted}원</span>
                            </div>
                            <div class="text-muted small mt-1">${item.mall_name}</div>
                        </div>
                    </div>
                </a>`;
            resultsContainer.appendChild(card);
        });

        searchResults.classList.remove('d-none');

        // 결과 영역으로 스크롤
        searchResults.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
});
