-- 의류 상품 10개 삽입
INSERT INTO
    products (
        model_code,
        brand_name,
        prod_name,
        base_price,
        category_code,
        img_hdfs_path
    )
VALUES (
        'UT-2024-WH',
        'UNIQLO',
        '프리미엄 코튼 반팔 티셔츠 - 화이트',
        19900,
        '상의',
        'https://image.uniqlo.com/UQ/ST3/AsianCommon/imagesgoods/422990/item/goods_01_422990.jpg'
    ),
    (
        'DM-2024-BL',
        'ZARA',
        '슬림핏 데님 자켓 - 블루',
        89900,
        '아우터',
        'https://static.zara.net/photos///2024/V/0/1/p/4661/450/400/2/w/750/4661450400_1_1_1.jpg'
    ),
    (
        'SL-2024-BK',
        'H&M',
        '와이드핏 슬랙스 - 블랙',
        39900,
        '하의',
        'https://www2.hm.com/content/dam/hm/about/2024/01/0001/0001-3x2/3x2-Trousers-Black.jpg'
    ),
    (
        'HD-2024-GY',
        'Nike',
        '에센셜 후드 집업 - 그레이',
        69900,
        '아우터',
        'https://static.nike.com/a/images/t_PDP_1280_v1/f_auto,q_auto:eco/61734ec7-dad8-40f3-9b95-c7500939150a/sportswear-club-fleece-mens-full-zip-hoodie-hDv5Md.png'
    ),
    (
        'SK-2024-NV',
        'SPAO',
        '미니 데님 스커트 - 네이비',
        29900,
        '하의',
        'https://image.spao.com/image/goods/2024/01/SPSK4TG01_BL1_1.jpg'
    ),
    (
        'CT-2024-BE',
        'MUJI',
        '오가닉 코튼 가디건 - 베이지',
        49900,
        '상의',
        'https://img.muji.net/img/item/4550512681861_1260.jpg'
    ),
    (
        'JN-2024-BL',
        'Levis',
        '501 오리지널 진 - 블루',
        119000,
        '하의',
        'https://lsco.scene7.com/is/image/lsco/005010101-front-pdp?fmt=jpeg&qlt=70&resMode=sharp2&fit=crop,1&op_usm=0.6,0.6,8&wid=2000&hei=1840'
    ),
    (
        'SW-2024-WH',
        'Adidas',
        '트레포일 로고 맨투맨 - 화이트',
        59900,
        '상의',
        'https://assets.adidas.com/images/w_600,f_auto,q_auto/8a2871d9c4b44e0ab5f0af3d00e8ca38_9366/Trefoil_Essentials_Crewneck_Sweatshirt_White_IU2664_01_laydown.jpg'
    ),
    (
        'DR-2024-BK',
        'ZARA',
        '미디 A라인 원피스 - 블랙',
        79900,
        '원피스',
        'https://static.zara.net/photos///2024/V/0/1/p/2731/144/800/2/w/750/2731144800_1_1_1.jpg'
    ),
    (
        'SN-2024-WH',
        'Converse',
        '척테일러 올스타 로우 - 화이트',
        69000,
        '신발',
        'https://www.converse.com/dw/image/v2/BCZC_PRD/on/demandware.static/-/Sites-cnv-master-catalog/default/dw3c8c3e3e/images/a_107/M7652_A_107X1.jpg'
    );

-- 각 상품의 product_id를 조회하여 naver_prices 삽입
DO $$
DECLARE
  pid1 BIGINT; pid2 BIGINT; pid3 BIGINT; pid4 BIGINT; pid5 BIGINT;
  pid6 BIGINT; pid7 BIGINT; pid8 BIGINT; pid9 BIGINT; pid10 BIGINT;
BEGIN
  SELECT product_id INTO pid1 FROM products WHERE model_code = 'UT-2024-WH';
  SELECT product_id INTO pid2 FROM products WHERE model_code = 'DM-2024-BL';
  SELECT product_id INTO pid3 FROM products WHERE model_code = 'SL-2024-BK';
  SELECT product_id INTO pid4 FROM products WHERE model_code = 'HD-2024-GY';
  SELECT product_id INTO pid5 FROM products WHERE model_code = 'SK-2024-NV';
  SELECT product_id INTO pid6 FROM products WHERE model_code = 'CT-2024-BE';
  SELECT product_id INTO pid7 FROM products WHERE model_code = 'JN-2024-BL';
  SELECT product_id INTO pid8 FROM products WHERE model_code = 'SW-2024-WH';
  SELECT product_id INTO pid9 FROM products WHERE model_code = 'DR-2024-BK';
  SELECT product_id INTO pid10 FROM products WHERE model_code = 'SN-2024-WH';

  INSERT INTO naver_prices (product_id, rank, price, mall_name, mall_url)
  VALUES
    (pid1, 1, 17900, '네이버쇼핑', 'https://shopping.naver.com/'),
    (pid1, 2, 18500, '11번가', 'https://www.11st.co.kr/'),
    (pid1, 3, 19000, '쿠팡', 'https://www.coupang.com/'),
    (pid1, 4, 19500, 'G마켓', 'https://www.gmarket.co.kr/'),
    (pid1, 5, 19900, 'UNIQLO 공식몰', 'https://www.uniqlo.com/kr/'),
    (pid2, 1, 79900, 'ZARA 공식몰', 'https://www.zara.com/kr/'),
    (pid2, 2, 82000, '무신사', 'https://www.musinsa.com/'),
    (pid2, 3, 85000, '29CM', 'https://www.29cm.co.kr/'),
    (pid2, 4, 87000, 'W컨셉', 'https://www.wconcept.co.kr/'),
    (pid2, 5, 89900, '네이버쇼핑', 'https://shopping.naver.com/'),
    (pid3, 1, 35900, '11번가', 'https://www.11st.co.kr/'),
    (pid3, 2, 37000, '쿠팡', 'https://www.coupang.com/'),
    (pid3, 3, 38500, '네이버쇼핑', 'https://shopping.naver.com/'),
    (pid3, 4, 39000, 'G마켓', 'https://www.gmarket.co.kr/'),
    (pid3, 5, 39900, 'H&M 공식몰', 'https://www2.hm.com/ko_kr/'),
    (pid4, 1, 62900, '쿠팡', 'https://www.coupang.com/'),
    (pid4, 2, 65000, '무신사', 'https://www.musinsa.com/'),
    (pid4, 3, 67000, '네이버쇼핑', 'https://shopping.naver.com/'),
    (pid4, 4, 68500, '11번가', 'https://www.11st.co.kr/'),
    (pid4, 5, 69900, 'Nike 공식몰', 'https://www.nike.com/kr/'),
    (pid5, 1, 26900, '네이버쇼핑', 'https://shopping.naver.com/'),
    (pid5, 2, 27500, '쿠팡', 'https://www.coupang.com/'),
    (pid5, 3, 28000, '11번가', 'https://www.11st.co.kr/'),
    (pid5, 4, 29000, 'G마켓', 'https://www.gmarket.co.kr/'),
    (pid5, 5, 29900, 'SPAO 공식몰', 'https://www.spao.com/'),
    (pid6, 1, 44900, '쿠팡', 'https://www.coupang.com/'),
    (pid6, 2, 46000, '네이버쇼핑', 'https://shopping.naver.com/'),
    (pid6, 3, 47500, '11번가', 'https://www.11st.co.kr/'),
    (pid6, 4, 48500, 'G마켓', 'https://www.gmarket.co.kr/'),
    (pid6, 5, 49900, 'MUJI 공식몰', 'https://www.muji.com/kr/'),
    (pid7, 1, 109000, '무신사', 'https://www.musinsa.com/'),
    (pid7, 2, 112000, '29CM', 'https://www.29cm.co.kr/'),
    (pid7, 3, 115000, '네이버쇼핑', 'https://shopping.naver.com/'),
    (pid7, 4, 117000, '쿠팡', 'https://www.coupang.com/'),
    (pid7, 5, 119000, 'Levis 공식몰', 'https://www.levi.com/KR/ko_KR/'),
    (pid8, 1, 53900, '쿠팡', 'https://www.coupang.com/'),
    (pid8, 2, 55000, '네이버쇼핑', 'https://shopping.naver.com/'),
    (pid8, 3, 57000, '무신사', 'https://www.musinsa.com/'),
    (pid8, 4, 58500, '11번가', 'https://www.11st.co.kr/'),
    (pid8, 5, 59900, 'Adidas 공식몰', 'https://www.adidas.co.kr/'),
    (pid9, 1, 71900, 'ZARA 공식몰', 'https://www.zara.com/kr/'),
    (pid9, 2, 73000, '무신사', 'https://www.musinsa.com/'),
    (pid9, 3, 75000, '29CM', 'https://www.29cm.co.kr/'),
    (pid9, 4, 77000, '네이버쇼핑', 'https://shopping.naver.com/'),
    (pid9, 5, 79900, 'W컨셉', 'https://www.wconcept.co.kr/'),
    (pid10, 1, 62000, '쿠팡', 'https://www.coupang.com/'),
    (pid10, 2, 64000, '무신사', 'https://www.musinsa.com/'),
    (pid10, 3, 66000, '네이버쇼핑', 'https://shopping.naver.com/'),
    (pid10, 4, 67500, '11번가', 'https://www.11st.co.kr/'),
    (pid10, 5, 69000, 'Converse 공식몰', 'https://www.converse.co.kr/');

  INSERT INTO product_features (product_id, detected_desc)
  VALUES
    (pid1, '편안한 착용감의 프리미엄 코튼 100% 반팔 티셔츠. 데일리룩으로 활용하기 좋으며 다양한 컬러와 매치가 용이합니다. 시즌 내내 입기 좋은 베이직 아이템.'),
    (pid2, '슬림한 핏의 클래식 데님 자켓. 세련된 블루 워싱으로 캐주얼하면서도 모던한 스타일 연출 가능. 사계절 활용도 높은 아우터.'),
    (pid3, '편안한 와이드 핏의 블랙 슬랙스. 신축성 있는 원단으로 활동성이 뛰어나며 오피스룩부터 캐주얼까지 다양하게 활용 가능.'),
    (pid4, '부드러운 기모 안감의 에센셜 후드 집업. 심플한 디자인으로 데일리 착용에 최적화. 그레이 컬러로 어떤 스타일과도 매치 용이.'),
    (pid5, '트렌디한 미니 길이의 데님 스커트. 네이비 컬러로 활용도가 높으며 캐주얼하면서도 여성스러운 무드 연출 가능.'),
    (pid6, '오가닉 코튼 100% 소재의 가디건. 베이직한 베이지 컬러로 레이어드하기 좋으며 부드러운 촉감이 특징. 사계절 활용 가능.'),
    (pid7, '리바이스의 아이코닉한 501 오리지널 진. 클래식한 스트레이트 핏으로 시대를 초월한 스타일. 내구성 뛰어난 데님 원단 사용.'),
    (pid8, '아디다스 트레포일 로고가 포인트인 맨투맨. 화이트 컬러의 클린한 디자인으로 데일리 착용에 최적. 편안한 기본 핏.'),
    (pid9, '우아한 A라인 실루엣의 미디 원피스. 블랙 컬러로 포멀한 자리부터 데일리까지 활용 가능. 여성스러운 무드의 베이직 아이템.'),
    (pid10, '컨버스의 클래식 척테일러 올스타 로우탑. 화이트 캔버스 소재로 어떤 스타일과도 매치 용이. 타임리스한 디자인의 스니커즈.');
END $$;