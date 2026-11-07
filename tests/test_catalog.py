"""Catalogue regression tests: grounding, matching and bounded Qwen retrieval."""
from collections import Counter
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from datetime import date

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from catalog_search import category_from_text, material_matches, model_candidates, rank_products, spec_matches
from paste_parser import parse_paste
PRODUCTS = json.loads((ROOT / "catalog.json").read_text())
CATALOG = {p["sku"]: p for p in PRODUCTS}


class Catalogue(unittest.TestCase):
    def test_complete_unique_grounded_catalogue(self):
        self.assertEqual(len(PRODUCTS), 100)
        self.assertEqual(len(CATALOG), 100)
        self.assertGreaterEqual(len(Counter(p["category"] for p in PRODUCTS)), 10)
        for product in PRODUCTS:
            for field in ("sku", "name", "category", "spec", "unit", "description", "material", "selection_notes", "source_url", "verification_status"):
                self.assertIsInstance(product[field], str)
                self.assertTrue(product[field].strip(), (product["sku"], field))
            self.assertGreaterEqual(len(product["description"]), 30)
            self.assertTrue(product["keywords"])
            self.assertTrue(product["uses"])
            self.assertNotIn("price", product)
            self.assertNotIn("stock", product)
        self.assertEqual(CATALOG["SKT09"]["unit"], "包")
        self.assertIn("1000 套/包", CATALOG["SKT09"]["spec"])

    def test_legacy_keys_units_and_order_data_are_preserved(self):
        old = json.loads((ROOT / "tools" / "catalog-baseline.json").read_text())
        for item in old:
            self.assertIn(item["sku"], CATALOG)
            for field in ("unit", "color", "spec"):
                self.assertEqual(CATALOG[item["sku"]][field], item[field])

    def test_purpose_retrieval_and_tool_compatibility(self):
        for text, category in (("帽绳收紧用哪种扣", "绳扣"), ("包住绳头的金属吊钟", "绳尾夹"), ("拉链不方便抓想加一条拉绳", "拉片")):
            with self.subTest(text=text):
                self.assertEqual(rank_products(text, PRODUCTS, limit=1)[0]["category"], category)
        candidates = model_candidates("安装T5按扣要什么工具？", PRODUCTS)
        self.assertIn("打扣工具", {p["category"] for p in candidates})
        self.assertEqual(category_from_text("怎么安装T5按扣用什么工具", {p["category"] for p in PRODUCTS}), "打扣工具")
        self.assertEqual(rank_products("4.2mm帽绳", PRODUCTS, limit=1)[0]["sku"], "K334")

    def test_models_receive_descriptions_without_store_links(self):
        candidates = model_candidates("我需要帽绳配件", PRODUCTS)
        self.assertLessEqual(len(candidates), 12)
        self.assertLess(len(json.dumps(candidates, ensure_ascii=False)), 8500)
        for item in candidates:
            self.assertTrue(item["description"])
            self.assertNotIn("source_url", item)
            self.assertNotIn("source_label", item)

    def test_unknown_models_and_ambiguous_variants_remain_unselected(self):
        rows = parse_paste("K999999 100个\nM364 100个\nK334 100个\n2026-12-23交货", PRODUCTS, date(2026,11,7))["rows"]
        self.assertEqual(rows[0]["candidates"], [])
        self.assertEqual(rows[1]["sku"], "")
        self.assertEqual(len(rows[1]["candidates"]), 4)
        self.assertEqual(rows[2]["sku"], "K334")

    def test_notes_do_not_change_colour_and_units_are_not_converted(self):
        result = parse_paste("黑色T5 200套 备注：与白色纽扣分开装\nSKT09 100套\n2026-12-23交货", PRODUCTS, date(2026,11,7))
        self.assertEqual(result["rows"][0]["sku"], "T5")
        self.assertTrue(any("单位" in issue for issue in result["rows"][1]["issues"]))

    def test_dimensions_do_not_invent_hole_sizes(self):
        self.assertTrue(spec_matches(CATALOG["K334"], "4.2 mm"))
        self.assertFalse(spec_matches(CATALOG["K218"], "9 mm"))
        self.assertFalse(spec_matches(CATALOG["K416"], "5 mm"))
        self.assertTrue(material_matches(CATALOG["K49-V20"], "金属"))
        self.assertFalse(material_matches(CATALOG["K49-V20"], "塑料"))

    def test_catalogue_chat_returns_new_products_without_altering_draft(self):
        import server
        with patch.object(server, "DEMO_MODE", True):
            result = server.order_chat({"message": "绳扣4.2mm有哪些？"})
            self.assertEqual([p["sku"] for p in result["matches"]], ["K334"])
            self.assertFalse(result["draft"]["sku"])
            result = server.order_chat({"message": "安装T5按扣要什么工具？"})
            self.assertEqual({p["category"] for p in result["matches"]}, {"打扣工具"})
            self.assertEqual([p["sku"] for p in result["matches"]], ["DK-001-PRESS"])
            result = server.order_chat({"message": "拆卸T5按扣用什么工具？"})
            self.assertEqual([p["sku"] for p in result["matches"]], ["DK-001-REMOVE"])
            result = server.order_chat({"message": "NA8343是整条拉链还是配件？"})
            self.assertIn("并非整条拉链", result["reply"])


if __name__ == "__main__":
    unittest.main()
