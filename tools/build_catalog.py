#!/usr/bin/env python3
"""Build the reviewed 100-SKU trial catalogue from saved public product facts.

No network or database writes. Uses the versioned legacy catalogue baseline,
preserving SKU/unit keys to keep repeat orders compatible on a fresh checkout.
"""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "backups" / "catalog-before-100-products.json"
BASELINE = ROOT / "tools" / "catalog-baseline.json"
FACTS = ROOT / "research" / "public-products.json"

GUIDES = {
    "插扣": ("用于让两端织带扣合并解开，可作为背包、腰带或箱包连接件的选型候选。", ["背包", "腰带", "织带开合"], ["插扣", "旁开扣", "快拆", "子母扣"], "先量织带宽度和厚度，核对公母件是否成套，批量前拿样试装；不能据此推定承重或用于生命安全装备。"),
    "调节扣": ("用于穿带、固定或调节织带长短；具体穿带方式要与扣件结构配套。", ["肩带", "背包带", "腰带调节"], ["调节扣", "日字扣", "口字扣", "长短", "松紧"], "按穿带内宽而非扣件外宽选择，核对织带厚度、穿带方向和是否需要独立开合功能。"),
    "四合扣": ("按压扣合的四合扣，供衣物、家纺等开合位置选型，与缝纫纽扣安装方式不同。", ["衣物门襟", "袖口", "家纺开合"], ["四合扣", "按扣", "揿扣", "子母扣", "门襟", "衣服"], "核对扣面直径、面料厚度、脚长及安装模具；一套配件构成由销售确认。儿童用品需另外确认适用标准和安装牢度。"),
    "D环扣": ("D形环可为织带和挂接件提供连接点，适合箱包配件选型。", ["箱包连接", "肩带连接点"], ["D环", "D形环", "连接点", "穿带环"], "以穿带内径匹配织带宽度，核对另一端挂钩能否穿过；无可核实承重数据。"),
    "钩扣": ("挂接式连接件，用于把带子或小配件连接到相应的环或连接位。", ["肩带挂接", "箱包配件", "钥匙挂接"], ["挂钩", "钩扣", "狗扣", "连接环", "挂接"], "核对穿带处内宽、钩口开度和连接环厚度；宠物牵引等用途须先做适配及拉力确认。"),
    "对扣": ("用于箱包等部位的配对扣合，结构须按实物核对。", ["箱包扣合", "服饰连接"], ["对扣", "箱包扣", "配对连接"], "核对成套件、扣合方向及安装位置，不能仅凭标称尺寸判断能否替换旧扣。"),
    "绳扣": ("穿绳后用于定位或调节抽绳松紧，可作为衣帽、抽绳袋等部位的选型候选。", ["帽绳", "裤脚抽绳", "抽绳袋", "衣服下摆"], ["抽绳", "绳扣", "弹簧扣", "猪鼻扣", "收紧", "松紧"], "先确认绳径、穿绳根数和孔数，再核对孔径及锁紧结构；外观长宽不能代替穿绳孔径。"),
    "绳尾夹": ("用于绳端包覆、整理或装饰，可作为衣帽吊绳端部配件选型；不能据此认定有锁绳功能。", ["帽绳绳端", "羽绒服吊绳", "抽绳装饰"], ["绳尾", "吊钟", "绳头", "绳端", "装饰"], "核对绳径、安装方式及是否需打结固定；若需要锁紧抽绳，应另选有对应结构的绳扣。"),
    "拉片": ("连接在拉头上的辅助抓握件或拉尾，便于手指拉动；这是拉片配件，并非整条拉链。", ["衣物拉头", "背包拉头", "手套抓握"], ["拉片", "拉尾", "拉绳", "拉链头", "抓握"], "确认拉头连接孔、安装结构和拉片长度；拉片不能替代拉头，也不能保证修复损坏的链牙。"),
    "打扣工具": ("用于塑料四合扣安装或拆卸的手工具，具体功能按所选款式区分。", ["四合扣安装", "样品制作", "维修拆扣"], ["打扣钳", "手压钳", "安装按扣", "拆扣", "模具"], "先确认扣型、模头和工具接口是否兼容；不同扣型不能只按外观大小共用模具。"),
}


def main():
    BACKUP.parent.mkdir(exist_ok=True)
    if not BACKUP.exists():
        shutil.copy2(ROOT / "catalog.json", BACKUP)
    old = json.loads(BASELINE.read_text())
    facts = json.loads(FACTS.read_text())
    sources = {p["id"]: p for p in facts["products"] if "error" not in p}
    products = []

    def add(ident, sku, name, category, spec, color="", material=None, manufacturer=None, detail="", unit=None):
        source = sources[str(ident)]
        evidence = json.dumps(source, ensure_ascii=False)
        manufacturer = manufacturer or sku
        if manufacturer.casefold() not in evidence.casefold():
            raise ValueError("型号未出现在保存的公开资料中: " + manufacturer)
        attributes = source["attributes"]
        material = material or attributes.get("材质") or attributes.get("材料") or "材质待确认"
        products.append({"sku": sku, "manufacturer_sku": manufacturer, "name": name, "category": category,
                         "color": color, "spec": spec, "unit": unit or source["unit"], "material": material,
                         "details": detail or ("按色卡选色，具体色号待销售确认" if not color else material),
                         "source_label": "KAM华联公开商家目录", "source_url": source["url"],
                         "source_record_id": str(ident), "source_variant": manufacturer,
                         "sku_kind": "manufacturer" if sku == manufacturer else "internal_variant",
                         "verification_status": "公开型号，规格待确认" if "待确认" in spec else "公开型号及规格"})

    # Keep historic keys and units, replacing the file rather than mutating orders.
    for product in old:
        product = dict(product)
        match = re.search(r"(?:detail/|products/)(\d+)", product.get("source_url", ""))
        source = sources.get(match[1]) if match else None
        product["material"] = (source or {}).get("attributes", {}).get("材质") or (
            "树脂" if "树脂" in product["name"] else "PA 塑料" if "PA" in product["details"] else "POM 塑料" if "POM" in product["details"] else "塑料")
        product["verification_status"] = "公开型号及规格" if source else "既有目录，待销售复核"
        product["sku_kind"] = "internal_variant" if product.get("manufacturer_sku") else "manufacturer"
        product["source_record_id"] = match[1] if match else ""
        products.append(product)

    for size in (20, 25, 32, 38):
        add(977887704, f"M364-V{size}", "蛙形塑料插扣", "插扣", f"内径 {size} mm", manufacturer="M364", material="塑料", detail="蛙形结构；具体塑料牌号待确认")
    for size in (25, 35):
        add(976056046, f"M956-{size}", "黑色塑料箱包插扣", "插扣", f"内径 {size} mm", "黑色")
    add(976341238, "M640E-20", "黑色塑料腰包插扣", "插扣", "内径 20 mm", "黑色")
    for code, category, name in (("M033-50", "插扣", "黑色宽带箱包插扣"), ("M298-50", "调节扣", "黑色三档织带调节扣"), ("M001-50", "调节扣", "黑色二档口字扣")):
        add(977245126, code, name, category, "内径 50 mm", "黑色", detail="同一组合页中的单个扣件，按个计量；不是三件套")
    for ident, code, name, category, size in (
        (977372572, "M422-30", "黑色尼龙箱包对扣", "对扣", 30),
        (986555855, "M602-20", "黑色双调节书包插扣", "插扣", 20),
        (976262200, "M605-50", "黑色行李箱密码绑带扣", "插扣", 50),
        (976579567, "M001-25", "塑料二档口字扣", "调节扣", 25),
        (976477464, "M143-50", "黑色宽带塑料插扣", "插扣", 50),
        (977617516, "M946-25", "黑色塑料箱包插扣", "插扣", 25),
        (979135905, "M001-15", "塑料二档口字扣", "调节扣", 15),
        (987166715, "M1056-25", "塑料三档调节扣", "调节扣", 25),
        (987166197, "M1055-25", "塑料三档方形调节扣", "调节扣", 25),
        (987118377, "M1023-25", "塑料背包调节扣", "调节扣", 25),
        (987116041, "M1020-10", "小型塑料织带调节扣", "调节扣", 10),
        (987084974, "M1006-10", "塑料鸭舌弧形梯扣", "调节扣", 10),
        (986995813, "M977-30", "塑料二档穿带调节扣", "调节扣", 30),
        (987310321, "M1109-25", "塑料腰带旁开插扣", "插扣", 25),
        (987240994, "M1095-25", "塑料休闲腰带插扣", "插扣", 25),
        (987073955, "M988-25", "塑料三角织带挂钩", "钩扣", 25),
        (986883401, "M909-25", "塑料肩带旋转钩扣", "钩扣", 25),
    ):
        add(ident, code, name, category, f"内径 {size} mm", "黑色" if name.startswith("黑色") else "",
            material="塑料（具体牌号待确认）" if code == "M001-25" else None)
    add(987298396, "M1105", "塑料圆形锁式调节扣", "调节扣", "尺寸待确认", material="T+PA（具体牌号待确认）")

    for ident, code, name, spec, color in (
        (975740553, "K286", "黑色猪鼻弹性绳扣", "孔径 5 mm；织带孔 8.5 mm", "黑色"),
        (978970476, "K013", "黑色长方形抽绳扣", "大号；孔径待确认", "黑色"),
        (986902599, "K334", "塑料帽绳弹簧扣", "孔径 4.2 mm", ""),
        (975849846, "K345", "黑色双孔锥形绳端吊钟", "外高 16.8 mm × 外宽 23 mm；孔径待确认", "黑色"),
        (975770740, "K201", "黑色弹簧穿绳扣", "绳孔 5 × 4 mm；织带孔 10 mm", "黑色"),
        (976511211, "K218", "黑色双孔定位绳扣", "外高 21.2 × 宽 23.6 × 厚 9 mm；孔径待确认", "黑色"),
        (980672262, "K416", "黑色圆形双孔鞋带绳扣", "标注尺寸 5 mm（孔径须复核）", "黑色"),
    ):
        add(ident, code, name, "绳尾夹" if code == "K345" else "绳扣", spec, color,
            material="塑料（页面标注 PVC，牌号待复核）" if code == "K201" else None)
    for code in ("K533", "K525", "K537", "K532", "K536", "K416E"):
        add(974159244, code, "彩色双孔塑料绳扣", "绳扣", "孔径及外形尺寸待确认")
    for ident, code, name, category, spec in (
        (983567263, "YK0006", "金属圆柱形绳端吊钟", "绳尾夹", "外形及孔径待确认"),
        (983536496, "YK0031", "金属绳头装饰吊钟", "绳尾夹", "外形及孔径待确认"),
        (975969948, "YK2093", "金属对按式束绳扣", "绳扣", "外形 17 × 8.8 mm；孔径待确认"),
        (979398803, "YK2080C", "金属帽绳吊钟", "绳尾夹", "外形及孔径待确认"),
        (975986967, "YA0075", "金属帽绳端部吊钟", "绳尾夹", "标注外形 6 × 13 mm；孔径待确认"),
    ):
        add(ident, code, name, category, spec, detail="表面处理可选挂白、挂枪、挂柠檬金，具体色样待确认")
    for size in (10, 12, 15, 20, 25, 32, 38):
        add(976862576, f"K49-V{size}", "金色金属三档日字调节扣", "调节扣", f"内径 {size} mm", "金色", material="金属（具体基材及镀层待确认）", manufacturer="K49")
    for code, size in (("CW008", 25), ("CW007", 20), ("CW006", 13), ("CW009", 32), ("CW010", 25), ("CW011", 20), ("CW012", 25)):
        add(973522897, code, "金属旋转牵引钩扣", "钩扣", f"内径 {size} mm", material="锌合金（以样品和销售确认）", detail="公开列明的 CW 系列款式；牵引用途需另行核对拉力与锁扣可靠性")
    for code in ("NA8343", "NA8556", "NA8574", "NA8542", "NA8551", "NA8546"):
        add(972430566, code, "注塑绳式拉链拉片", "拉片", "拉片长度及连接孔待确认")
    for code in ("SKA610", "SKA713", "SKA723", "SKA725", "SKA823", "SKA825"):
        add(973041717, code, "彩色塑料四合扣", "四合扣", "扣面直径及脚长待确认")
    for code, size in (("SKT09", "9"), ("SLT5B", "9"), ("TA15", "8.9")):
        add(972428985, code, "彩色轻薄树脂四合扣", "四合扣", f"扣面 {size} mm；1000 套/包", detail="按包计量，每包1000套；色号、脚长和面料适配待确认")
    add(972431311, "DK-001-PRESS", "四合扣安装手压钳", "打扣工具", "手压钳；配套模头待确认", manufacturer="DK-001", material="不锈钢（页面标注）", detail="安装款；T3、T5、T8等扣型的配套模头须逐项确认")
    add(972431311, "DK-001-REMOVE", "四合扣拆卸起扣钳", "打扣工具", "起扣钳；适配扣型待确认", manufacturer="DK-001", material="不锈钢（页面标注）", detail="拆卸款；不是安装钳，先核对扣型与面料保护方法")
    for color, suffix in (("红色", "RED"), ("白色", "WHITE")):
        add(975628166, f"T5-{suffix}", color + "树脂四合扣", "四合扣", "12.4 mm", color, manufacturer="T5", detail="圆形凸花；扣面直径12.4mm")

    assert len(products) == 100, len(products)
    assert len({p["sku"] for p in products}) == 100
    for p in products:
        description, uses, keywords, notes = GUIDES[p["category"]]
        p["description"] = p["name"] + "，" + p["spec"] + "，按" + p["unit"] + "计量。" + description
        p["uses"] = uses
        p["keywords"] = list(dict.fromkeys([*keywords, *re.findall(r"双孔|圆形|三档|二档|旋转|密码|蛙形|轻薄|锥形", p["name"])]))
        p["selection_notes"] = notes
        if p["sku"] == "DK-001-PRESS":
            p["description"] = "塑料四合扣安装手压钳，按件计量，用于配套扣件的压合安装。需按具体扣型确认模头和工具接口，适合打样及小批量手工作业。"
            p["uses"] = ["四合扣安装", "打样压扣"]
            p["keywords"] = ["安装", "手压钳", "打扣钳", "压合", "模头"]
        elif p["sku"] == "DK-001-REMOVE":
            p["description"] = "四合扣拆卸起扣钳，按件计量，用于返修时拆除原有扣件。此款用于拆卸，不能替代安装手压钳；先确认扣型并保护面料。"
            p["uses"] = ["拆扣返修", "四合扣拆卸"]
            p["keywords"] = ["拆卸", "拆扣", "起扣钳", "返修", "更换旧扣"]
        if "待确认" in p["spec"]:
            p["selection_notes"] = "此款尚有规格待确认，请先让销售核对实物参数。" + notes
        if p["unit"] == "包":
            p["selection_notes"] += "数量按包填写，不能把套数直接填为包数。"
        if not p.get("source_url") or not p["unit"]:
            raise ValueError("missing provenance/unit: " + p["sku"])
    (ROOT / "catalog.json").write_text(json.dumps(products, ensure_ascii=False, indent=2) + "\n")
    audit = {
        "built_at": datetime.now(timezone.utc).isoformat(), "count": len(products),
        "category_counts": dict(Counter(p["category"] for p in products)),
        "verification_counts": dict(Counter(p["verification_status"] for p in products)),
        "internal_variant_skus": [p["sku"] for p in products if p["sku_kind"] == "internal_variant"],
        "company_identity": {"likely_entity": "福建省石狮市华联服装配件企业有限公司", "brands": ["华联", "KAM"],
                             "fuzhou_branch_verified": False, "inventory_verified": False,
                             "intro_source": "https://wap.gzitn.com/store.php?CompID=3897", "brand_source": "https://www.kam-hl.com/zh-hant/"},
        "taobao": {"search_url": "https://s.taobao.com/search?q=KAM华联服装辅料", "status": "仅取得动态页面外壳，无可导入商品明细；未登录或绕过访问限制", "imported_records": 0},
        "public_source": facts["shop_url"], "readable_listing_count": len(sources),
        "unreadable_listing_count": len(facts["products"]) - len(sources),
        "rules": ["来源链接仅内部存档，不在客户商品卡中展示", "商品描述与选型建议为独立编写的一般用途说明，不是性能认证",
                  "包含同一公开页面中的不同型号、尺寸和颜色SKU", "内部变体SKU与厂家型号分开保存", "未导入店铺价格、实时库存、客户或成本数据"],
        "invoice_clues": ["HT01", "T8A", "J024-36", "M032F-40", "M232-25", "M031-40", "J051-40"],
        "invoice_clues_status": "图片货号未能逐项核实名称和规格，未强行映射为其他商品",
    }
    (ROOT / "research" / "catalog-import.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: audit[k] for k in ("count", "category_counts", "verification_counts")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
