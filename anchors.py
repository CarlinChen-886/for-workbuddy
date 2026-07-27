"""赠品价值自动计算：OCR 识别赠品 → 匹配锚点库（已知品牌正装最高规格单 ml 价）→ ml × 单价。

数据内嵌（来源：data_all.json anchors_specs；规则1/2·天猫官旗优先 / 京东自营兜底）。
"""
import re

# 锚点库：key = 标准产品名（含规格），value = (单 ml 价 ¥/ml, 来源描述)
# 多 key 是为了兼容 OCR 的不同写法（去掉品牌前缀/缩写的核心词也能命中）。
ANCHORS = {
    # ── 兰蔻 ──
    "菁纯面霜(轻盈型)":       (40.92, "兰蔻菁纯面霜60ml ¥2455·天猫官旗"),
    "菁纯面霜":                (40.92, "兰蔻菁纯面霜60ml ¥2455·天猫官旗"),
    "全新兰蔻菁纯面霜":       (40.92, "兰蔻菁纯面霜60ml ¥2455·天猫官旗"),
    "菁纯精华水":              (7.47,  "兰蔻菁纯精华水150ml ¥1120·天猫官旗"),
    "全新菁纯精华水":         (7.47,  "兰蔻菁纯精华水150ml ¥1120·天猫官旗"),
    "菁纯眼霜":                (60.00, "兰蔻菁纯眼霜20ml ¥1200·天猫官旗"),
    "菁纯洁面乳":              (7.20,  "兰蔻菁纯洁面乳125ml ¥900·天猫官旗"),
    "全新菁纯洁面乳":         (7.20,  "兰蔻菁纯洁面乳125ml ¥900·天猫官旗"),
    "菁纯眼部按摩棒":          (0.0,   "非官方店在售工具→¥0（规则3）"),

    # ── 海蓝之谜 ──
    "LA MER修护精萃水":        (7.73,  "LA MER精萃水150ml ¥1160·天猫官旗"),
    "修护精萃水":              (7.73,  "LA MER精萃水150ml ¥1160·天猫官旗"),
    "精萃水":                  (7.73,  "LA MER精萃水150ml ¥1160·天猫官旗"),
    "油皮精萃水":              (7.73,  "LA MER精萃水150ml ¥1160·天猫官旗"),
    "LA MER碧玺焕亮洁面泡沫":  (7.20,  "LA MER碧玺洁面125ml ¥900·天猫官旗"),
    "碧玺焕亮洁面泡沫":        (7.20,  "LA MER碧玺洁面125ml ¥900·天猫官旗"),
    "碧玺洁面":                (7.20,  "LA MER碧玺洁面125ml ¥900·天猫官旗"),
    "LA MER云绒霜":            (54.08, "LA MER云绒霜60ml ¥3245·天猫官旗"),
    "云绒霜":                  (54.08, "LA MER云绒霜60ml ¥3245·天猫官旗"),
    "LA MER精华面霜":          (49.90, "LA MER奇迹面霜100ml ¥4990·京东自营"),
    "LA MER精华面霜(奇迹面霜)": (49.90, "LA MER奇迹面霜100ml ¥4990·京东自营"),
    "精华面霜":                (49.90, "LA MER奇迹面霜100ml ¥4990·京东自营"),
    "奇迹面霜":                (49.90, "LA MER奇迹面霜100ml ¥4990·京东自营"),
    "LA MER全新奇迹眼霜":      (132.00,"LA MER全新奇迹眼霜15ml ¥1980·京东自营"),
    "全新奇迹眼霜":            (132.00,"LA MER全新奇迹眼霜15ml ¥1980·京东自营"),
    "奇迹眼霜":                (132.00,"LA MER全新奇迹眼霜15ml ¥1980·京东自营"),
    "LA MER浓缩修护精华露":    (89.60, "LA MER浓缩精华50ml ¥4480"),
    "浓缩修护精华":            (89.60, "LA MER浓缩精华50ml ¥4480"),
    "浓缩修护精华露":          (89.60, "LA MER浓缩精华50ml ¥4480"),
    "浓缩精华":                (89.60, "LA MER浓缩精华50ml ¥4480"),
    "浓修瓶精华":              (89.60, "LA MER浓缩精华50ml ¥4480"),
    "LA MER奇迹晚霜":          (57.90, "LA MER奇迹晚霜100ml ¥5790·京东自营"),
    "奇迹晚霜":                (57.90, "LA MER奇迹晚霜100ml ¥5790·京东自营"),

    # ── 赫莲娜 ──
    "HR黑绷带[50]面霜":        (127.60,"HR赫莲娜黑绷带50ml ¥6380·天猫官旗"),
    "HR黑绷带":                (127.60,"HR赫莲娜黑绷带50ml ¥6380·天猫官旗"),
    "黑绷带":                  (127.60,"HR赫莲娜黑绷带50ml ¥6380·天猫官旗"),
    "全新黑绷带[50]面霜":      (127.60,"HR赫莲娜黑绷带50ml ¥6380·天猫官旗"),
    "HR新一代白绷带面霜":      (65.60, "HR赫莲娜白绷带50ml ¥3280·天猫官旗"),
    "白绷带":                  (65.60, "HR赫莲娜白绷带50ml ¥3280·天猫官旗"),
    "HR小露珠饱满水":          (8.45,  "HR小露珠饱满水200ml ¥1690·天猫官旗"),
    "小露珠饱满水":            (8.45,  "HR小露珠饱满水200ml ¥1690·天猫官旗"),
    "小露珠":                  (8.45,  "HR小露珠饱满水200ml ¥1690·天猫官旗"),
    # 待补
    "HR绿宝瓶精华":            (0.0,   "HR绿宝瓶精华待补（价格未锚定）"),
    "第六代绿宝瓶精华":        (0.0,   "HR绿宝瓶精华待补（价格未锚定）"),
    "绿宝瓶":                  (0.0,   "HR绿宝瓶精华待补（价格未锚定）"),
    "HR纯净沁润洁面泡沫":      (0.0,   "HR洁面待补"),
}


def _extract_ml(spec) -> float:
    """从规格文本提取总 ml（支持 '30ml × 2' / '15ml×1' / '5ml'）"""
    if not spec:
        return 0.0
    s = str(spec).replace(" ", "").replace("×", "x").replace("*", "x").replace("X", "x")
    m = re.search(r"(\d+(?:\.\d+)?)ml[xX](\d+)", s, re.I)
    if m:
        return float(m.group(1)) * float(m.group(2))
    m = re.search(r"(\d+(?:\.\d+)?)ml", s, re.I)
    if m:
        return float(m.group(1))
    return 0.0


_BRAND_PREFIX = re.compile(r"^(LA\s*MER|HR|兰蔻|海蓝之谜|赫莲娜|\(?LA MER\)?|\[|\(|\s|）|】)", re.I)


def _normalize(s: str) -> str:
    return _BRAND_PREFIX.sub("", re.sub(r"[\[\]【】（）()]", "", s)).replace(" ", "").lower()


def _match_anchor(gift_name: str):
    """模糊匹配赠品名 → 锚点。返回 (unit_price, source) 或 (0.0, '')。"""
    if not gift_name:
        return 0.0, ""
    gn = _normalize(gift_name)
    # 1) 精确/包含匹配
    for k, v in ANCHORS.items():
        kn = _normalize(k)
        if kn and (kn in gn or gn in kn):
            return v
    # 2) 核心词匹配（去掉品牌前缀后 ≥ 3 个字符的子串）
    for k, v in ANCHORS.items():
        kn = _normalize(k)
        if len(kn) >= 3 and kn in gn:
            return v
    return 0.0, ""


def compute_gift_value(gift_name: str, gift_spec) -> dict:
    """自动算赠品价值。

    返回 {value, formula, source, status}
    status: ok / no_ml / no_anchor
    """
    ml = _extract_ml(gift_spec)
    if ml <= 0:
        return {"value": 0.0, "formula": "", "status": "no_ml"}
    unit, source = _match_anchor(gift_name)
    if unit <= 0:
        return {"value": 0.0, "formula": "", "status": "no_anchor"}
    value = round(unit * ml, 2)
    formula = f"{ml:g}ml × ¥{unit:.2f}/ml = ¥{value:.2f}（{source}）"
    return {"value": value, "formula": formula, "source": source, "status": "ok"}

def lookup_main_price(main_name: str, main_spec, brand_hint: str = "") -> dict:
    """按锚点库估算主品正价：OCR 识别规格 ml × 锚点单 ml 价。

    适用：
      - 唯一规格产品（如 LA MER 全新奇迹眼霜 15ml ¥1980）→ 准确
      - 多规格产品（30/60/100ml 价格非线性）→ 仅作兜底估算
    """
    ml = _extract_ml(main_spec)
    if ml <= 0:
        return {"price": 0.0, "source": "", "status": "no_ml"}
    candidates = [(k, v) for k, v in ANCHORS.items() if v[0] > 0]
    if brand_hint:
        from_key = {
            "兰蔻": ["兰蔻", "lancome"],
            "海蓝之谜": ["海蓝之谜", "la mer", "lamer"],
            "赫莲娜": ["赫莲娜", "hr", "helena rubinstein"],
        }.get(brand_hint, [])
        filtered = [(k, v) for k, v in candidates
                    if any(kw.lower() in v[1].lower() or kw.lower() in k.lower() for kw in from_key)]
        if filtered:
            candidates = filtered
    unit, source = _match_anchor(main_name)
    if unit <= 0 and candidates:
        unit, source = candidates[0][1]
    if unit <= 0:
        return {"price": 0.0, "source": "", "status": "no_anchor"}
    value = round(unit * ml, 2)
    return {
        "price": value,
        "source": source,
        "status": "anchor_estimate",
        "note": "Shopme 未搜到，按锚点库单 ml 价估算（多规格产品可能不精确，请确认）",
    }
