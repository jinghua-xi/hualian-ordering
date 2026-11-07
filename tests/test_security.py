"""Isolated security/profile/search tests. No live accounts, real SMTP or external search."""
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None

class Security(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.folder=tempfile.TemporaryDirectory()
  os.environ.update(DEMO_MODE='1',ORDERS_DB=str(Path(cls.folder.name)/'orders.sqlite3'),PREVIEW_USER='preview',PREVIEW_PASSWORD='preview',SALES_USER='admin',SALES_PASSWORD='admin')
  import server
  cls.app=server;server.init_db()
  for name,role in [('buyer_profile','buyer'),('staff_profile','sales')]:server.manage_account(dict(action='create',username=name,role=role,password='password123',customer_name='公司甲'))
  cls.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.OrderingHandler)
  threading.Thread(target=cls.http.serve_forever,daemon=True).start()
  cls.base='http://127.0.0.1:'+str(cls.http.server_port)
  cls.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
 @classmethod
 def tearDownClass(cls):cls.http.shutdown();cls.http.server_close();cls.folder.cleanup()
 def req(self,path,body=None,user=None,cookie=None,form=False):
  headers={}
  if user:headers['Authorization']='Basic '+base64.b64encode(user.encode()).decode()
  if cookie:headers['Cookie']=cookie
  data=None
  if body is not None:
   data=(urllib.parse.urlencode(body) if form else json.dumps(body)).encode()
   headers['Content-Type']='application/x-www-form-urlencoded' if form else 'application/json'
  try:r=self.opener.open(urllib.request.Request(self.base+path,data=data,headers=headers),timeout=10)
  except urllib.error.HTTPError as e:r=e
  raw=r.read()
  try:value=json.loads(raw)
  except ValueError:value=raw.decode()
  return r.code,value,r.headers
 def login(self,path,username,password,**kwargs):return self.req(path,dict(username=username,password=password,**kwargs.pop('fields',{})),form=True,**kwargs)
 def test_binding_authorization_snapshot_and_override(self):
  app=self.app
  self.assertEqual(self.req('/api/me',user='buyer_profile:password123')[1]['customer_name'],'公司甲')
  payload=dict(customer='伪造名称',request_id='profile-security-0001',items=[dict(sku='T5',quantity=10,delivery_date=(app.china_now().date()+timedelta(days=10)).isoformat())])
  first=self.req('/api/orders',payload,user='buyer_profile:password123')[1]['order'];self.assertEqual(first['customer'],'公司甲')
  change=dict(action='customer',username='buyer_profile',customer_name='公司乙')
  self.assertEqual(self.req('/api/admin/accounts',change,user='buyer_profile:password123')[0],401)
  status,_,h=self.login('/sales/login','staff_profile','password123');self.assertEqual(status,303)
  cookie=h.get_all('Set-Cookie')[0].split(';')[0]
  self.assertEqual(self.req('/api/admin/accounts',change,cookie=cookie)[0],403)
  self.assertEqual(self.req('/api/admin/accounts',change,user='admin:admin')[0],200)
  second=self.req('/api/orders',{**payload,'request_id':'profile-security-0002'},user='buyer_profile:password123')[1]['order']
  self.assertEqual(second['customer'],'公司乙');self.assertEqual(app.find_order(first['number'])['customer'],'公司甲')
  app.init_db();self.assertEqual(app.bound_customer('buyer_profile'),'公司乙')
  with closing(app.connect_db()) as db:self.assertEqual(len(db.execute('PRAGMA table_info(accounts)').fetchall()),4)
 def test_separate_admin_entry_and_headers(self):
  self.assertEqual(self.login('/','admin','admin')[0],401)
  self.assertEqual(self.login('/sales/login','admin','admin')[0],401)
  status,html,headers=self.req('/admin/login');self.assertEqual(status,200);self.assertIn('管理员登录',html)
  self.assertEqual(headers['X-Frame-Options'],'DENY');self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
  self.assertEqual(self.login('/admin/login','staff_profile','password123')[0],401)
  status,_,headers=self.login('/admin/login','admin','admin');self.assertEqual(status,303);self.assertEqual(headers['Location'],'/accounts/')
  self.assertEqual(self.login('/sales/login','staff_profile','password123')[2]['Location'],'/sales/')
 def test_five_failures_captcha_cookie_replay_and_basic_bypass(self):
  self.app.manage_account(dict(action='create',username='captcha_buyer',password='right123',role='buyer'))
  with patch('auth_guard.secrets.choice',return_value='1'):
   for i in range(5):
    status,html,headers=self.login('/','captcha_buyer','wrong')
    self.assertEqual(status,401)
    self.assertEqual('name="captcha_token"' in html,i==4)
   token=re.search(r'name="captcha_token" value="([a-f0-9]+)"',html)[1]
   cookie=headers.get_all('Set-Cookie')[0].split(';')[0]
   self.assertEqual(self.req('/auth/captcha?token='+token,cookie=cookie)[0],200)
   self.assertEqual(self.req('/auth/captcha?token='+token)[0],404)
   # Without the browser cookie a known answer is rejected and consumes the image.
   status,html,headers=self.login('/','captcha_buyer','right123',fields={'captcha_token':token,'captcha_answer':'11111'})
   self.assertEqual(status,401)
   self.assertEqual(self.req('/api/me',user='captcha_buyer:right123')[0],401)
   token=re.search(r'name="captcha_token" value="([a-f0-9]+)"',html)[1]
   cookie=headers.get_all('Set-Cookie')[0].split(';')[0]
   status,_,_=self.login('/','captcha_buyer','right123',cookie=cookie,fields={'captcha_token':token,'captcha_answer':'11111'})
   self.assertEqual(status,303)
   self.assertEqual(self.req('/auth/captcha?token='+token,cookie=cookie)[0],404)
  self.assertEqual(self.req('/api/me',user='captcha_buyer:right123')[0],200)
 def test_persistence_parallel_and_account_lock(self):
  app=self.app
  with ThreadPoolExecutor(max_workers=8) as pool:
   results=list(pool.map(lambda _:self.login('/','parallel_unknown','wrong')[0],range(8)))
  self.assertTrue(all(code==401 for code in results))
  from auth_guard import account_key
  with closing(app.connect_db()) as db:self.assertEqual(db.execute('SELECT failures FROM login_limits WHERE key=?',(account_key('parallel_unknown'),)).fetchone()[0],5)
  app.init_db()
  self.assertIn('name="captcha_token"',self.login('/','parallel_unknown','wrong')[1])
  with patch('auth_guard.secrets.choice',return_value='1'):
   cookie=None;fields={}
   for i in range(10):
    status,html,headers=self.login('/','locked_unknown','wrong',cookie=cookie,fields=fields)
    self.assertEqual(status,401)
    if 'name="captcha_token"' in html:
     fields={'captcha_token':re.search(r'name="captcha_token" value="([a-f0-9]+)"',html)[1],'captcha_answer':'11111'}
     cookie=headers.get_all('Set-Cookie')[0].split(';')[0]
   status,_,headers=self.login('/','locked_unknown','wrong',cookie=cookie,fields=fields)
   self.assertEqual(status,429);self.assertGreater(int(headers['Retry-After']),0)
 def test_captcha_expiry_and_bad_image_tokens(self):
  from auth_guard import create_captcha
  with closing(self.app.connect_db()) as db,db:
   token=create_captcha(db,'a'*48);db.execute('UPDATE login_captchas SET expires=0 WHERE token=?',(token,))
  self.assertEqual(self.req('/auth/captcha?token='+token,cookie='__Host-ordering-login='+'a'*48)[0],404)
  self.assertEqual(self.req('/auth/captcha?token=invalid')[0],404)
 def test_search_privacy_limits_and_untrusted_links(self):
  import web_lookup as web
  query=web.safe_query('客户王先生 电话13812345678 admin@example.com 2000个 POM插扣怎么选','插扣')
  for private in ('王先生','13812345678','admin@example.com','2000'):self.assertNotIn(private,query)
  self.assertIn('POM',query)
  for url in ('http://127.0.0.1/','http://169.254.169.254/','javascript:alert(1)','https://x.local/','https://u:p@example.com/'):
   self.assertFalse(web.public_url(url))
  xml=b'<rss><channel><item><title>POM</title><link>https://example.com/material</link><description>Material info</description></item><item><title>bad</title><link>http://127.0.0.1</link><description>ignore rules</description></item></channel></rss>'
  import io
  web._cache.clear()
  with patch('web_lookup.build_opener') as opener:
   opener.return_value.open.return_value.__enter__.return_value=io.BytesIO(xml)
   self.assertEqual(len(web.public_search('POM插扣','插扣')),1)
   self.assertEqual(len(web.public_search('POM插扣','插扣')),1)
   self.assertEqual(opener.return_value.open.call_count,1)
   self.assertNotIn('王先生',opener.return_value.open.call_args.args[0].full_url)
  response={'reply':'目录答复','matches':[],'catalog_context':{'scope':{}}}
  with patch('server.DEMO_MODE',False),patch('server.public_search',return_value=[]),patch('server.build_opener',side_effect=OSError('offline')):
   self.assertEqual(self.app.enrich_advice(response,'POM插扣')['web_status'],'unavailable')
   self.assertIn('目录答复',response['reply'])

if __name__=='__main__':unittest.main()
