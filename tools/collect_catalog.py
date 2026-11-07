#!/usr/bin/env python3
"""Collect a bounded selection of public KAM seller listings, without credentials.

Only product facts are retained. Contact data, prices, stock and page scripts are
deliberately not stored or imported. Existing orders are never opened here.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html import unescape
import json
from pathlib import Path
import re
import time
from urllib.request import Request, urlopen
from threading import Event

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "research" / "public-products.json"
BASE = "https://www.yiwugo.com"
SHOP = BASE + "/hu/888004278.html"
CHALLENGED = Event()
SECTIONS = {
    "绳扣": (622229, 22), "四合扣": (630568, 14),
    "树脂钮扣": (622221, 12), "拉链": (622222, 9),
    "打扣工具": (622223, 9), "弹簧扣": (665121, 9),
    "五金": (622220, 12), "调节扣": (675015, 12),
    "插扣": (654362, 12), "D环扣": (671514, 8), "钩扣": (675022, 8),
}


def fetch(url):
    if CHALLENGED.is_set():
        raise ValueError("public source requested verification; collection stopped")
    # No account, cookies, API token, proxy rotation or access-control bypass.
    with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20) as response:
        if response.status != 200:
            raise ValueError("unexpected public page status")
        html = response.read(2_000_000).decode("utf-8", "replace")
        if "verifyCodeDemo" in html or "id=\"picyzm\"" in html:
            CHALLENGED.set()
            raise ValueError("public source requested verification; no bypass attempted")
        return html


def collect_product(entry):
    ident, section = entry
    url = BASE + "/product/detail/" + ident + ".html"
    try:
        html = fetch(url)
        script = next(s for s in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
                      if "window.__INITIAL_STATE__" in s)
        page = json.JSONDecoder().raw_decode(script[script.find("{"):])[0]["c"]
        detail = page["detail"]
        product = detail["productDetailVO"]
        if detail.get("userId") != "shop_294992":
            raise ValueError("unexpected merchant")
        attributes = {v["name"]: v["value"] for v in detail.get("productExtendList", [])}
        variants = [{k: v[k] for k in ("specificationNameOne", "specificationNameTwo") if v.get(k)}
                    for v in detail.get("productPropertySkuBoList", [])]
        return {"id": ident, "section": section, "url": url,
                "title": product["title"], "goods_code": product.get("goodsCode", ""),
                "attributes": attributes, "variants": variants,
                "unit": product.get("metric", ""),
                "description": unescape(re.sub(r"<[^>]+>", " ", product.get("introduction") or ""))[:1000]}
    except (OSError, ValueError, KeyError, StopIteration, TypeError) as error:
        return {"id": ident, "section": section, "url": url, "error": type(error).__name__ + ": " + str(error)[:120]}
    finally:
        time.sleep(0.4)


def main():
    entries = {}
    for section, (ident, limit) in SECTIONS.items():
        html = fetch(SHOP + "?dt=" + str(ident))
        ids = list(dict.fromkeys(re.findall(r"/product/detail/(\d+)\.html", html)))
        for item in ids[:limit]:
            entries.setdefault(item, section)
        print(section, "public listings:", len(ids), "selected:", min(len(ids), limit), flush=True)
        time.sleep(0.6)
    # Recheck every legacy SKU so that historic repeat orders retain their keys.
    for product in json.loads((ROOT / "catalog.json").read_text()):
        match = re.search(r"/detail/(\d+)\.html", product.get("source_url", ""))
        if match:
            entries[match[1]] = product["category"]
    DEST.parent.mkdir(exist_ok=True)
    facts = {"retrieved_at": datetime.now(timezone.utc).isoformat(), "merchant": "KAM华联服装辅料（工厂直营）",
             "shop_url": SHOP, "products": []}
    with ThreadPoolExecutor(max_workers=2) as pool:
        for i, product in enumerate(pool.map(collect_product, entries.items()), 1):
            facts["products"].append(product)
            if i % 10 == 0:
                DEST.write_text(json.dumps(facts, ensure_ascii=False, indent=2) + "\n")
                print("Read", i, "/", len(entries), "public products", flush=True)
    DEST.write_text(json.dumps(facts, ensure_ascii=False, indent=2) + "\n")
    print("Saved", len(facts["products"]), "records; errors:", sum("error" in p for p in facts["products"]), flush=True)


if __name__ == "__main__":
    main()
