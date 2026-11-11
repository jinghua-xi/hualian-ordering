"""Consultation stays read-only and grounds recommendations in the live catalogue."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class ChatConsultation(unittest.TestCase):
    def consult(self, message, **context):
        with patch.object(server, "DEMO_MODE", True):
            return server.consult_catalog(message, context)

    def test_order_and_draft_commands_cannot_change_purchase_fields(self):
        draft = {"sku": "T5", "quantity": 100, "customer": "测试客户"}
        for message in ("再加一款", "选用 M916-25", "我要下单 T5 2000 套", "换成 M916-25",
                        "客户名称是另一家公司", "备注：单独包装", "数量改成200套"):
            with self.subTest(message=message):
                result = self.consult(message, draft=draft, note_requested=True, next_item=True)
                self.assertTrue({"draft", "next_item", "item_note"}.isdisjoint(result))
                self.assertNotIn("已加入", result["reply"])
                self.assertNotIn("已填写", result["reply"])

    def test_catalogue_queries_and_followups_use_actual_specs(self):
        for message, category, spec in (
            ("有哪些内径 25 mm 的插扣？", "插扣", "25 mm"),
            ("有哪些孔径四点二毫米的绳扣？", "绳扣", "4.2 mm"),
            ("背包织带25毫米，需要快速解开", "插扣", "25 mm"),
            ("20毫米肩带只调长短，不需要打开", "调节扣", "20 mm"),
        ):
            with self.subTest(message=message):
                result = self.consult(message)
                self.assertTrue(result["matches"])
                for product in result["matches"]:
                    self.assertEqual(product, server.CATALOG[product["sku"]])
                    self.assertEqual(product["category"], category)
                    self.assertTrue(server.spec_matches(product, spec))
                suggestions = server.consultation_choices(message, result["matches"])
                self.assertGreater(len(suggestions), 0)
                self.assertLessEqual(len(suggestions), 3)
                for suggestion in suggestions:
                    detail = self.consult(suggestion["value"], catalog_context=result["catalog_context"])
                    self.assertEqual(len(detail["matches"]), 1)
                    self.assertIn(detail["matches"][0], result["matches"])

    def test_alternatives_disclose_incompatible_spec_or_category(self):
        result = self.consult("推荐三款 99 毫米的黑色插扣")
        self.assertIn("目录备选", result["reply"])
        self.assertTrue(result["matches"])
        for product in result["matches"]:
            self.assertEqual(product["category"], "插扣")
            self.assertIn(product["spec"], result["reply"])
        result = self.consult("推荐三款商务衬衫纽扣")
        self.assertIn("没有收录纽扣", result["reply"])
        self.assertIn("与缝纫纽扣不同", result["reply"])

    def test_unknown_sku_and_unverified_performance_do_not_return_products(self):
        for query in ("推荐一个型号叫M9999-99的插扣", "M9999-99", "哪个插扣能承重一百公斤？",
                      "推荐一部电影", "怎么打开 Excel？", "T5 20mm"):
            with self.subTest(query=query):
                result = self.consult(query)
                self.assertFalse((result or {}).get("matches"))

    def test_t5_colour_variants_and_bounded_suggestions(self):
        message = "T5 四合扣有哪些颜色？"
        result = self.consult(message)
        self.assertEqual({item["color"] for item in result["matches"]}, {"黑色", "灰色", "红色", "白色"})
        self.assertEqual(len(server.consultation_choices(message, result["matches"])), 3)
        all_products = self.consult("目录里有哪些商品？")
        self.assertEqual(len(all_products["matches"]), len(server.PRODUCTS))
        self.assertEqual(len(server.consultation_choices("", all_products["matches"])), 3)

    def test_manufacturer_code_can_match_multiple_specifications(self):
        result = self.consult("M364 有哪些规格？")
        self.assertEqual({item["manufacturer_sku"] for item in result["matches"]}, {"M364"})
        self.assertEqual(len(result["matches"]), 4)
        variant = self.consult("M364-V25 有什么规格？")
        self.assertEqual([item["sku"] for item in variant["matches"]], ["M364-V25"])

    def test_qwen_cannot_supply_a_sku_outside_the_catalogue(self):
        payload = {"intent": "advice", "category": "插扣", "spec": "", "color": "", "material": "",
                   "catalog_skus": ["NONEXISTENT-123"]}
        answer = BytesIO(json.dumps({"message": {"content": json.dumps(payload)}}).encode())
        with patch.object(server, "DEMO_MODE", False), patch.object(server, "build_opener") as opener:
            opener.return_value.open.return_value = answer
            result = server.consult_catalog("想给这件衣服配点辅料", {})
        opener.return_value.open.assert_called_once()
        self.assertFalse(result.get("matches"))
        self.assertNotIn("NONEXISTENT", result["reply"])

    def test_daily_quota_is_atomic_and_resets_at_beijing_midnight(self):
        now = server.china_now().replace(hour=23, minute=59, second=59, microsecond=0)
        owner = "account:quota-test"
        with tempfile.TemporaryDirectory() as folder, patch.object(server, "DB_PATH", Path(folder) / "orders.sqlite3"), \
                patch.object(server, "china_now", return_value=now) as clock:
            server.init_db()
            with closing(server.connect_db()) as db, db:
                db.execute("INSERT INTO chat_usage VALUES (?, ?, ?)", (owner, now.date().isoformat(), 149))
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: server.chat_quota(owner, spend=True), range(2)))
            self.assertEqual(sum(allowed for _, allowed in results), 1)
            self.assertEqual(server.chat_quota(owner)[0]["remaining"], 0)
            server.refund_chat(owner, now.date().isoformat())
            self.assertEqual(server.chat_quota(owner)[0]["remaining"], 1)
            clock.return_value = now + timedelta(seconds=1)
            self.assertEqual(server.chat_quota(owner)[0]["remaining"], 150)
            self.assertEqual(server.chat_quota(owner, spend=True)[0]["remaining"], 149)

    def test_general_questions_do_not_get_misrouted_to_the_catalogue(self):
        response = {"reply": "这是一般知识答复。", "search_query": ""}
        with patch.object(server, "generate_chat_reply", return_value=response) as generate:
            for message in ("怎么打开 Excel？", "Python3 怎么读取文件？", "四合扣是谁发明的？", "推荐一部电影"):
                with self.subTest(message=message):
                    self.assertEqual(server.answer_chat(message, {})["reply"], response["reply"])
            self.assertEqual(generate.call_count, 4)

    def test_automatic_search_grounds_answer_and_does_not_send_history(self):
        sources = [{"title": "地铁公告", "url": "https://example.com/news", "snippet": "线路已开通。"}]
        with patch.object(server, "DEMO_MODE", False), \
                patch.object(server, "generate_chat_reply", side_effect=[
                    {"reply": "", "search_query": "柏林地铁开通公告"}, {"reply": "线路已开通 [1]。", "search_query": ""}]) as generate, \
                patch.object(server, "public_keyword_search", return_value=sources) as search:
            result = server.answer_chat("柏林这条地铁开通了吗？", {"history": [{"role": "user", "content": "订单号内部记录"}]})
        search.assert_called_once_with("柏林地铁开通公告", baike=False)
        self.assertEqual(generate.call_args.kwargs["references"], sources)
        self.assertEqual(result["references"], sources)
        self.assertEqual(result["reply"], "线路已开通 [1]。")

    def test_snap_uses_have_a_manufacturer_reference_when_live_search_fails(self):
        with patch.object(server, "public_keyword_search", return_value=[]), \
                patch.object(server, "generate_chat_reply") as generate:
            result = server.answer_chat("搜索四合扣的用途场景", {})
        generate.assert_not_called()
        self.assertEqual(result["web_status"], "reference")
        self.assertIn("包袋", result["reply"])
        self.assertEqual(result["references"], [server.SNAP_USE_GUIDE])


if __name__ == "__main__":
    unittest.main()
