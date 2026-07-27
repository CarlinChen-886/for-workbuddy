"""Shopme 商品搜索 API 客户端（cn-ecommerce-search 后端）。

端点（来源：shopmeskills/mcp/packages/cn-ecommerce-search-mcp/src/shopme-client.ts）：
  POST /mcp/goods/search   按关键词搜索商品（淘宝/天猫/小红书）
  POST /mcp/goods/detail   取商品详情 + SKU 列表

需要 header：`x-mcp-caller: cn-ecommerce-search-mcp`（Cloudflare 验证字段，否则 403）。
"""
import os
import re
import json
import httpx

_BASE = os.environ.get("SHOPME_API_BASE", "https://api.shopmeagent.com").rstrip("/")
_CALLER_ID = "cn-ecommerce-search-mcp"
_TIMEOUT = float(os.environ.get("SHOPME_API_TIMEOUT", "20"))


def _post(path: str, body: dict, timeout: float = _TIMEOUT):
    try:
        r = httpx.post(
            f"{_BASE}{path}",
            json=body,
            headers={
                "Content-Type": "application/json",
                "x-mcp-caller": _CALLER_ID,
            },
            timeout=timeout,
        )
        return r.status_code, r.text
    except Exception as e:
        return 0, str(e)


def search_products(keyword: str, limit: int = 5):
    """搜商品并过滤异常价格（<50 或 >20000 视为套装/异常值）。

    返回 [{name, original_name, price, shop_name, platform, url, product_id}]。
    """
    code, text = _post("/mcp/goods/search", {"keyword": keyword, "limit": limit})
    if code != 200:
        return []
    try:
        d = json.loads(text)
    except Exception:
        return []
    out = []
    for it in (d.get("products") or []):
        try:
            price = float(it.get("price") or 0)
        except Exception:
            price = 0.0
        if price < 50 or price > 20000:  # 套装价/数据异常
            continue
        out.append({
            "name": it.get("name", ""),
            "original_name": it.get("original_name", ""),
            "price": price,
            "shop_name": it.get("shop_name", ""),
            "platform": it.get("platform", ""),
            "url": it.get("url", ""),
            "product_id": it.get("product_id", ""),
        })
    return out


def get_product_detail(product_id: str, platform: str = "taobao"):
    code, text = _post("/mcp/goods/detail",
                       {"product_id": product_id, "platform": platform})
    if code != 200:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


# 官方旗舰店的判定信号（最重要：店名必须含「官方旗舰店」才可信）
_OFFICIAL_HINTS = ["官方旗舰店", "官方旗舰"]


def _is_official(shop_name: str) -> bool:
    sn = shop_name or ""
    return any(k in sn for k in _OFFICIAL_HINTS)


def _detect_brand(text: str) -> str:
    """从主品名/活动文本里识别品牌"""
    t = (text or "").lower()
    rules = [
        ("兰蔻", ["兰蔻", "lancome", "lancôme"]),
        ("海蓝之谜", ["海蓝之谜", "la mer", "lamer"]),
        ("赫莲娜", ["赫莲娜", "hr", "helena rubinstein", "helena"]),
    ]
    for brand, keys in rules:
        for k in keys:
            if k.lower() in t:
                return brand
    return ""


def _clean_keyword(main_name: str, main_spec: str, brand: str) -> str:
    """把 OCR 可能残缺/带括号的主品名，整理成干净搜索词。

    - 去掉中文/英文括号及其内容（如「菁纯面霜（轻盈型」→「菁纯面霜」）
    - 去掉残留标点
    - 若主品名不含品牌词，则补上检测到的品牌（「兰蔻 菁纯面霜」）
    - 追加规格 ml（「30ml」）
    """
    name = main_name or ""
    name = re.split(r"[（(]", name)[0]               # 取首个括号前的内容（兼容「（轻盈型」未闭合）
    name = re.sub(r"[）)】\]「」]", " ", name)         # 去残留右括号/符号
    name = re.sub(r"\s+", " ", name).strip()
    if brand and brand.lower() not in name.lower():
        name = f"{brand} {name}"
    if main_spec:
        m = re.search(r"(\d+)\s*ml", str(main_spec), re.I)
        if m and f"{m.group(1)}ml" not in name:
            name = f"{name} {m.group(1)}ml"
    return name.strip()


def auto_lookup_main_price(main_name: str, main_spec: str = "", brand_hint: str = "", raw_text: str = "") -> dict:
    """根据 OCR 识别的主品名+规格，自动查官方正价。

    流程：Shopme API 搜（仅采信官方旗舰店报价）→ 无官旗结果则锚点库估算。

    关键安全规则：
      - 只认店名含「官方旗舰店」的报价为可信（status=ok）
      - 若搜索结果里没有官方旗舰店，绝不拿第三方/医美店的异常价顶替，
        而是回退到锚点库估算（status=anchor_estimate），让用户可见来源再确认。

    返回 {price, source, shop_name, url, name, status}：
      - status: ok / not_found / anchor_estimate
    """
    if not main_name and not raw_text:
        return {"price": 0.0, "source": "", "status": "not_found"}
    detected_brand = brand_hint or _detect_brand(main_name) or _detect_brand(raw_text)
    # 核心名（不含品牌、不含规格），用于组合多种搜索词
    core = _clean_keyword(main_name, "", "")
    m = re.search(r"(\d+)\s*ml", str(main_spec or ""), re.I)
    specml = f"{m.group(1)}ml" if m else ""
    # 候选搜索词：品牌×有无规格 多种组合。
    # 目的：抗 Shopme 返回抖动 + 抗 OCR 品牌识别偶发失败（任一命中官旗即采用）。
    variants = []
    if detected_brand:
        if specml:
            variants.append(f"{detected_brand} {core} {specml}")
        variants.append(f"{detected_brand} {core}")
    if specml:
        variants.append(f"{core} {specml}")
    variants.append(core)
    seen = set()
    variants = [v for v in variants if not (v in seen or seen.add(v))]
    # 1) Shopme API 搜：任一变体命中官方旗舰店即采用
    official_best = None
    for kw in variants:
        items = search_products(kw, limit=8)
        official = [x for x in items if _is_official(x["shop_name"])]
        if official:
            if specml:
                hit = next((x for x in official if specml in x["name"]), None)
                if hit:
                    official = [hit]
            official_best = official[0]
            break
    if official_best:
        return {
            "price": official_best["price"],
            "source": official_best["shop_name"],
            "shop_name": official_best["shop_name"],
            "url": official_best["url"],
            "name": official_best["name"],
            "status": "ok",
        }
    # 2) Fallback：没有官方旗舰店结果 → 用锚点库估算（不再采信第三方异常价）
    from anchors import lookup_main_price as _anchor_lookup
    fallback = _anchor_lookup(main_name, main_spec, detected_brand)
    if fallback.get("status") == "anchor_estimate":
        fallback["shop_name"] = "锚点估算（Shopme 无官旗结果）"
        fallback["url"] = ""
        return fallback
    return {"price": 0.0, "source": "未找到", "status": "not_found"}