#!/usr/bin/env python3
# 竞品礼盒 OCR 提取 · agent 侧命令行入口（贴图 → 自动出结构化 JSON 草稿）
#
# 用法:
#   python ocr_extract.py <图片1> [图片2 ...]
#   python ocr_extract.py /path/a.png /path/b.jpg
#
# 输出: 每张图的结构化草稿 JSON（main_product / gifts / promotion / final_price /
#       platform_coupons / period_logo / raw_text / needs），供 agent 补全
#       official_price、赠品估值、平台券金额拆分后，喂给 build_excel.py。
#
# 注: 图片读不到的字段（官方原价、赠品估值、平台券具体拆分金额）留空/待确认，
#     由 agent 用 WebSearch 官方价 + 结果页校正补齐。
import os, sys, json

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from ocr_parser import parse_image_ocr


def main():
    if len(sys.argv) < 2:
        print("用法: python ocr_extract.py <图片1> [图片2 ...]", file=sys.stderr)
        sys.exit(1)

    from rapidocr_onnxruntime import RapidOCR
    engine = RapidOCR()

    results = []
    for path in sys.argv[1:]:
        if not os.path.exists(path):
            results.append({"_error": f"文件不存在: {path}"})
            continue
        try:
            res, _ = engine(path)
        except Exception as e:
            results.append({"_error": f"OCR 失败 {path}: {e}"})
            continue
        parsed = parse_image_ocr(res) if res else parse_image_ocr([])
        parsed["_source_image"] = path
        results.append(parsed)

    # 多图时输出数组，单图时输出对象，方便管道处理
    out = results if len(results) > 1 else results[0]
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
