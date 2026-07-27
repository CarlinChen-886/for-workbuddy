#!/usr/bin/env python3
# 竞品礼盒优惠对比 · 在线生成器（Flask 后端 · v3）
# 流程：前端只传图片+日期 → /api/extract (OCR+解析→结构化 JSON) → 用户在 Page2 可编辑
#       （含平台券/品牌/官方价锚点）→ /api/generate 出 xlsx
import os, json, tempfile, sys, re
from flask import Flask, request, send_file, render_template, jsonify

# 复用 Excel 生成逻辑 + OCR 解析（同级目录，支持本地和云部署）
from build_excel import build_workbook
from ocr_parser import parse_image_ocr
from shopme import auto_lookup_main_price
from anchors import compute_gift_value

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # 64 MB / upload

# OCR 引擎懒加载（首请求才初始化，约 1-2s）
_OCR_ENGINE = None
def _get_ocr():
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/extract", methods=["POST"])
def api_extract():
    """接收多张图片+用户为每图选的日期，并发运行 OCR + 解析，返回结构化 JSON"""
    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from threading import Lock
        files = request.files.getlist("images")
        dates = request.form.getlist("dates")
        names = request.form.getlist("names") or []
        brands = request.form.getlist("brands") or []
        if not files:
            return jsonify({"ok": False, "error": "未收到图片"}), 400

        tmpdir = tempfile.mkdtemp(prefix="giftcmp_")
        ocr = _get_ocr()
        ocr_lock = Lock()

        # 先保存文件（轻量）+ 并行OCR
        tasks = []
        for i, f in enumerate(files):
            if not f.filename:
                continue
            ext = os.path.splitext(f.filename)[1].lower() or ".png"
            if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                ext = ".png"
            path = os.path.join(tmpdir, f"img_{i}{ext}")
            f.save(path)
            tasks.append((i, f, path, ext))

        def _ocr_one(i, f, path, ext):
            parsed = {"main_product": {"name": "", "spec": "", "raw": "", "ml": 0},
                      "gifts": [], "promotion": {"type": "", "amount": 0.0, "condition": "", "raw": ""},
                      "final_price": 0.0, "platform_coupons": [], "period_logo": "",
                      "raw_text": "", "needs": [],
                      "merchant_subsidies": [],
                      "merchant_coupons": [],
                      "shopping_funds": [],
                      "claimed_total_discount": 0.0}
            try:
                with ocr_lock:  # rapidocr 非线程安全，必须串行调用
                    res, _ = ocr(path)
                if res:
                    with ocr_lock:  # parse 也可能用临界资源
                        parsed = parse_image_ocr(res)
                    platform_ocr_total = sum(c.get("amount", 0) or 0 for c in parsed.get("platform_coupons", []))
                    promo_ocr_amount = parsed.get("promotion", {}).get("amount", 0) or 0
                    parsed["claimed_total_discount"] = round(platform_ocr_total + promo_ocr_amount, 2)
                    parsed.setdefault("merchant_subsidies", [])
                    parsed.setdefault("merchant_coupons", [])
                    parsed.setdefault("shopping_funds", [])
                    # ── 全自动：OCR 后立即查主品官方正价 + 用锚点库算赠品价值 ──
                    try:
                        mn = parsed.get("main_product", {}).get("name", "")
                        ms = parsed.get("main_product", {}).get("spec", "")
                        parsed["official_price_auto"] = auto_lookup_main_price(mn, ms, raw_text=parsed.get("raw_text", ""))
                    except Exception as e:
                        parsed["official_price_auto"] = {"price": 0, "source": f"自动查价异常:{str(e)[:60]}", "status": "error"}
                    for g in parsed.get("gifts", []):
                        try:
                            v = compute_gift_value(g.get("name", ""), g.get("spec", ""))
                            g["value_auto"] = v.get("value", 0)
                            g["value_formula"] = v.get("formula", "")
                            g["value_status"] = v.get("status", "no_anchor")
                        except Exception as e:
                            g["value_auto"] = 0
                            g["value_formula"] = f"自动算异常:{str(e)[:60]}"
                            g["value_status"] = "error"
            except Exception as e:
                parsed["needs"] = ["OCR识别失败：" + str(e)[:80]]
            return i, parsed, path, ext

        # 并发：max_workers=2（有锁时够了，主要是并行存文件+改格式）
        results = [None] * len(tasks)
        with ThreadPoolExecutor(max_workers=2) as ex:
            futures = {ex.submit(_ocr_one, i, f, path, ext): (i, path, ext) for i, f, path, ext in tasks}
            for fut in as_completed(futures):
                try:
                    i, parsed, path, ext = fut.result(timeout=120)
                except Exception as e:
                    i, _, path, ext = futures[fut]
                    parsed = {"main_product": {"name": "", "spec": "", "raw": "", "ml": 0},
                              "gifts": [], "promotion": {"type": "", "amount": 0.0, "condition": "", "raw": ""},
                              "final_price": 0.0, "platform_coupons": [], "period_logo": "",
                              "raw_text": "", "needs": [f"OCR超时：" + str(e)[:60]],
                              "merchant_subsidies": [], "merchant_coupons": [], "shopping_funds": [],
                              "claimed_total_discount": 0.0}
                results[i] = (parsed, path, ext)

        items = []
        for result in results:
            if result is None:
                continue  # skip failed tasks that didn't produce a result
            parsed, path, ext = result
            i = len(items)  # re-index sequentially
            items.append({
                "idx": i,
                "name": names[i] if i < len(names) and names[i] else "",
                "brand": brands[i] if i < len(brands) and brands[i] else "",
                "date": dates[i] if i < len(dates) else "",
                "image_path": path,
                "image_url": f"/img/{i}.{ext.lstrip('.')}",
                "parsed": parsed,
            })

        return jsonify({"ok": True, "tmpdir": tmpdir, "items": items})
    except Exception as e:
        # 确保任何异常都返回 JSON，不泄漏 HTML 到前端
        return jsonify({"ok": False, "error": f"服务器错误：{str(e)[:200]}"}), 500


@app.route("/img/<path:fn>")
def img(fn):
    """让前端预览刚上传的图（仅限本次 tmpdir）"""
    tmpdir = request.args.get("d", "")
    if not (tmpdir.startswith("/tmp") or tmpdir.startswith("/var/folders/")):
        return ("forbidden", 403)
    return send_file(os.path.join(tmpdir, fn))


def _normalize_benefit_items(items, source_default="人工补充"):
    """将前端传来的权益条目标准化为 (name, amount, threshold, source, counted) 列表"""
    out = []
    for it in (items or []):
        try:
            amt = float(it.get("amount", 0) or 0)
        except Exception:
            amt = 0.0
        counted = it.get("count_toward_discount", True)
        if isinstance(counted, str):
            counted = counted.lower() in ("true", "1", "yes", "on")
        out.append({
            "name": it.get("name", ""),
            "amount": amt,
            "threshold": it.get("threshold", ""),
            "source": it.get("source", source_default),
            "count_toward_discount": bool(counted),
        })
    return [x for x in out if x["name"] or x["amount"] > 0]


def _parsed_to_excel_product(item):
    """把前端传来的（可编辑过的）parsed JSON 转换成 build_workbook 需要的 product 格式"""
    p = item.get("parsed", {})
    main = p.get("main_product", {})
    gifts_in = p.get("gifts", [])
    prom = p.get("promotion", {})
    plat_in = item.get("platform_coupons")
    if plat_in is None:
        plat_in = p.get("platform_coupons", [])

    # 商家货补 / 商家券 / 购物金（前端透传或 item 侧有）
    subs = _normalize_benefit_items(item.get("merchant_subsidies") or p.get("merchant_subsidies"))
    mcps = _normalize_benefit_items(item.get("merchant_coupons") or p.get("merchant_coupons"))
    sfs  = _normalize_benefit_items(item.get("shopping_funds") or p.get("shopping_funds"))

    gifts = []
    for g in gifts_in:
        name = g.get("name", "")
        # 赠品价值：优先用户手动填（value_user），否则用锚点库自动算（value_auto）
        user_v = g.get("value_user")
        auto_v = g.get("value_auto", 0) or 0
        try:
            value = float(user_v if user_v not in (None, "") else auto_v)
        except Exception:
            value = float(auto_v)
        gifts.append({
            "name": name + (" [工具]" if g.get("is_tool") else ""),
            "spec": g.get("spec", ""),
            "value_formula": g.get("value_formula", ""),
            "value": value if not g.get("is_tool") else 0.0,
        })

    try:
        official = float(item.get("official_price", 0) or 0)
    except Exception:
        official = 0.0
    # 主品官方正价：优先用户在前端手动填的（official_price_user），否则用 OCR 后自动查的 official_price_auto
    user_official = item.get("official_price_user")
    auto_official = (p.get("official_price_auto") or {}).get("price", 0) or 0
    try:
        official = float(user_official if user_official not in (None, "") else (item.get("official_price") or auto_official))
    except Exception:
        official = float(item.get("official_price") or auto_official or 0)
    try:
        final = float(item.get("final_price", p.get("final_price", 0)) or 0)
    except Exception:
        final = float(p.get("final_price", 0) or 0)
    try:
        promo_amt = float(prom.get("amount", 0) or 0)
    except Exception:
        promo_amt = 0.0

    # 构建商家优惠罗列文本（合并：货补 + 商家券 + 购物金 + 原有优惠）
    promo_lines = []
    if prom.get("type") or prom.get("raw") or promo_amt:
        promo_lines.append(f'{prom.get("type") or "限时优惠"}：¥{promo_amt}（{prom.get("condition") or prom.get("raw","")}）')
    for x in subs:
        tag = "计入" if x["count_toward_discount"] else "不计入"
        promo_lines.append(f'商家货补-{x["name"]}：¥{x["amount"]}（{x["threshold"] or "无门槛"}，来源：{x["source"]}，{tag}折扣）')
    for x in mcps:
        tag = "计入" if x["count_toward_discount"] else "不计入"
        promo_lines.append(f'商家券-{x["name"]}：¥{x["amount"]}（{x["threshold"] or "无门槛"}，来源：{x["source"]}，{tag}折扣）')
    for x in sfs:
        tag = "计入" if x["count_toward_discount"] else "不计入"
        promo_lines.append(f'购物金/储值-{x["name"]}：¥{x["amount"]}（{x["threshold"] or "无门槛"}，来源：{x["source"]}，{tag}折扣）')
    merchant_benefits_text = "\n".join(promo_lines) if promo_lines else ""

    # 扩展 promotions 列表让 _fill_sheet 原生展示
    extended_promotions = []
    if prom.get("type") or prom.get("raw") or final or promo_amt:
        extended_promotions.append({
            "type": prom.get("type") or "限时优惠",
            "amount": promo_amt,
            "condition": prom.get("condition") or prom.get("raw", ""),
        })
    for x in subs:
        tag = "计入折扣" if x["count_toward_discount"] else "不计入折扣"
        extended_promotions.append({
            "type": f'商家货补-{x["name"]}',
            "amount": x["amount"],
            "condition": f'{x["threshold"] or "无门槛"}，来源：{x["source"]}，{tag}',
        })
    for x in mcps:
        tag = "计入折扣" if x["count_toward_discount"] else "不计入折扣"
        extended_promotions.append({
            "type": f'商家券-{x["name"]}',
            "amount": x["amount"],
            "condition": f'{x["threshold"] or "无门槛"}，来源：{x["source"]}，{tag}',
        })
    # 购物金不进入 promotions，由 shopping_credits 单独展示（build_excel.py 的 _fill_sheet 已支持）
    sf_total = sum(x.get("amount", 0) for x in sfs if isinstance(x, dict))
    sc_dict = None
    if sfs and sf_total > 0:
        names = [f'{x.get("name","")}（{x.get("threshold","") or "无门槛"}，来源：{x.get("source","")}）' for x in sfs if x.get("name")]
        sc_dict = {
            "type": "购物金/储值权益",
            "amount": sf_total,
            "condition": "; ".join(names) if names else "人工补充",
            "note": "规则10：储值权益完全不计入折扣率，仅信息展示",
        }

    product = {
        "brand": item.get("brand", "") or "",
        "name": item.get("name") or main.get("name", ""),
        "main_product": main.get("name", "") + (" " + main.get("spec", "") if main.get("spec") else ""),
        "main_composition": main.get("raw", "") or main.get("name", ""),
        "period": p.get("period_logo", ""),
        "logo_text": p.get("period_logo", ""),
        "official_price": official,
        "upload_date": item.get("date", ""),
        "gifts": gifts,
        "gift_total_value": round(sum(x["value"] for x in gifts), 2),
        "promotions": extended_promotions if extended_promotions else (
            [{"type": prom.get("type") or "限时优惠", "amount": promo_amt,
              "condition": prom.get("condition") or prom.get("raw", "")}]
            if (prom.get("type") or prom.get("raw") or final or promo_amt) else []),
        "final_price": final,
        "platform_coupons": [
            {"name": c.get("name", "平台券"), "amount": float(c.get("amount", 0) or 0),
             "condition": c.get("condition", "")}
            for c in plat_in
        ],
        "image_path": item.get("image_path", ""),
        "shopping_credits": sc_dict,
    }
    return product


@app.route("/api/generate", methods=["POST"])
def api_generate():
    """接收前端传回的（已编辑过的）解析结果 → 生成 Excel（支持平台券 / 品牌分组 / 官方价锚点）"""
    data = request.get_json(force=True) or {}
    items = data.get("items", [])
    if not items:
        return jsonify({"ok": False, "error": "无商品数据"}), 400

    products = [_parsed_to_excel_product(it) for it in items]

    anchors_specs = {}
    if data.get("anchors_text"):
        for i, line in enumerate((data.get("anchors_text") or "").splitlines()):
            line = line.strip()
            if line:
                anchors_specs[f"锚点{i+1}"] = line

    excel_data = {"anchors_specs": anchors_specs, "products": products}
    wb = build_workbook(excel_data)

    outdir = tempfile.mkdtemp(prefix="giftcmp_out_")
    out_path = os.path.join(outdir, "竞品优惠对比分析.xlsx")
    wb.save(out_path)
    return send_file(
        out_path,
        as_attachment=True,
        download_name="竞品优惠对比分析.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if __name__ == "__main__":
    import argparse, os
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 5055)))
    parser.add_argument("--host", type=str, default="0.0.0.0")
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, threaded=True)
