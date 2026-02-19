#!/bin/bash

# ============================================
#  📦 테스트 데이터 삽입 스크립트
# ============================================
# 성별(남자/여자) × 카테고리(상의/하의/아우터) = 18개 상품
# 각 상품당 naver_prices 3개 + product_features 1개
#
# 검색 테스트 목적:
#   - 카테고리 필터(상의/하의/아우터) 동작 검증
#   - 성별 필터(남자/여자) 동작 검증
#   - 조합 필터(예: "여자_상의") 검증
# ============================================



CONTAINER_NAME="postgres-main"
DB_NAME="datadb"
DB_USER="datauser"

echo "============================================"
echo "  📦 테스트 데이터 삽입 시작"
echo "============================================"
echo ""

# PostgreSQL 컨테이너 확인
echo "0️⃣  PostgreSQL 컨테이너 확인..."
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "   ❌ ${CONTAINER_NAME} 컨테이너가 실행 중이지 않습니다"
    exit 1
fi
echo "   ✅ ${CONTAINER_NAME} 실행 중"
echo ""

# 기존 테스트 데이터 정리 (model_code 패턴으로 구분)
echo "🗑️   기존 테스트 데이터 정리..."
docker exec -i ${CONTAINER_NAME} psql -U ${DB_USER} -d ${DB_NAME} << 'EOF'
-- NULL product_id 고아 데이터 먼저 삭제 (이전 실패 실행 잔재)
DELETE FROM naver_prices WHERE product_id IS NULL;
DELETE FROM product_features WHERE product_id IS NULL;
-- TEST- 접두사 상품 및 연결 데이터 삭제 (CASCADE로 naver_prices, product_features 자동 삭제)
DELETE FROM naver_prices
WHERE product_id IN (SELECT product_id FROM products WHERE model_code LIKE 'TEST-%');
DELETE FROM product_features
WHERE product_id IN (SELECT product_id FROM products WHERE model_code LIKE 'TEST-%');
DELETE FROM products WHERE model_code LIKE 'TEST-%';
EOF
echo "   ✅ 기존 테스트 데이터 정리 완료"
echo ""

# ──────────────────────────────────────────────
# 1. Products 18개 삽입 (gender + category_code 포함)
# ──────────────────────────────────────────────
echo "1️⃣  Products 테이블에 18개 상품 삽입..."
echo "   (남자 상의 3 + 하의 3 + 아우터 3 / 여자 상의 3 + 하의 3 + 아우터 3)"

docker exec -i ${CONTAINER_NAME} psql -U ${DB_USER} -d ${DB_NAME} << 'EOF'
INSERT INTO products (model_code, brand_name, prod_name, base_price, category_code, gender, img_hdfs_path)
VALUES
  -- 남자 상의 (3개)
  ('TEST-M-TOP-01', 'UNIQLO',   '드라이EX 반팔 라운드넥 - 네이비',        19900, '상의', '남자', 'https://image.uniqlo.com/UQ/ST3/AsianCommon/imagesgoods/422990/item/goods_01_422990.jpg'),
  ('TEST-M-TOP-02', 'Nike',     '드라이핏 트레이닝 티셔츠 - 블랙',        35000, '상의', '남자', 'https://static.nike.com/a/images/t_PDP_1280_v1/f_auto,q_auto:eco/dri-fit-training-tshirt.jpg'),
  ('TEST-M-TOP-03', '무신사스탠다드', '베이직 오버핏 반팔 티셔츠 - 화이트', 22000, '상의', '남자', 'https://image.musinsa.com/mfile_s01/2024/01/basic-overfit-white.jpg'),

  -- 남자 하의 (3개)
  ('TEST-M-BOT-01', 'Levis',    '512 슬림 테이퍼 진 - 블루',             99000, '하의', '남자', 'https://lsco.scene7.com/is/image/lsco/005120101-front-pdp.jpg'),
  ('TEST-M-BOT-02', 'Adidas',   '에센셜 트레이닝 조거 팬츠 - 그레이',    55000, '하의', '남자', 'https://assets.adidas.com/images/w_600/jogging-pants-grey.jpg'),
  ('TEST-M-BOT-03', '무신사스탠다드', '와이드 치노 팬츠 - 베이지',         39000, '하의', '남자', 'https://image.musinsa.com/mfile_s01/2024/01/wide-chino-beige.jpg'),

  -- 남자 아우터 (3개)
  ('TEST-M-OUT-01', 'ZARA',     '슬림핏 데님 자켓 - 인디고 블루',         89900, '아우터', '남자', 'https://static.zara.net/photos/2024/V/0/1/p/4661/450/400/2/w/750/slim-denim-jacket.jpg'),
  ('TEST-M-OUT-02', 'North Face','눕시 패딩 다운 자켓 - 블랙',           289000, '아우터', '남자', 'https://images.thenorthface.com/is/image/TheNorthFace/nuptse-jacket-black.jpg'),
  ('TEST-M-OUT-03', 'Patagonia', '나노퍼프 가벼운 인슐레이션 자켓 - 올리브', 349000, '아우터', '남자', 'https://www.patagonia.com/dw/image/v2/nano-puff-olive.jpg'),

  -- 여자 상의 (3개)
  ('TEST-W-TOP-01', 'ZARA',     '플로럴 프린트 블라우스 - 화이트 패턴',   69900, '상의', '여자', 'https://static.zara.net/photos/2024/V/0/1/p/floral-blouse-white.jpg'),
  ('TEST-W-TOP-02', 'H&M',      '크롭 리브드 니트 탑 - 크림',            29900, '상의', '여자', 'https://www2.hm.com/content/dam/hm/about/crop-ribbed-knit-cream.jpg'),
  ('TEST-W-TOP-03', '29CM',     '오프숄더 코튼 티셔츠 - 라벤더',          35000, '상의', '여자', 'https://img.29cm.co.kr/media/2024/off-shoulder-lavender.jpg'),

  -- 여자 하의 (3개)
  ('TEST-W-BOT-01', 'SPAO',     '미니 플리츠 스커트 - 블랙',             35900, '하의', '여자', 'https://image.spao.com/image/goods/2024/01/SPSK4TG01_BK1_1.jpg'),
  ('TEST-W-BOT-02', 'WHO.A.U',  '하이웨이스트 와이드 데님 팬츠 - 라이트블루', 59000, '하의', '여자', 'https://whoau.speedycdn.com/images/2024/highwaist-wide-denim-lb.jpg'),
  ('TEST-W-BOT-03', 'Mango',    '린넨 와이드 팬츠 - 아이보리',           79000, '하의', '여자', 'https://st.mngbcn.com/rcs/pics/static/T7/fotos/linen-wide-pants-ivory.jpg'),

  -- 여자 아우터 (3개)
  ('TEST-W-OUT-01', 'UNIQLO',   '후리스 풀집업 재킷 - 핑크',             59900, '아우터', '여자', 'https://image.uniqlo.com/UQ/ST3/AsianCommon/imagesgoods/fleece-pink.jpg'),
  ('TEST-W-OUT-02', 'W컨셉',    '울 혼방 롱 코트 - 카멜',               259000, '아우터', '여자', 'https://image.wconcept.co.kr/productimg/2024/wool-long-coat-camel.jpg'),
  ('TEST-W-OUT-03', 'MOJO.S.PHINE', '오버핏 트위드 자켓 - 블랙/화이트', 189000, '아우터', '여자', 'https://www.mojo.co.kr/upload/2024/tweed-jacket-bw.jpg');
EOF

echo "   ✅ Products 18개 삽입 완료"
echo ""

# ──────────────────────────────────────────────
# 2. Naver_prices (상품별 최저가 1위 쇼핑몰 + 2개)
# ──────────────────────────────────────────────
echo "2️⃣  Naver_prices 테이블에 쇼핑몰 가격 삽입 (상품당 5개)..."
docker exec -i ${CONTAINER_NAME} psql -U ${DB_USER} -d ${DB_NAME} << 'PSQL'
DO $$
DECLARE
  pid BIGINT;
BEGIN
  -- ── 남자 상의 ──
  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-M-TOP-01';
  INSERT INTO naver_prices (product_id,rank,price,mall_name,mall_url) VALUES
    (pid,1,15900,'쿠팡','https://www.coupang.com/'),
    (pid,2,17900,'11번가','https://www.11st.co.kr/'),
    (pid,3,18500,'무신사','https://www.musinsa.com/'),
    (pid,4,19000,'G마켓','https://www.gmarket.co.kr/'),
    (pid,5,19900,'UNIQLO','https://www.uniqlo.com/kr/');

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-M-TOP-02';
  INSERT INTO naver_prices (product_id,rank,price,mall_name,mall_url) VALUES
    (pid,1,29000,'쿠팡','https://www.coupang.com/'),
    (pid,2,30000,'11번가','https://www.11st.co.kr/'),
    (pid,3,32000,'무신사','https://www.musinsa.com/'),
    (pid,4,33500,'네이버쇼핑','https://shopping.naver.com/'),
    (pid,5,35000,'Nike','https://www.nike.com/kr/');

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-M-TOP-03';
  INSERT INTO naver_prices (product_id,rank,price,mall_name,mall_url) VALUES
    (pid,1,18000,'무신사','https://www.musinsa.com/'),
    (pid,2,19000,'쿠팡','https://www.coupang.com/'),
    (pid,3,20000,'29CM','https://www.29cm.co.kr/'),
    (pid,4,21000,'G마켓','https://www.gmarket.co.kr/'),
    (pid,5,22000,'네이버쇼핑','https://shopping.naver.com/');

  -- ── 남자 하의 ──
  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-M-BOT-01';
  INSERT INTO naver_prices (product_id,rank,price,mall_name,mall_url) VALUES
    (pid,1,89000,'무신사','https://www.musinsa.com/'),
    (pid,2,92000,'쿠팡','https://www.coupang.com/'),
    (pid,3,94000,'29CM','https://www.29cm.co.kr/'),
    (pid,4,96000,'네이버쇼핑','https://shopping.naver.com/'),
    (pid,5,99000,'Levis공식','https://www.levi.com/KR/');

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-M-BOT-02';
  INSERT INTO naver_prices (product_id,rank,price,mall_name,mall_url) VALUES
    (pid,1,45000,'쿠팡','https://www.coupang.com/'),
    (pid,2,47000,'11번가','https://www.11st.co.kr/'),
    (pid,3,50000,'무신사','https://www.musinsa.com/'),
    (pid,4,52000,'G마켓','https://www.gmarket.co.kr/'),
    (pid,5,55000,'Adidas','https://www.adidas.co.kr/');

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-M-BOT-03';
  INSERT INTO naver_prices (product_id,rank,price,mall_name,mall_url) VALUES
    (pid,1,33000,'무신사','https://www.musinsa.com/'),
    (pid,2,35000,'쿠팡','https://www.coupang.com/'),
    (pid,3,36000,'29CM','https://www.29cm.co.kr/'),
    (pid,4,37500,'네이버쇼핑','https://shopping.naver.com/'),
    (pid,5,39000,'W컨셉','https://www.wconcept.co.kr/');

  -- ── 남자 아우터 ──
  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-M-OUT-01';
  INSERT INTO naver_prices (product_id,rank,price,mall_name,mall_url) VALUES
    (pid,1,79900,'ZARA','https://www.zara.com/kr/'),
    (pid,2,82000,'무신사','https://www.musinsa.com/'),
    (pid,3,84000,'29CM','https://www.29cm.co.kr/'),
    (pid,4,87000,'네이버쇼핑','https://shopping.naver.com/'),
    (pid,5,89900,'W컨셉','https://www.wconcept.co.kr/');

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-M-OUT-02';
  INSERT INTO naver_prices (product_id,rank,price,mall_name,mall_url) VALUES
    (pid,1,259000,'쿠팡','https://www.coupang.com/'),
    (pid,2,265000,'무신사','https://www.musinsa.com/'),
    (pid,3,272000,'11번가','https://www.11st.co.kr/'),
    (pid,4,280000,'네이버쇼핑','https://shopping.naver.com/'),
    (pid,5,289000,'NorthFace','https://www.thenorthface.co.kr/');

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-M-OUT-03';
  INSERT INTO naver_prices (product_id,rank,price,mall_name,mall_url) VALUES
    (pid,1,329000,'무신사','https://www.musinsa.com/'),
    (pid,2,334000,'29CM','https://www.29cm.co.kr/'),
    (pid,3,339000,'쿠팡','https://www.coupang.com/'),
    (pid,4,344000,'네이버쇼핑','https://shopping.naver.com/'),
    (pid,5,349000,'Patagonia','https://www.patagonia.com/kr/');

  -- ── 여자 상의 ──
  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-W-TOP-01';
  INSERT INTO naver_prices (product_id,rank,price,mall_name,mall_url) VALUES
    (pid,1,59900,'ZARA','https://www.zara.com/kr/'),
    (pid,2,62000,'무신사','https://www.musinsa.com/'),
    (pid,3,64000,'W컨셉','https://www.wconcept.co.kr/'),
    (pid,4,67000,'29CM','https://www.29cm.co.kr/'),
    (pid,5,69900,'네이버쇼핑','https://shopping.naver.com/');

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-W-TOP-02';
  INSERT INTO naver_prices (product_id,rank,price,mall_name,mall_url) VALUES
    (pid,1,24900,'H&M','https://www2.hm.com/ko_kr/'),
    (pid,2,26000,'쿠팡','https://www.coupang.com/'),
    (pid,3,27000,'11번가','https://www.11st.co.kr/'),
    (pid,4,28500,'무신사','https://www.musinsa.com/'),
    (pid,5,29900,'네이버쇼핑','https://shopping.naver.com/');

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-W-TOP-03';
  INSERT INTO naver_prices (product_id,rank,price,mall_name,mall_url) VALUES
    (pid,1,29000,'29CM','https://www.29cm.co.kr/'),
    (pid,2,30500,'W컨셉','https://www.wconcept.co.kr/'),
    (pid,3,32000,'무신사','https://www.musinsa.com/'),
    (pid,4,33500,'네이버쇼핑','https://shopping.naver.com/'),
    (pid,5,35000,'G마켓','https://www.gmarket.co.kr/');

  -- ── 여자 하의 ──
  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-W-BOT-01';
  INSERT INTO naver_prices (product_id,rank,price,mall_name,mall_url) VALUES
    (pid,1,29900,'SPAO','https://www.spao.com/'),
    (pid,2,31000,'쿠팡','https://www.coupang.com/'),
    (pid,3,32500,'무신사','https://www.musinsa.com/'),
    (pid,4,34000,'11번가','https://www.11st.co.kr/'),
    (pid,5,35900,'네이버쇼핑','https://shopping.naver.com/');

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-W-BOT-02';
  INSERT INTO naver_prices (product_id,rank,price,mall_name,mall_url) VALUES
    (pid,1,49000,'무신사','https://www.musinsa.com/'),
    (pid,2,52000,'29CM','https://www.29cm.co.kr/'),
    (pid,3,54000,'W컨셉','https://www.wconcept.co.kr/'),
    (pid,4,56500,'네이버쇼핑','https://shopping.naver.com/'),
    (pid,5,59000,'G마켓','https://www.gmarket.co.kr/');

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-W-BOT-03';
  INSERT INTO naver_prices (product_id,rank,price,mall_name,mall_url) VALUES
    (pid,1,69000,'무신사','https://www.musinsa.com/'),
    (pid,2,72000,'W컨셉','https://www.wconcept.co.kr/'),
    (pid,3,74000,'29CM','https://www.29cm.co.kr/'),
    (pid,4,76500,'네이버쇼핑','https://shopping.naver.com/'),
    (pid,5,79000,'Mango','https://shop.mango.com/kr/');

  -- ── 여자 아우터 ──
  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-W-OUT-01';
  INSERT INTO naver_prices (product_id,rank,price,mall_name,mall_url) VALUES
    (pid,1,49900,'UNIQLO','https://www.uniqlo.com/kr/'),
    (pid,2,52000,'쿠팡','https://www.coupang.com/'),
    (pid,3,54000,'무신사','https://www.musinsa.com/'),
    (pid,4,56500,'11번가','https://www.11st.co.kr/'),
    (pid,5,59900,'네이버쇼핑','https://shopping.naver.com/');

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-W-OUT-02';
  INSERT INTO naver_prices (product_id,rank,price,mall_name,mall_url) VALUES
    (pid,1,229000,'W컨셉','https://www.wconcept.co.kr/'),
    (pid,2,237000,'29CM','https://www.29cm.co.kr/'),
    (pid,3,244000,'무신사','https://www.musinsa.com/'),
    (pid,4,251000,'네이버쇼핑','https://shopping.naver.com/'),
    (pid,5,259000,'G마켓','https://www.gmarket.co.kr/');

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-W-OUT-03';
  INSERT INTO naver_prices (product_id,rank,price,mall_name,mall_url) VALUES
    (pid,1,169000,'무신사','https://www.musinsa.com/'),
    (pid,2,174000,'29CM','https://www.29cm.co.kr/'),
    (pid,3,179000,'W컨셉','https://www.wconcept.co.kr/'),
    (pid,4,184000,'네이버쇼핑','https://shopping.naver.com/'),
    (pid,5,189000,'MOJO','https://www.mojo.co.kr/');

END $$;
PSQL

echo "   ✅ Naver_prices 삽입 완료 (상품당 5개 쇼핑몰)"
echo ""

# ──────────────────────────────────────────────
# 3. Product_features (VLM 스타일 설명)
# ──────────────────────────────────────────────
echo "3️⃣  Product_features 테이블에 상품 설명 삽입..."
docker exec -i ${CONTAINER_NAME} psql -U ${DB_USER} -d ${DB_NAME} << 'PSQL'
DO $$
DECLARE pid BIGINT;
BEGIN
  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-M-TOP-01';
  INSERT INTO product_features(product_id,detected_desc) VALUES(pid,'네이비 컬러의 반팔 라운드넥 티셔츠. 드라이 소재로 흡습성 우수. 남성 데일리 기본템.') ON CONFLICT(product_id) DO UPDATE SET detected_desc=EXCLUDED.detected_desc;

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-M-TOP-02';
  INSERT INTO product_features(product_id,detected_desc) VALUES(pid,'블랙 드라이핏 트레이닝 티셔츠. 통기성 좋은 기능성 소재로 운동복으로 최적화된 남성 상의.') ON CONFLICT(product_id) DO UPDATE SET detected_desc=EXCLUDED.detected_desc;

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-M-TOP-03';
  INSERT INTO product_features(product_id,detected_desc) VALUES(pid,'화이트 오버핏 반팔 티셔츠. 여유로운 실루엣으로 캐주얼 무드. 남성 스트릿 패션 기본 아이템.') ON CONFLICT(product_id) DO UPDATE SET detected_desc=EXCLUDED.detected_desc;

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-M-BOT-01';
  INSERT INTO product_features(product_id,detected_desc) VALUES(pid,'블루 슬림 테이퍼 데님 진. 발목 라인이 좁아지는 테이퍼드 핏으로 남성 하의 스타일링에 활용도 높음.') ON CONFLICT(product_id) DO UPDATE SET detected_desc=EXCLUDED.detected_desc;

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-M-BOT-02';
  INSERT INTO product_features(product_id,detected_desc) VALUES(pid,'그레이 조거 팬츠. 밴딩 허리와 조거 밑단으로 편안함 극대화. 남성 스포티 캐주얼 스타일링.') ON CONFLICT(product_id) DO UPDATE SET detected_desc=EXCLUDED.detected_desc;

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-M-BOT-03';
  INSERT INTO product_features(product_id,detected_desc) VALUES(pid,'베이지 와이드 치노 팬츠. 넉넉한 와이드 핏으로 편안하며 클린한 캐주얼 남성 하의.') ON CONFLICT(product_id) DO UPDATE SET detected_desc=EXCLUDED.detected_desc;

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-M-OUT-01';
  INSERT INTO product_features(product_id,detected_desc) VALUES(pid,'인디고 블루 슬림핏 데님 자켓. 클래식 포켓 디테일. 남성 봄가을 아우터로 활용하기 좋은 아이템.') ON CONFLICT(product_id) DO UPDATE SET detected_desc=EXCLUDED.detected_desc;

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-M-OUT-02';
  INSERT INTO product_features(product_id,detected_desc) VALUES(pid,'블랙 눕시 패딩 다운 자켓. 충전재 700 필파워로 따뜻함 우수. 남성 겨울 아우터 베스트셀러.') ON CONFLICT(product_id) DO UPDATE SET detected_desc=EXCLUDED.detected_desc;

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-M-OUT-03';
  INSERT INTO product_features(product_id,detected_desc) VALUES(pid,'올리브 경량 인슐레이션 자켓. 압축 보관 가능하며 방풍 소재로 아웃도어 남성 아우터로 최적.') ON CONFLICT(product_id) DO UPDATE SET detected_desc=EXCLUDED.detected_desc;

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-W-TOP-01';
  INSERT INTO product_features(product_id,detected_desc) VALUES(pid,'화이트 플로럴 패턴 블라우스. 부드러운 소재와 꽃무늬 프린트로 여성스러운 봄여름 상의.') ON CONFLICT(product_id) DO UPDATE SET detected_desc=EXCLUDED.detected_desc;

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-W-TOP-02';
  INSERT INTO product_features(product_id,detected_desc) VALUES(pid,'크림 컬러 크롭 리브드 니트 탑. 골지 소재에 크롭 기장으로 여성 캐주얼 데일리 상의.') ON CONFLICT(product_id) DO UPDATE SET detected_desc=EXCLUDED.detected_desc;

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-W-TOP-03';
  INSERT INTO product_features(product_id,detected_desc) VALUES(pid,'라벤더 오프숄더 코튼 티셔츠. 어깨가 드러나는 디자인으로 여성 여름 상의 트렌드 아이템.') ON CONFLICT(product_id) DO UPDATE SET detected_desc=EXCLUDED.detected_desc;

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-W-BOT-01';
  INSERT INTO product_features(product_id,detected_desc) VALUES(pid,'블랙 미니 플리츠 스커트. 잔주름 디테일로 여성스러운 실루엣 연출. 다양한 상의와 매치 용이.') ON CONFLICT(product_id) DO UPDATE SET detected_desc=EXCLUDED.detected_desc;

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-W-BOT-02';
  INSERT INTO product_features(product_id,detected_desc) VALUES(pid,'라이트블루 하이웨이스트 와이드 데님 팬츠. 높은 허리선과 넓은 핏으로 다리가 길어보이는 여성 하의.') ON CONFLICT(product_id) DO UPDATE SET detected_desc=EXCLUDED.detected_desc;

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-W-BOT-03';
  INSERT INTO product_features(product_id,detected_desc) VALUES(pid,'아이보리 린넨 와이드 팬츠. 천연 린넨 소재로 시원하고 자연스러운 드레이프감. 여성 여름 하의.') ON CONFLICT(product_id) DO UPDATE SET detected_desc=EXCLUDED.detected_desc;

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-W-OUT-01';
  INSERT INTO product_features(product_id,detected_desc) VALUES(pid,'핑크 후리스 풀집업 재킷. 부드럽고 따뜻한 플리스 소재로 여성 가을겨울 아우터로 인기.') ON CONFLICT(product_id) DO UPDATE SET detected_desc=EXCLUDED.detected_desc;

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-W-OUT-02';
  INSERT INTO product_features(product_id,detected_desc) VALUES(pid,'카멜 울 혼방 롱 코트. 클래식한 카멜 컬러의 롱 실루엣으로 여성 겨울 고급 아우터.') ON CONFLICT(product_id) DO UPDATE SET detected_desc=EXCLUDED.detected_desc;

  SELECT product_id INTO pid FROM products WHERE model_code = 'TEST-W-OUT-03';
  INSERT INTO product_features(product_id,detected_desc) VALUES(pid,'블랙&화이트 트위드 오버핏 자켓. 클래식한 트위드 패턴에 오버핏 실루엣으로 여성 정장 아우터.') ON CONFLICT(product_id) DO UPDATE SET detected_desc=EXCLUDED.detected_desc;
END $$;
PSQL

echo "   ✅ Product_features 삽입 완료"
echo ""

# ──────────────────────────────────────────────
# 4. 데이터 확인
# ──────────────────────────────────────────────
echo "4️⃣  삽입된 데이터 확인..."
docker exec ${CONTAINER_NAME} psql -U ${DB_USER} -d ${DB_NAME} -c "
SELECT
  gender,
  category_code,
  COUNT(*) AS 상품수
FROM products
WHERE model_code LIKE 'TEST-%'
GROUP BY gender, category_code
ORDER BY gender, category_code;
"

PRODUCT_COUNT=$(docker exec ${CONTAINER_NAME} psql -U ${DB_USER} -d ${DB_NAME} -t -c "SELECT COUNT(*) FROM products WHERE model_code LIKE 'TEST-%';")
PRICE_COUNT=$(docker exec ${CONTAINER_NAME} psql -U ${DB_USER} -d ${DB_NAME} -t -c "SELECT COUNT(*) FROM naver_prices np JOIN products p ON np.product_id = p.product_id WHERE p.model_code LIKE 'TEST-%';")
FEATURE_COUNT=$(docker exec ${CONTAINER_NAME} psql -U ${DB_USER} -d ${DB_NAME} -t -c "SELECT COUNT(*) FROM product_features pf JOIN products p ON pf.product_id = p.product_id WHERE p.model_code LIKE 'TEST-%';")

echo ""
echo "============================================"
echo "  🚀 테스트 데이터 삽입 완료!"
echo ""
echo "  삽입된 데이터 요약:"
echo "    ✅ 상품 (products):       ${PRODUCT_COUNT}개"
echo "    ✅ 쇼핑몰 가격 (naver_prices): ${PRICE_COUNT}개"
echo "    ✅ 상품 설명 (product_features): ${FEATURE_COUNT}개"
echo ""
echo "  카테고리 구성:"
echo "    남자 상의 3개 / 남자 하의 3개 / 남자 아우터 3개"
echo "    여자 상의 3개 / 여자 하의 3개 / 여자 아우터 3개"
echo ""
echo "  검색 필터 테스트:"
echo "    → '남자' + '상의' 선택 시 3개 결과 예상"
echo "    → '여자' + '아우터' 선택 시 3개 결과 예상"
echo "============================================"
