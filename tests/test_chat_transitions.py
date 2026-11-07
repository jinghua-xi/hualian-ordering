"""Completed-item transitions must preserve cart boundaries and unfinished drafts."""
from datetime import timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class ChatTransitions(unittest.TestCase):
    def setUp(self):
        self.ready = {**server.EMPTY_DRAFT, "sku": "T5", "category": "四合扣", "color": "黑色", "quantity": 100, "unit": "套",
                      "delivery_date": (server.china_now().date()+timedelta(days=20)).isoformat(), "customer": "测试客户"}

    def chat(self, text, draft=None):
        with patch.object(server, "DEMO_MODE", True):
            return server.order_chat({"message": text, "draft": self.ready if draft is None else draft})

    def test_new_purpose_and_explicit_next_consultation_stage_previous(self):
        for text in ("帽子抽绳需要收紧，推荐什么？", "我要25毫米宽的织带能快速解开，推荐什么？",
                     "继续咨询下一个商品", "包住绳头的金属吊钟有哪些？", "绳扣多少钱？", "M916-25"):
            with self.subTest(text=text):
                result = self.chat(text)
                self.assertTrue(result.get("next_item"))
                self.assertEqual(result["draft"]["customer"], "测试客户")
                self.assertEqual(result["draft"]["delivery_date"], "")
                self.assertIsNone(result["draft"]["quantity"])
        self.assertEqual(self.chat("M916-25")["draft"]["sku"], "M916-25")

    def test_model_recognized_use_case_stages_previous_too(self):
        extracted = {**server.demo_extraction("建议"), "intent": "advice", "category": "拉片"}
        with patch.object(server, "DEMO_MODE", False), patch.object(server, "qwen_extraction", return_value=extracted), patch.object(server, "enrich_advice", side_effect=lambda result, message: result):
            result = server.order_chat({"message": "背包拉头处想配一个便于手指拉动的小部件，有推荐吗？", "draft": self.ready})
        self.assertTrue(result.get("next_item"))
        self.assertFalse(result["draft"]["sku"])
        self.assertEqual({p["category"] for p in result["matches"]}, {"拉片"})

    def test_incomplete_items_are_not_discarded(self):
        for missing in ("sku", "quantity", "delivery_date"):
            draft = {**self.ready, missing: None if missing == "quantity" else ""}
            for text in ("帽子抽绳需要收紧，推荐什么？", "继续咨询下一个商品"):
                with self.subTest(missing=missing, text=text):
                    result = self.chat(text, draft)
                    self.assertEqual(result["mode"], "draft_pending")
                    self.assertFalse(result.get("next_item"))
                    self.assertIn("草稿还没完成", result["reply"])
                    self.assertEqual(result["draft"]["quantity"], draft["quantity"])
        with patch.object(server, "DEMO_MODE", False), patch.object(server, "qwen_extraction", return_value={**server.demo_extraction("建议"), "intent":"advice", "category":"拉片"}):
            result=server.order_chat({"message":"戴手套时不方便抓握，能帮我选吗？", "draft":{**self.ready,"delivery_date":""}})
        self.assertEqual(result["mode"], "draft_pending")

    def test_corrections_and_questions_about_current_item_do_not_stage(self):
        for text in ("数量改成200套", "这个扣子怎么安装？", "这款有其他颜色吗？", "这款多少钱？", "这个25毫米能用吗？", "T5和M916-25有什么区别？"):
            with self.subTest(text=text):
                self.assertFalse(self.chat(text).get("next_item"))

    def test_dates_and_customer_names_remain_current_item_fields(self):
        delivery=(server.china_now().date()+timedelta(days=30)).isoformat()
        result=self.chat(delivery)
        self.assertFalse(result.get("next_item"))
        self.assertEqual(result["draft"]["delivery_date"], delivery)
        result=self.chat("客户名称是另一家公司")
        self.assertFalse(result.get("next_item"))
        self.assertEqual(result["draft"]["customer"], "另一家公司")

    def test_validated_units_and_dates_required_before_staging(self):
        for draft in ({**self.ready, "unit":"个"}, {**self.ready,"quantity":0}, {**self.ready,"delivery_date":server.china_now().date().isoformat()}):
            result=self.chat("帽子抽绳需要收紧，推荐什么？", draft)
            self.assertEqual(result["mode"], "draft_pending")
            self.assertFalse(result.get("next_item"))

    def test_model_failure_does_not_prevent_known_transition(self):
        with patch.object(server,"DEMO_MODE",False),patch.object(server,"qwen_extraction",side_effect=server.RequestError(503,"暂时不可用")):
            result=server.order_chat({"message":"帽子抽绳需要收紧，推荐什么？","draft":self.ready})
        self.assertTrue(result.get("next_item"))
        self.assertIn("暂时无法连接AI",result["reply"])
        self.assertEqual(result["draft"]["customer"], "测试客户")


if __name__ == "__main__":
    unittest.main()
