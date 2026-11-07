"""Small, local catalogue retrieval for Qwen; never invents a product or SKU."""
import re

CATEGORY_ALIASES = {
    "四合扣": ("四合扣", "按扣", "揿扣", "摁扣", "啪钮", "暗扣"),
    "调节扣": ("调节扣", "调节长短", "日字扣", "三档扣", "三挡扣", "梯扣", "口字扣", "二档扣"),
    "插扣": ("插扣", "旁开扣", "快拆扣", "背包开合", "腰带开合"),
    "D环扣": ("D环扣", "D环", "D形环", "D型环", "连接环"),
    "钩扣": ("钩扣", "挂钩", "旋转钩", "狗扣", "挂钥匙"),
    "对扣": ("对扣", "箱包搭扣"),
    "绳扣": ("绳扣", "弹簧扣", "猪鼻扣", "抽绳锁扣", "抽绳固定", "绳子松紧", "帽绳收紧"),
    "绳尾夹": ("绳尾夹", "绳尾扣", "吊钟", "绳头", "绳尾", "绳端"),
    "纽扣": ("纽扣", "钮扣", "四眼扣", "两眼扣", "衬衫扣", "西装扣"),
    "拉链": ("拉链", "拉锁", "衣服拉合"),
    "打扣工具": ("打扣工具", "打扣机", "打扣钳", "手压钳", "模具", "安装按扣", "装四合扣"),
    "五金扣": ("五金扣", "金属扣", "金属环", "皮带头"),
}


def category_from_text(text, categories):
    # Longest literal phrase wins. No fuzzy category guesses for order forms.
    folded = text.casefold().replace(" ", "")
    if "打扣工具" in categories and re.search(r"(?:安装|装|打|拆|起).{0,12}(?:按扣|四合扣|扣子)", text) and re.search(r"工具|钳|模具|机器|怎么|如何|用什么", text):
        return "打扣工具"
    options = [(len(term), category) for category in categories
               for term in (category, *CATEGORY_ALIASES.get(category, ()))
               if term.casefold().replace(" ", "") in folded]
    return max(options, default=(0, ""))[1]


def spec_matches(product, requested):
    """Compare a stated measurement, keeping missing hole sizes unconfirmed."""
    def dimension(text):
        match = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(mm|cm|毫米|厘米)", text, re.I)
        if not match:
            return None
        return float(match[1]) * (10 if match[2].casefold() in ("cm", "厘米") else 1)
    spec = product["spec"]
    if product["category"] in ("绳扣", "绳尾夹") and re.search(r"孔径(?:及外形尺寸)?待确认|孔径须复核", spec):
        return False
    expected, actual = dimension(requested), dimension(spec)
    return expected is not None and actual is not None and expected == actual


def product_text(product):
    values = [product.get(key, "") for key in
              ("name", "details", "description", "material", "uses", "keywords", "selection_notes")]
    return " ".join(" ".join(value) if isinstance(value, list) else str(value) for value in values)


def material_matches(product, material):
    if not material:
        return True
    # Only the material field/name/basic details are facts. A warning comparing
    # this item to another material must not make it match that other material.
    text = " ".join(str(product.get(key, "")) for key in ("name", "details", "material"))
    patterns = {"塑料": r"塑料|树脂|\bPOM\b|\bPA\b|\bPP\b|尼龙",
                "树脂": r"树脂", "尼龙": r"尼龙|\bPA\b", "金属": r"金属|合金|铜|不锈钢|铁|五金"}
    return bool(re.search(patterns.get(material, re.escape(material)), text, re.I))


def rank_products(query, products, limit=12, pinned=()):
    """Rank actual records by codes, category, dimensions and descriptive phrases.

    This is retrieval, not automatic selection: ambiguous variants still need
    the customer to choose a SKU/colour. Qwen sees at most `limit` rich records.
    """
    categories = tuple(dict.fromkeys(p["category"] for p in products))
    category = category_from_text(query, categories)
    folded = query.casefold()
    pairs = set(re.findall(r"(?=([\u4e00-\u9fff]{2}))", folded))
    sizes = re.findall(r"(\d+(?:\.\d+)?)\s*(?:mm|毫米)", folded)
    scored = []
    for index, product in enumerate(products):
        score = 0
        for code in {product["sku"], product.get("manufacturer_sku", product["sku"])}:
            if re.search(r"(?<![a-z0-9-])" + re.escape(code.casefold()) + r"(?![a-z0-9-])", folded):
                score += 200
        if product["sku"] in pinned:
            score += 15 if category else 60
        if category:
            score += 35 if category == product["category"] else -20
        text = product_text(product).casefold()
        score += min(18, sum(1 for pair in pairs if pair in text))
        score += sum(6 for word in product.get("keywords", []) if word.casefold() in folded)
        score += sum(12 for value in sizes if value in re.findall(r"\d+(?:\.\d+)?", product["spec"]))
        if product.get("color") and product["color"] in query:
            score += 10
        scored.append((score, -index, product))
    return [item[2] for item in sorted(scored, key=lambda item: item[:2], reverse=True)[:limit]]


def model_candidates(query, products, pinned=()):
    # Deliberately exclude internal provenance/merchant URLs from model context.
    fields = ("sku", "name", "category", "color", "spec", "unit", "material",
              "description", "uses", "keywords", "selection_notes", "verification_status")
    result = []
    import json
    for p in rank_products(query, products, pinned=pinned):
        item = {key: p[key] for key in fields if key in p}
        if result and len(json.dumps([*result, item], ensure_ascii=False)) > 3800:
            break
        result.append(item)
    return result
