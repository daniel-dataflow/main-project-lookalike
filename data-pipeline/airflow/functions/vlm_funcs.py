import os
import json
import gc
from pathlib import Path
from hdfs import InsecureClient
from ollama import Client

def parse_filename_category(filename):
    try:
        parts = filename.split('.')[0].split('_')
        brand = parts[0] if len(parts) > 0 else "Unknown"
        product_code = parts[3] if len(parts) > 3 else "Unknown"
        return {
            "brand": brand,
            "gender": parts[1] if len(parts) > 1 else "Unknown",
            "category_code": parts[2] if len(parts) > 2 else "Unknown",
            "product_code": product_code,
            "product_id": f"{brand}_{product_code}"
        }
    except:
        return {}

def process_vlm_extraction(brand_name: str, hdfs_url: str, ollama_host: str, model_name: str, out_dir: str) -> list[str]:
    hdfs_client = InsecureClient(hdfs_url, user='root')
    ollama_client = Client(host=ollama_host)
    hdfs_path = f"/raw/{brand_name}/image"
    
    out_root = Path(out_dir) / brand_name
    out_root.mkdir(parents=True, exist_ok=True)
    out_paths = []

    try:
        images = [os.path.join(hdfs_path, f) for f in hdfs_client.list(hdfs_path) if f.lower().endswith(('.jpg', '.png', '.jpeg'))]
    except Exception as e:
        print(f"❌ HDFS 접속 실패: {e}")
        return []

    total_imgs = len(images)
    print(f"🚀 Ollama 가동 ({model_name}) - 총 {total_imgs}장 분석 시작")

    for idx, img_path in enumerate(images, 1):
        filename = os.path.basename(img_path)
        basic_info = parse_filename_category(filename)
        product_id = basic_info.get("product_id")
        
        save_path = out_root / f"{product_id}_vlm.json"
        if save_path.exists():
            print(f"⏭️ [{idx}/{total_imgs}] [건너뜀] 이미 분석된 파일: {product_id}")
            out_paths.append(str(save_path))
            continue

        status = hdfs_client.status(img_path)
        if status.get('length', 0) < 5120: continue

        with hdfs_client.read(img_path) as reader:
            img_bytes = reader.read()

        # Ollama 프롬프트 (기존과 동일)
        messages = [{'role': 'system', 'content': '출력은 반드시 JSON 포맷으로 작성. search_text는 한국어 1문장.'},
                    {'role': 'user', 'content': f'카테고리: {basic_info.get("category_code")}. JSON 형식으로만 작성.', 'images': [img_bytes]}]
        
        try:
            res = ollama_client.chat(model=model_name, messages=messages, options={'temperature': 0.1})
            clean_json = res['message']['content'].replace("```json", "").replace("```", "").strip()
            clean_json = clean_json[clean_json.find("{"):clean_json.rfind("}")+1]
            parsed_data = json.loads(clean_json)

            doc = {
                "product_id": product_id,
                "basic_info": basic_info,
                "analysis": parsed_data
            }
            
            # 중간 결과 JSON 파일로 즉시 저장
            save_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            out_paths.append(str(save_path))
            print(f"✅ [{idx}/{total_imgs}] VLM 분석 완료: {product_id}")

        except Exception as e:
            print(f"❌ [{idx}/{total_imgs}] 에러 발생: {filename} - {e}")
            continue
        finally:
            gc.collect()

    return out_paths
