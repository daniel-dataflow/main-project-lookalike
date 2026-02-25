import os
from hdfs import InsecureClient
from datetime import datetime

# HDFS Configuration
# 실제 환경에 맞게 수정 필요. 보통 로컬 테스트 시 localhost:9870
HDFS_URL = os.getenv("HDFS_URL", "http://localhost:9870")
HDFS_USER = os.getenv("HDFS_USER", "user")

def get_hdfs_client():
    try:
        return InsecureClient(HDFS_URL, user=HDFS_USER)
    except Exception as e:
        print(f"⚠️ HDFS Client Connection Failed: {e}")
        return None

def save_html_to_hdfs(html_content, brand, product_id):
    """
    HTML 컨텐츠를 HDFS에 저장합니다.
    경로: /datalake/raw/{brand}/{YYYYMMDD}/{product_id}.html
    """
    client = get_hdfs_client()
    if not client:
        # Fallback: HDFS 연결 실패 시 로컬에 저장 (테스트 용도)
        save_local_fallback(html_content, brand, product_id)
        return

    today = datetime.now().strftime("%Y%m%d")
    hdfs_path = f"/datalake/raw/{brand}/{today}/{product_id}.html"

    try:
        # with client.write(hdfs_path, encoding='utf-8', overwrite=True) as writer:
        #     writer.write(html_content)
        # hdfs 라이브러리 사용법에 따라 조정
        client.write(hdfs_path, data=html_content, encoding='utf-8', overwrite=True)
        print(f"✅ Saved to HDFS: {hdfs_path}")
    except Exception as e:
        print(f"❌ Failed to save to HDFS {hdfs_path}: {e}")
        # 실패 시 로컬 저장 시도
        save_local_fallback(html_content, brand, product_id)

def save_local_fallback(html_content, brand, product_id):
    today = datetime.now().strftime("%Y%m%d")
    local_dir = f"c:/project/datalake/raw/{brand}/{today}"
    os.makedirs(local_dir, exist_ok=True)
    local_path = os.path.join(local_dir, f"{product_id}.html")
    
    with open(local_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"⚠️ Saved to Local (Fallback): {local_path}")
