import json
import os
import re

# 1. 몽고DB에서 내보낸 합쳐진 파일 이름
input_file = 'data.json' 

# 2. 저장할 폴더 이름
output_dir = 'extracted_files'
os.makedirs(output_dir, exist_ok=True)

# 3. 파일 읽기 및 분리 저장
with open(input_file, 'r', encoding='utf-8') as f:
    try:
        data = json.load(f)
        
        for i, doc in enumerate(data):
            # 문서 내 'filename' 필드 확인 (없으면 'no_name_index' 사용)
            raw_name = doc.get('filename', f"document_{i}")
            
            # 파일명으로 쓸 수 없는 문자 제거 (\ / : * ? " < > |)
            clean_name = re.sub(r'[\\/:*?"<>|]', '_', str(raw_name))
            
            # .json 확장자가 이미 붙어있지 않다면 추가
            if not clean_name.lower().endswith('.json'):
                clean_name += '.json'
                
            file_path = os.path.join(output_dir, clean_name)
            
            # 개별 파일로 저장
            with open(file_path, 'w', encoding='utf-8') as out_f:
                json.dump(doc, out_f, indent=4, ensure_ascii=False)
                
        print(f"✅ 총 {len(data)}개의 파일이 '{output_dir}' 폴더에 저장되었습니다.")

    except json.JSONDecodeError:
        print("❌ 파일 형식이 JSON 배열 형식이 아닙니다. 내보낼 때 'JSON Array'를 선택했는지 확인해 주세요.")