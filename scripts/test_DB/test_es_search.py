import asyncio
import sys
import json
import glob
from pathlib import Path

# FastAPI 백엔드의 경로를 모듈로 인식할 수 있게 추가
sys.path.append("/home/ubuntu/main-project-lookalike/web/backend")

from app.services.search_service import _search_by_knn

async def main():
    # 저장되어 있는 text_embed_json 하나를 읽어서 query_vector로 활용
    json_files = glob.glob("/app/data-pipeline/elasticsearch/data/text_embed_json/*.json")
    if not json_files:
        print("텍스트 임베딩 파일이 없습니다.")
        return

    sample_file = json_files[0]
    with open(sample_file, 'r', encoding='utf-8') as f:
        doc = json.load(f)
    
    query_vector = doc.get("text_vector")
    if not query_vector:
        print("text_vector가 없습니다.")
        return
        
    print(f"✅ 테스트용 기준 상품 ID: {doc.get('product_id')}")
    print(f"✅ 벡터 차원 수: {len(query_vector)}")
    print("--------------------------------------------------")
    print("🔍 Elasticsearch kNN API 및 PostgreSQL Hydration 테스트 중...")
    
    try:
        results = await _search_by_knn(query_vector=query_vector, category=None, limit=5)
        print("✅ 리턴된 결과 건수:", len(results))
        for i, item in enumerate(results, 1):
            print(f"[{i}위] {item['brand']} - {item['product_name']} (가격: {item['price']}원)")
            print(f"     => 유사도(점수) 여부: {'포함됨' if 'similarity_score' in item else '없음'}, ES Search Source: {item.get('search_source')}")
            print(f"     => 쇼핑몰: {item['mall_name']} ({item['mall_url']})")
    except Exception as e:
        print(f"❌ kNN 테스트 실패: {e}")

if __name__ == "__main__":
    asyncio.run(main())
