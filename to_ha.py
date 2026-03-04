import os
import glob
from hdfs import InsecureClient

# --- [1. 설정] ---
# 경로 설정
TARGET_DATE = "20260303"
LOCAL_IMG_DIR = f"./data/zara_processed/{TARGET_DATE}/image"

# 하둡(HDFS) 설정
HDFS_IMAGE_DIR = "/raw/zara/image"                     
HDFS_WEB_URL = "http://localhost:8906" # 네임노드 Web UI 포트 (환경에 맞게 수정)

# --- [2. 기능 1: 이미지 HDFS 업로드] ---
def upload_images_to_hdfs():
    print(f"\n🚀 로컬 이미지를 HDFS로 업로드 시작... (경로: {LOCAL_IMG_DIR})")
    
    try:
        # HDFS 클라이언트 연결
        client = InsecureClient(HDFS_WEB_URL, user="root")
        
        # HDFS에 타겟 디렉토리가 없으면 생성
        client.makedirs(HDFS_IMAGE_DIR)
        
        # 로컬 이미지 파일 목록 조회
        image_files = glob.glob(f"{LOCAL_IMG_DIR}/*.jpg")
        
        if not image_files:
            print("   ⚠️ 업로드할 이미지 파일이 없습니다.")
            return

        success_cnt = 0
        for local_path in image_files:
            file_name = os.path.basename(local_path)
            hdfs_dest_path = f"{HDFS_IMAGE_DIR}/{file_name}"
            
            # overwrite=True로 덮어쓰기 허용
            client.upload(hdfs_dest_path, local_path, overwrite=True)
            success_cnt += 1
                
        print(f"   ✅ HDFS 업로드 완료! (총 {success_cnt}건 저장됨 -> {HDFS_IMAGE_DIR})")
        
    except Exception as e:
        print(f"   ❌ HDFS 연결/업로드 에러: {e}")

# --- [3. 실행 부] ---
if __name__ == "__main__":
    print(f"=== 데이터 파이프라인 적재 작업 시작 (대상일자: {TARGET_DATE}) ===")
    upload_images_to_hdfs()
    print("\n🎉 모든 작업이 완료되었습니다!")
