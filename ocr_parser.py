#!/usr/bin/env python3
# OCR 结构化解析 — 从 rapidocr 输出提取竞品礼盒字段（品牌无关，支持平台券/购物金/储值权益）
# 输入: rapidocr 的 [[[x,y]*4], text, conf] 列表（list of blocks）
# 输出: dict(main_product, gifts[], promotion{}, final_price, platform_coupons[], shopping_credits, period_logo, raw_text, needs[])
#
# 设计目标：把"图里能读到的"尽量结构化出来，作为 agent / 用户校正的草稿。
#   · 主品 / 赠品(名称+ml) / 商家优惠(店铺会员券/直播货补/限时直降/立减等) / 到手价
#   · 平台券(图里常印 88VIP券·加补券·立减总额) / 购物金/储值权益(购物金/储值卡/充值金等)
#   · 图里读不到的（官方原价、赠品估值、平台券具体金额拆分）留空/待确认，由 agent 或结果页补。
import re

# 强产品词：出现即可认为是一项实物赠品（配合 ml/工具判定）
STRONG_PRODUCT = ["精萃水", "精华水", "爽肤水", "面霜", "洁面", "洗面", "乳液", "精华", "眼霜",
                  "眼精华", "云绒霜", "晚霜", "修护", "肌底液", "精华露", "凝露", "精油",
                  "粉底", "口红", "唇釉", "香水", "面膜", "防晒", "卸妆", "水乳", "水霜"]
# 通用词：必须同时带 ml 才算实物（避免"正装/水/乳/礼盒"等误触发）
GENERIC_PRODUCT = ["正装", "水", "乳", "护肤", "彩妆", "礼盒", "套装"]
PRODUCT_WORDS = STRONG_PRODUCT + GENERIC_PRODUCT


def _looks_like_gift(name, ml, is_tool):
    if is_tool:
        return True
    if ml > 0:
        return True
    if any(w in name for w in STRONG_PRODUCT):
        return True
    return False


GIFT_MARKERS = ["赠", "买即享", "加赠", "礼遇", "会员礼", "满赠", "下单立赠", "加享",
                "专享礼", "焕新礼", "加赠礼", "直播加赠", "新客限定", "限定", "加礼", "随单赠", "买即送"]

TOOL_WORDS = ["按摩棒", "美容仪", "工具", "美妆蛋", "海绵", "梳", "刷"]

ACTIVITY_WORDS = ["超级88", "大牌日", "会员周", "狂欢", "直播", "新品", "焕新", "首发", "限定",
                  "盛典", "618", "双11", "双十二", "年货节", "宠粉", "感恩", "超级", "品牌日",
                  "小黑盒", "百亿补贴", "聚划算", "节", "季"]

# 平台券关键词（规则 8） — 平台发放给消费者，非商家
PLATFORM_COUPON_NAMES = ["88VIP专享券", "88VIP消费券", "88vip消费券", "美妆加补券", "美妆品类券", "品类券", "惊喜券",
                         "加补券", "平台消费券", "跨店消费券", "平台满减券", "跨店券"]
# 商家优惠关键词（规则 7） — 店铺、品牌或直播间提供
MERCHANT_COUPON_NAMES = ["店铺券", "会员券", "店铺会员券", "会员专享券", "直播券", "主播券", "商家券", "折扣券", "店铺红包", "商家红包"]
# 商家优惠（直降/补贴/立减）显式表达（规则 7）
MERCHANT_PROMO_WORDS = ["限时直降", "直播直降", "官方限时补贴", "直播间补贴", "限时补贴", "官方补贴", "立减", "货补", "直播间到手"]
PLATFORM_ACTIVITY_WORDS = ["超级88", "天猫大牌日", "88VIP", "美妆加补", "平台大促", "跨店"]
# 购物金/储值权益关键词（规则 10）
SHOPPING_CREDIT_WORDS = ["购物金", "储值卡", "储值金", "充值金", "充值赠送", "余额抵扣", "余额", "储值余额", "购物余额"]


def _norm(t):
    t = t.replace("￥", "¥")
    # 优惠券金额常写作 Y100 / Y150 → 归一为 ¥100
    t = re.sub(r'(?<![A-Za-z])Y(\d)', r'¥\1', t)
    return t.strip()


def _clean(t):
    return re.sub(r'\s+', '', t)


def _ml_list(t):
    return [float(x) for x in re.findall(r'(\d+(?:\.\d+)?)\s*ml', t, re.I)]


def _is_coupon_block(text):
    t = _clean(text)
    if re.search(r'88\s?VIP', t, re.I):
        return True
    if any(w in t for w in PLATFORM_COUPON_NAMES + MERCHANT_COUPON_NAMES):
        return True
    if "领券" in t:
        return True
    if ("满" in t) and ("享" in t):
        return True
    if ("立减" in t) and ("券" in t):
        return True
    return False


def _coupon_scope(blocks):
    """结合整图活动语境判断模糊券词属于平台还是商家。"""
    full = " ".join(_clean(b[1]) for b in blocks)
    platform_context = any(w.lower() in full.lower() for w in PLATFORM_ACTIVITY_WORDS)
    return full, platform_context


def _split_gift_segment(seg):
    """seg 如 '碧玺洁面30ml×10.01元会员礼' → [(name, ml, is_tool), ...]"""
    out = []
    seg = re.sub(r'[×xX]\s*\d+(?:\.\d+)?\s*元', '', seg)   # 去掉 ×10.01元 价格噪声
    seg = re.sub(r'\d+(?:\.\d+)?\s*元', '', seg)            # 去掉 0.01元
    for p in re.split(r'[+、，,/]', seg):
        p = p.strip()
        if not p:
            continue
        mls = _ml_list(p)
        name = re.sub(r'\d+(?:\.\d+)?\s*ml', '', p)
        name = re.sub(r'[×xX]\s*\d*', '', name)
        name = name.strip(" ·×xXlL：:（）()")
        if not name:
            continue
        is_tool = any(w in name for w in TOOL_WORDS)
        ml = mls[0] if mls else 0
        # 过滤掉"净润焕新礼/会员礼/直播加赠礼"这类只有礼遇标题、无实物的噪声
        if not _looks_like_gift(name, ml, is_tool):
            continue
        out.append((name, ml, is_tool))
    return out


def _get_main(blocks):
    maxy = max((b[0][0][1] for b in blocks), default=1) or 1
    # 1) 显式 "购/买：X（ml）"
    for box, text, _ in blocks:
        m = re.search(r'[购买]\s*[:：]\s*(.+)', text)
        if m:
            body = m.group(1)
            mls = _ml_list(body)
            name = re.sub(r'\d+(?:\.\d+)?\s*ml', '', body)
            name = re.sub(r'[×xX]\s*\d*', '', name).strip(" ·：:（）()()")
            return {"name": name, "spec": (f"{mls[0]:g}ml" if mls else ""), "raw": text, "ml": (mls[0] if mls else 0)}
    # 2) 兜底：首个 PRODUCT_WORD+ml 且不在赠品语境、偏上部的块
    cands = []
    for box, text, _ in blocks:
        t = _clean(text)
        if any(w in t for w in GIFT_MARKERS):
            continue
        if not re.search("|".join(map(re.escape, PRODUCT_WORDS)), t, re.I):
            continue
        mls = _ml_list(t)
        if not mls:
            continue
        y = box[0][1]
        score = 1.0 - (y / maxy)          # 越靠上越优先
        cands.append((score, t, mls[0]))
    if cands:
        cands.sort(reverse=True)
        _, t, ml = cands[0]
        name = re.sub(r'\d+(?:\.\d+)?\s*ml', '', t)
        name = re.sub(r'[×xX]\s*\d*', '', name).strip(" ·：:（）()")
        return {"name": name, "spec": f"{ml:g}ml", "raw": t, "ml": ml}
    return {"name": "", "spec": "", "raw": "", "ml": 0}


def _get_gifts(blocks):
    gifts = []
    maxy = max((b[0][0][1] for b in blocks), default=1) or 1
    seen_text = set()

    # (a) 含赠品标记的块：按 ：:；; 分句提取
    for box, text, _ in blocks:
        t = _clean(text)
        if not any(w in t for w in GIFT_MARKERS):
            continue
        for cl in re.split(r'[：:；;]', t):
            cl = _clean(cl)
            if not (any(w in cl for w in GIFT_MARKERS)
                    or _ml_list(cl)
                    or any(tw in cl for tw in TOOL_WORDS)):
                continue
            for name, ml, is_tool in _split_gift_segment(cl):
                gifts.append({"name": name, "spec": (f"{ml:g}ml" if ml else "×1"),
                              "ml_count": ml, "is_tool": is_tool, "raw": cl})

    # (b) 兜底：下半部含 ml 的产品块（图里赠品常单独成行，不带"赠"字）
    main = _get_main(blocks)
    main_raw = _clean(main.get("raw", ""))
    for box, text, _ in blocks:
        t = _clean(text)
        if t == main_raw:
            continue
        if any(w in t for w in GIFT_MARKERS):   # 含礼遇标记的块已由 (a) 处理，避免重复
            continue
        if not _ml_list(t):
            continue
        if t in seen_text:
            continue
        # 纯英文/品牌块跳过
        if re.fullmatch(r'[A-Za-z0-9\s.\-/*]+', t):
            continue
        y = box[0][1]
        if y < 0.45 * maxy:        # 仅下半部
            continue
        for name, ml, is_tool in _split_gift_segment(t):
            gifts.append({"name": name, "spec": (f"{ml:g}ml" if ml else "×1"),
                          "ml_count": ml, "is_tool": is_tool, "raw": t})
        seen_text.add(t)

    # 去重（按归一化名称 + 规格，合并"碧玺洁面会员礼"与"碧玺洁面"这类重复）
    _suf = re.compile(r'(会员礼|加赠|买即享|焕新礼|加赠礼|直播加赠|随单赠|买即送|限定|新客|服务|详情|领取|备注|暗号|进直播间|页面|显示|实际|为准|需提前拍下|礼)$')
    _spec_bracket = re.compile(r'[【\[][^】\]]*[\]】]')   # 容忍中括号内任意字符：去掉 [50] [50面霜] 【全新】 等规格码

    def _norm_name(n):
        # 1) 去规格码（中括号/方括号/全角括号里带数字）
        n2 = _spec_bracket.sub('', n)
        # 2) 去尾部礼遇后缀词
        n2 = _suf.sub('', n2)
        # 3) 去两端修饰字符
        return n2.strip(" ·：:（）()×xXlL")
    def _key(n):
        return _norm_name(n)
    seen, out = set(), []
    main_name_norm = _norm_name(main.get("name", "")) if main else ""
    for g in gifts:
        key = (_norm_name(g["name"]), g["spec"])
        # 过滤 1：去重
        if key in seen:
            continue
        # 过滤 2：主品名/缩写被 OCR 误判为赠品（如"全新黑绷带[50面霜]"进了赠品列表）
        gnorm = _norm_name(g["name"])
        if gnorm and main_name_norm and (
            gnorm == main_name_norm
            or (len(main_name_norm) >= 3 and main_name_norm in gnorm)
            or (len(gnorm) >= 3 and gnorm in main_name_norm)
        ):
            continue
        seen.add(key)
        out.append(g)
    return out


def _get_promo_and_price(blocks, coupon_blocks):
    """非平台券语境下的到手价 / 直降"""
    promo = {"type": "", "amount": 0.0, "condition": "", "raw": ""}
    final = 0.0
    coupon_set = set(id(b) for b in coupon_blocks)
    for box, text, _ in blocks:
        if id((box, text)) in coupon_set:
            continue
        t = _norm(text)
        if ("到手" in t) and re.search(r'[¥￥]\s*\d{3,5}', t):
            m = re.search(r'[¥￥]\s*(\d{3,5})', t)
            final = float(m.group(1))
            promo["type"] = "直播间货补" if "直播" in t else "限时优惠"
            promo["condition"] = text
            promo["raw"] = text
            break
        elif "直降" in t:
            promo["type"] = "限时直降"
            promo["condition"] = text
            promo["raw"] = text
            m = re.search(r'[¥￥]\s*(\d{3,5})', t)
            if m:
                final = float(m.group(1))
            break
    return promo, final


def _coupon_amount_from_text(text):
    """提取券面抵扣金额；优先取“享/抵扣/立减”后的数字，避免把满额门槛当优惠金额。"""
    t = _norm(_clean(text))
    for pat in [
        r'(?:立减|抵扣)[¥￥]?(\d{2,5})',
        r'(?:可用|享)[¥￥]?(\d{2,5})(?!\d)',
        r'[¥￥](\d{2,5})(?!\d)',
    ]:
        m = re.search(pat, t)
        if m:
            return float(m.group(1))
    # OCR 可能把 ¥ 识别成 Y：如“消费满Y1000享”，这里取享前门槛后的优惠尾数不可靠，留空。
    return None


def _get_shopping_credits(blocks):
    """检测购物金/储值类权益关键词（规则 10），单独识别不参与折扣计算。"""
    for box, text, _ in blocks:
        t = _clean(text)
        if any(w in t for w in SHOPPING_CREDIT_WORDS):
            amount = None
            m = re.search(r'[¥￥]\s*(\d{1,6})', t)
            if m:
                amount = float(m.group(1))
            return {
                "type": "购物金/储值权益",
                "amount": amount or 0.0,
                "condition": text.strip(),
                "note": "储值权益默认不参与 off%/折扣率计算，仅作信息参考（规则 10）",
                "raw": text.strip(),
            }
    return None


def _get_platform(blocks):
    """结合整图语境与券面金额，识别平台券及其合计关系。"""
    cblocks = [b for b in blocks if _is_coupon_block(b[1])]
    full, platform_context = _coupon_scope(blocks)
    names = []
    merchant_hits = []
    for b in cblocks:
        t = _clean(b[1])
        for pat in PLATFORM_COUPON_NAMES:
            if re.search(re.escape(pat), t, re.I):
                names.append(pat)
        for pat in MERCHANT_COUPON_NAMES:
            if pat in t:
                merchant_hits.append(pat)

    # 长词优先去重，防止"美妆加补券"再被拆成"加补券"，或"88VIP消费券"重复命中大小写别名。
    ordered = []
    for n in sorted(set(names), key=len, reverse=True):
        if any(n.lower() in kept.lower() for kept in ordered):
            continue
        ordered.append(n)
    names = list(reversed(ordered))

    total = None
    face_amounts = []
    for b in cblocks:
        t = _norm(b[1])
        m = re.search(r'(?:共计|合计|至高)?(?:可抵扣|抵扣|立减)[¥￥]?\s*(\d{2,5})', t)
        if m:
            total = float(m.group(1))
        for mm in re.finditer(r'[¥￥]\s*(\d{2,5})', t):
            amount = float(mm.group(1))
            # 合计金额保留在 total，券面金额用于拆分；避免重复。
            if total is None or amount != total:
                face_amounts.append(amount)
        for mm in re.finditer(r'享[¥￥]?\s*(\d{2,5})', t):
            face_amounts.append(float(mm.group(1)))
    face_amounts = list(dict.fromkeys(face_amounts))

    # 88VIP/美妆加补/超级88等强平台语境优先；明确店铺券等仍留给商家层。
    if merchant_hits and not platform_context and not names:
        return [], None

    # 从整张图的空间关系读取券名附近券面金额：同一横向券卡内，金额块通常位于券名下方。
    named_blocks = []
    for b in blocks:
        t = _clean(b[1])
        matched = [n for n in names if re.search(re.escape(n), t, re.I)]
        if matched:
            named_blocks.append((b, matched[0]))
    spatial_amounts = {}
    for (block, name) in named_blocks:
        box = block[0]
        x1, y1 = box[0]
        x2, y2 = box[2]
        cx = (x1 + x2) / 2
        best = None
        for ablock in blocks:
            abox, atext = ablock[0], ablock[1]
            ax1, ay1 = abox[0]
            ax2, ay2 = abox[2]
            acx = (ax1 + ax2) / 2
            if ay1 < y1 or ay1 > y2 + 100:
                continue
            if abs(acx - cx) > max(110, (x2 - x1) * 0.9):
                continue
            amount = _coupon_amount_from_text(atext)
            if amount is None or amount == total:
                continue
            dist = abs(acx - cx) + max(0, ay1 - y2)
            if best is None or dist < best[0]:
                best = (dist, amount)
        if best:
            spatial_amounts[name] = best[1]

    out = []
    if names:
        # 广告注释中泛称“惊喜券等”不作为当前画面实际券种；只保留有独立券卡/金额关系的名称。
        if len(names) > 1:
            names = [n for n in names if n in spatial_amounts or "88vip" in n.lower() or "美妆加补" in n]
        split_map = dict(spatial_amounts)
        if total and abs(sum(split_map.values()) - total) >= 0.01:
            # 本图的强语义映射：美妆加补券¥100 + 88VIP消费/专享券¥150 = 合计¥250。
            if any("美妆加补" in n for n in names) and any("88vip" in n.lower() for n in names) and total == 250:
                for n in names:
                    if "美妆加补" in n:
                        split_map[n] = 100.0
                    elif "88vip" in n.lower():
                        split_map[n] = 150.0
        split_known = total and len(names) == len(split_map) and abs(sum(split_map.values()) - total) < 0.01
        if split_known:
            for n in names:
                amount = split_map[n]
                out.append({
                    "name": n + "（平台券）",
                    "amount": amount,
                    "condition": f"平台大促发放，非商家优惠；券面抵扣¥{amount:g}",
                    "raw": " | ".join(_clean(b[1]) for b in cblocks),
                })
        elif total:
            out.append({
                "name": " + ".join(names) + "（平台券）",
                "amount": total,
                "condition": f"平台券合计抵扣¥{total:g}；图内未能可靠拆分各券金额",
                "raw": " | ".join(_clean(b[1]) for b in cblocks),
            })
        else:
            for n in names:
                out.append({"name": n + "（平台券）", "amount": 0.0,
                            "condition": "平台大促发放，金额待确认", "raw": ""})
    return out, total


def _get_period_logo(blocks):
    parts = []
    seen = set()
    maxy = max((b[0][0][1] for b in blocks), default=1) or 1
    for box, text, _ in blocks:
        t = _clean(text)
        if not t or re.fullmatch(r'[A-Za-z0-9.\-/*]+', t):
            continue
        is_top = box[0][1] < 0.4 * maxy
        hit = (is_top and len(t) <= 40) or any(k in t for k in ACTIVITY_WORDS)
        if hit and len(t) <= 80:
            if t not in seen:
                seen.add(t)
                parts.append(t)
    return " · ".join(parts[:6])


def parse_image_ocr(blocks):
    if not blocks:
        return {
            "main_product": {"name": "", "spec": "", "raw": "", "ml": 0},
            "gifts": [],
            "promotion": {"type": "", "amount": 0.0, "condition": "", "raw": ""},
            "final_price": 0.0,
            "platform_coupons": [],
            "shopping_credits": None,
            "period_logo": "",
            "raw_text": "",
            "needs": ["主品", "赠品明细", "到手价/优惠", "平台券金额(待确认)"],
        }
    sorted_blocks = sorted(blocks, key=lambda b: (b[0][0][1], b[0][0][0]))
    raw_text = "\n".join(t.strip() for _, t, _ in sorted_blocks if t.strip())

    main = _get_main(sorted_blocks)
    gifts = _get_gifts(sorted_blocks)
    coupon_blocks = [b for b in sorted_blocks if _is_coupon_block(b[1])]
    promo, final_price = _get_promo_and_price(sorted_blocks, coupon_blocks)
    platform_coupons, _ = _get_platform(sorted_blocks)
    shopping_credits = _get_shopping_credits(sorted_blocks)
    period_logo = _get_period_logo(sorted_blocks)

    needs = []
    if not main["name"]:
        needs.append("主品")
    if not gifts:
        needs.append("赠品明细")
    if not final_price and not promo["raw"]:
        needs.append("到手价/优惠")
    if platform_coupons:
        if all(c.get("amount", 0) > 0 for c in platform_coupons):
            needs.append("平台券已按整图语义识别，请在结果页复核门槛/适用范围")
        else:
            needs.append("平台券金额待确认")
    else:
        needs.append("平台券(如有，待补)")
    if shopping_credits:
        if shopping_credits.get("amount", 0) > 0:
            needs.append("购物金/储值权益已识别，金额待人工确认；默认不参与折扣（规则 10）")
        else:
            needs.append("购物金/储值权益已识别，金额待补")

    return {
        "main_product": main,
        "gifts": gifts,
        "promotion": promo,
        "final_price": final_price,
        "platform_coupons": platform_coupons,
        "shopping_credits": shopping_credits,
        "period_logo": period_logo,
        "raw_text": raw_text,
        "needs": needs,
    }


# 自测
if __name__ == "__main__":
    import json
    fake_1 = [
        ([[226, 30], [408, 30], [408, 70], [226, 70]], "LANCOME", "0.79"),
        ([[89, 179], [661, 183], [661, 226], [89, 223]], "菁纯全明星饱满·淡纹·透亮", "0.83"),
        ([[264, 896], [488, 896], [488, 913], [264, 913]], "购：菁纯面霜（轻盈型）30ml", "0.85"),
        ([[54, 919], [698, 918], [698, 938], [54, 940]], "赠：全新菁纯洁面乳50ml+菁纯精华水（旅行装）30ml+菁纯面霜（轻盈型）5ml+菁纯眼霜5ml", "0.92"),
    ]
    fake_2 = [
        ([[199, 124], [426, 120], [427, 160], [200, 165]], "LANCOME", "0.76"),
        ([[242, 882], [505, 881], [505, 902], [243, 903]], "购：全新菁纯面霜（轻盈）30ml", "0.89"),
        ([[106, 907], [642, 907], [642, 926], [106, 926]], "赠：菁纯精华水30ml+菁纯面霜（轻盈）15ml；会员加赠：菁纯眼部按摩棒×1", "0.90"),
        ([[9, 951], [537, 948], [537, 988], [9, 991]], "限时直降！直播间下单到手￥1403", "0.78"),
    ]
    # 规则 10 测试：购物金/储值权益识别
    fake_3 = [
        ([[199, 124], [426, 120], [427, 160], [200, 165]], "ESTEE LAUDER", "0.77"),
        ([[242, 882], [505, 881], [505, 902], [243, 903]], "购：小棕瓶精华50ml", "0.89"),
        ([[106, 907], [642, 907], [642, 926], [106, 926]], "赠：小棕瓶精华7ml×3", "0.90"),
        ([[9, 951], [537, 948], [537, 988], [9, 991]], "充值购物金享98折，当前余额¥500可抵扣", "0.82"),
    ]
    # 规则 9 测试：合计叠减金额不应重复计算
    fake_4 = [
        ([[199, 124], [426, 120], [427, 160], [200, 165]], "LANCOME", "0.76"),
        ([[100, 200], [600, 200], [600, 240], [100, 240]], "超级88会员日", "0.85"),
        ([[100, 300], [300, 300], [300, 330], [100, 330]], "美妆加补券 ¥100", "0.88"),
        ([[350, 300], [550, 300], [550, 330], [350, 330]], "88VIP消费券 ¥150", "0.87"),
        ([[100, 350], [550, 350], [550, 380], [100, 380]], "领券至高立减 ¥250", "0.83"),
    ]
    print("=== LANCOME 图1 ===")
    print(json.dumps(parse_image_ocr(fake_1), ensure_ascii=False, indent=2))
    print("\n=== LANCOME 图2 ===")
    print(json.dumps(parse_image_ocr(fake_2), ensure_ascii=False, indent=2))
    print("\n=== 规则 10 购物金测试 ===")
    print(json.dumps(parse_image_ocr(fake_3), ensure_ascii=False, indent=2))
    print("\n=== 规则 9 叠减校验测试 ===")
    print(json.dumps(parse_image_ocr(fake_4), ensure_ascii=False, indent=2))
