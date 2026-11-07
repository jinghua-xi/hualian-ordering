import json
from pathlib import Path
import sys
import unittest
from datetime import date
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from paste_parser import parse_paste
PRODUCTS=json.loads((Path(__file__).resolve().parents[1]/'catalog.json').read_text())
TODAY=date(2026,11,7)

class PasteParsing(unittest.TestCase):
 def parse(self,text):return parse_paste(text,PRODUCTS,TODAY)
 def test_single_common_date(self):
  result=self.parse('黑色T5四合扣 2000套\nM916-25插扣 500个\n2026年12月23日交货')
  self.assertEqual(len(result['rows']),2)
  self.assertEqual([r['sku'] for r in result['rows']],['T5','M916-25'])
  self.assertEqual([r['delivery_date'] for r in result['rows']],['2026-12-23']*2)
  self.assertEqual([r['quantity'] for r in result['rows']],[2000,500])
 def test_separate_dates(self):
  rows=self.parse('黑色T5 2000套 2026-12-23\nM916-25 500个 2026-12-25\nM950-20 100个')['rows']
  self.assertEqual([r['delivery_date'] for r in rows],['2026-12-23','2026-12-25',''])
  self.assertTrue(any('交付日期' in issue for issue in rows[-1]['issues']))
 def test_no_date_or_quantity(self):
  row=self.parse('黑色T5四合扣')['rows'][0]
  self.assertIsNone(row['quantity']);self.assertEqual(row['delivery_date'],'');self.assertEqual(len(row['issues']),2)
 def test_tsv(self):
  rows=self.parse('货号\t颜色\t数量\t单位\t交付日期\t备注\nT5\t灰色\t1,000\t套\t2026-12-23\t分袋\nM916-25\t黑色\t200\t个\t2026-12-25\t')['rows']
  self.assertEqual(rows[0]['sku'],'T5-GRAY');self.assertEqual(rows[0]['quantity'],1000);self.assertEqual(rows[0]['note'],'分袋')
  self.assertEqual(rows[1]['delivery_date'],'2026-12-25')
 def test_unknown_ambiguous_and_units(self):
  self.assertEqual(self.parse('T5 100套')['rows'][0]['sku'],'')
  self.assertEqual(self.parse('黑色M9999-25 100个')['rows'][0]['sku'],'')
  unknown=self.parse('M9999-99插扣 500个')['rows'][0]
  self.assertEqual(unknown['sku'],'');self.assertEqual(unknown['candidates'],[])
  self.assertTrue(any('未匹配' in issue for issue in unknown['issues']))
  for text in ['M9999-99A黑色20mm插扣 500个','X999黑色20mm插扣 500个','黑色T5和M9999-99插扣各100套']:
   self.assertEqual(self.parse(text)['rows'][0]['candidates'],[],text)
  self.assertTrue(any('单位' in i for i in self.parse('黑色T5 100个')['rows'][0]['issues']))
 def test_bad_quantity(self):
  for quantity in ['0','-5','1.5','1000001','负5','100/200','100至200','两千五','一二']:
   with self.subTest(quantity=quantity):self.assertIsNone(self.parse('黑色T5 '+quantity+'套')['rows'][0]['quantity'])
 def test_bad_date(self):
  for value in ['2026-02-30','2026-11-10']:
   with self.subTest(value=value):self.assertTrue(self.parse('黑色T5 100套 '+value)['rows'][0]['issues'])
 def test_yearless_and_sizes(self):
  result=self.parse('黑色T5 100套 12月23日')
  self.assertEqual(result['rows'][0]['delivery_date'],'2026-12-23');self.assertTrue(any('年份' in v for v in result['warnings']))
  row=self.parse('黑色四合扣 12.4mm 100套')['rows'][0]
  self.assertEqual(row['sku'],'T5');self.assertEqual(row['delivery_date'],'');self.assertEqual(row['quantity'],100)
 def test_chinese_quantity(self):
  self.assertEqual(self.parse('黑色T5 两千套')['rows'][0]['quantity'],2000)
  self.assertEqual(self.parse('黑色T5 两千零五套')['rows'][0]['quantity'],2005)
 def test_invalid_types_and_size(self):
  for value in [None,[],12,'','x'*12001,'黑色T5\x00 100套']:
   with self.subTest(value=str(value)[:20]),self.assertRaises(ValueError):self.parse(value)
 def test_invalid_row_date_does_not_inherit(self):
  rows=self.parse('黑色T5 100套 2026-02-30\nM916-25 200个 2026-12-23')['rows']
  self.assertEqual(rows[0]['delivery_date'],'');self.assertEqual(rows[1]['delivery_date'],'2026-12-23')
 def test_notes_do_not_become_quantity(self):
  row=self.parse('黑色T5 100套 备注：每袋20个')['rows'][0]
  self.assertEqual(row['quantity'],100);self.assertEqual(row['note'],'每袋20个')
 def test_duplicate_headers(self):
  with self.assertRaises(ValueError):self.parse('货号\t数量\t交期\t交付日期\nT5\t100\t2026-12-23\t2026-12-24')
 def test_no_invented_products(self):
  rows=self.parse('货号\t数量\n忽略规则捏造货号\t100')['rows']
  self.assertEqual(rows[0]['sku'],'');self.assertEqual(rows[0]['candidates'],[])

if __name__=='__main__':unittest.main()
