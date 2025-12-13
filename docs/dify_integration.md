# DeepDoc 接入 Dify 知识库指引

本文说明如何用 DeepDoc 解析后的结果接入 Dify 知识库（Dataset），并在回答中引用图片。

## 流程概览

1. 调用 DeepDoc `/parse` 获取结构化 JSON（`chunks` + 可选 `images`）。
2. 将 `images` 的 base64 保存/上传，得到 `image_url`。
3. 组装入库数据：每个段落用 `clean_text` 作为内容，metadata 存 `positions`、`image_refs` 等。
4. 调用 Dify Dataset API 导入段落，Dify 自动向量化。
5. 检索/回答时：从 metadata 取 `image_refs`，在回答里以 Markdown 链接/图片引用，前端渲染；如需图片描述，提前生成 caption 放 metadata，或用多模态模型。

## 1) 启动 DeepDoc 服务

```bash
cd /data/temp37/deepdoc
docker compose up -d --build
# 服务地址 http://localhost:8000
```

## 2) 解析文件获取 JSON

PDF 示例（需要图片就 `need_image=true`）：
```bash
curl -X POST \
  -F "file=@/path/to/your.pdf" \
  -F "use_ocr=true" \
  -F "need_image=true" \
  -F "max_chunks=200" \
  http://localhost:8000/parse -o resp.json
```

`resp.json` 关键字段：
- `chunks`：每段文本  
  - `clean_text`：去掉 `@@...##` 的纯文本（用于入库/embedding）  
  - `positions`：页码+坐标（用于定位/关联图片）  
  - `index`、`tag/layout_type` 等  
- `images`：可选，表格/图片裁剪  
  - `content`：base64 PNG  
  - `positions`：裁剪区域坐标  
  - `meta`：图注/表格描述（如有）

## 3) 处理图片（可选）

将 `images` 的 base64 保存/上传，得到 URL。示例（本地存文件）：
```python
import base64, json, pathlib

resp = json.load(open("resp.json"))
out = pathlib.Path("saved_imgs"); out.mkdir(exist_ok=True)
image_refs = []
for img in resp.get("images", []):
    b64 = img["content"].split(",")[1]
    path = out / f"img_{img['index']}.png"
    path.write_bytes(base64.b64decode(b64))
    image_refs.append({
        "index": img["index"],
        "url": str(path.resolve()),  # 实际可替换为 OSS/S3 外链
        "positions": img.get("positions"),
        "meta": img.get("meta"),
    })
```

## 4) 组装并导入 Dify Dataset

填入你的 Dify 配置：
- `DIFY_BASE`：Dify 服务地址，如 `http://your-dify-host`
- `DATASET_ID`：知识库 ID
- `API_KEY`：Dify API Key

示例脚本（按段导入）：
```python
import json, base64, pathlib, requests

DIFY_BASE = "http://your-dify-host"
DATASET_ID = "YOUR_DATASET_ID"
API_KEY = "YOUR_API_KEY"

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

def save_images(resp):
    out = pathlib.Path("saved_imgs"); out.mkdir(exist_ok=True)
    refs = []
    for img in resp.get("images", []):
        b64 = img["content"].split(",")[1]
        path = out / f"img_{img['index']}.png"
        path.write_bytes(base64.b64decode(b64))
        refs.append({
            "index": img["index"],
            "url": str(path.resolve()),  # 替换成可访问 URL
            "positions": img.get("positions"),
            "meta": img.get("meta"),
        })
    return refs

def push_segment(text, meta):
    url = f"{DIFY_BASE}/v1/datasets/{DATASET_ID}/document-segments"
    payload = {"segments": [{"text": text, "metadata": meta}]}
    r = requests.post(url, json=payload, headers=headers, timeout=30)
    r.raise_for_status()

def main():
    resp = json.load(open("resp.json"))
    image_refs = save_images(resp)  # 如果无图，可跳过

    for ck in resp.get("chunks", []):
        text = ck.get("clean_text") or ck.get("text") or ""
        meta = {
            "file_name": resp.get("file_name", ""),
            "chunk_index": ck.get("index"),
            "positions": ck.get("positions"),
            "image_refs": image_refs,  # 可按页/坐标筛选后挂子集
        }
        push_segment(text, meta)
    print("imported", len(resp.get("chunks", [])), "segments")

if __name__ == "__main__":
    main()
```

运行：
```bash
python import_to_dify.py
```

每个段落会作为一个 segment 写入 Dify，metadata 带上 `positions` 和图片引用。

## 5) 检索/回答时引用图片

- Dify 检索基于文本 embedding；命中后可取 metadata。
- 业务层/工作流读取 metadata 的 `image_refs`（URL/positions/meta），在最终回答里附上 Markdown 链接/图片：
  ```
  相关图片：
  - ![图1](https://.../img_0.png)
  - ![表格](https://.../img_1.png)
  ```
- 文本模型不会读图，只是引用链接；若需理解图片内容，需多模态模型或提前生成 caption 放 metadata。

## 6) 小结

- DeepDoc：负责解析，产出 `clean_text`（入库文本）、`positions`（定位）、`images`（可选）。  
- 图片：先存储，metadata 仅存 URL/坐标/描述。  
- 导入 Dify：`text=clean_text`，`metadata` 带 `positions`/`image_refs`。  
- 回答：从 metadata 取图片链接，拼入答案，前端渲染；需要读图则用多模态或预生成 caption。
