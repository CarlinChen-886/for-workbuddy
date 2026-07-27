# 竞品礼盒优惠对比 · 在线生成器

上传电商礼盒/直播间主图 → OCR 自动提取赠品与优惠 → 人工校正 → 一键下载多品牌 17 列对比 Excel。

## 快速开始（本地运行）

```bash
pip install -r requirements-cloud.txt
python app.py --port 5055
```

打开 http://127.0.0.1:5055/

## 一键部署到 Railway

1. 把这个目录推到一个 GitHub 仓库
2. 打开 [railway.app](https://railway.app)，用 GitHub 登录
3. 点「New Project」→「Deploy from GitHub Repo」→ 选你的仓库
4. Railway 自动读取 `Procfile` 和 `requirements-cloud.txt`，几分钟后上线
5. 在 Railway 设置里点「Generate Domain」获得公网链接

**注意**：首次 OCR 调用会下载约 400MB 的模型文件，需要 1-2 分钟。Railway 免费额度每月 $5，大约够 200-500 次 OCR 调用。

## 一键部署到 Render

1. 推到 GitHub
2. 打开 [render.com](https://render.com)，创建「Web Service」
3. 连接你的 GitHub 仓库
4. Build Command: `pip install -r requirements-cloud.txt`
5. Start Command: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120`
6. 免费套餐会自动休眠，首次访问需等 30-60 秒唤醒

## 文件说明

| 文件 | 用途 |
|---|---|
| `app.py` | Flask 后端（OCR解析 + Excel生成） |
| `build_excel.py` | 17列多Sheet Excel 生成器 |
| `ocr_parser.py` | 品牌无关的图片OCR结构化解析 |
| `templates/index.html` | 苹果风格两页SPA前端 |
| `Procfile` | Railway/Render 进程定义 |
| `requirements-cloud.txt` | Python 依赖清单 |
