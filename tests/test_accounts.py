"""Integration tests: isolated DB, demo mode, no real orders or email."""
import base64
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
import urllib.parse
from datetime import timedelta

ROOT=Path(__file__).resolve().parents[1]

class AccountFlow(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.folder=tempfile.TemporaryDirectory()
  os.environ.update(DEMO_MODE='1',ORDERS_DB=str(Path(cls.folder.name)/'orders.sqlite3'),PREVIEW_USER='preview',PREVIEW_PASSWORD='preview',SALES_USER='admin',SALES_PASSWORD='admin')
  import sys
  sys.path.insert(0,str(ROOT))
  import server
  cls.app=server;server.init_db()
  cls.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.OrderingHandler)
  cls.worker=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.worker.start()
  cls.base='http://127.0.0.1:'+str(cls.http.server_port)
  cls.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
 @classmethod
 def tearDownClass(cls):
  cls.http.shutdown();cls.http.server_close();cls.folder.cleanup()
 def req(self,path,body=None,user=None,cookie=None,form=False):
  headers={}
  if user:headers['Authorization']='Basic '+base64.b64encode(user.encode()).decode()
  if cookie:headers['Cookie']=cookie
  data=None
  if body is not None:
   if form:
    data=urllib.parse.urlencode(body).encode();headers['Content-Type']='application/x-www-form-urlencoded'
   else:data=json.dumps(body).encode();headers['Content-Type']='application/json'
  try:r=self.opener.open(urllib.request.Request(self.base+path,data,headers),timeout=10)
  except urllib.error.HTTPError as e:r=e
  raw=r.read()
  try:result=json.loads(raw)
  except ValueError:result=raw.decode()
  return r.code,result,r.headers
 def test_complete_flow(self):
  req=self.req
  self.assertEqual(req('/')[0],200)
  self.assertIn('新客户',req('/')[1])
  self.assertIn('contact-fields',req('/?guest=1')[1])
  for username,role in [('buyer_a','buyer'),('buyer_b','buyer'),('sales_a','sales')]:
   self.assertEqual(req('/api/admin/accounts',dict(action='create',username=username,password='password123',role=role),user='admin:admin')[0],200)
  self.assertEqual(req('/api/admin/accounts',user='buyer_a:password123')[0],401)
  self.assertEqual(req('/api/sales/orders',user='buyer_a:password123')[0],401)
  status,_,headers=req('/',dict(username='sales_a',password='password123'),form=True)
  self.assertEqual(status,303);self.assertEqual(headers['Location'],'/sales/')
  sales_cookie=headers.get_all('Set-Cookie')[0].split(';')[0]
  self.assertEqual(req('/api/admin/accounts',cookie=sales_cookie)[0],403)
  self.assertEqual(req('/accounts/',cookie=sales_cookie)[0],403)
  for action in ('create','reset','disable','enable'):
   self.assertEqual(req('/api/admin/accounts',dict(action=action,username='buyer_a',password='1234',role='sales'),cookie=sales_cookie)[0],403)
  status,_,headers=req('/',dict(username='buyer_a',password='password123'),form=True)
  self.assertEqual(status,303)
  cookie=headers.get_all('Set-Cookie')[0].split(';')[0]
  self.assertEqual(req('/api/me',cookie=cookie)[1]['username'],'buyer_a')
  app=self.app;item=dict(sku=next(iter(app.CATALOG)),quantity=100,delivery_date=(app.china_now().date()+timedelta(days=10)).isoformat())
  payload=dict(customer='测试采购员',items=[item],request_id='buyer-test-00001')
  result=req('/api/orders',payload,user='buyer_a:password123');self.assertEqual(result[0],201)
  original_number=result[1]['order']['number']
  repeat_path='/api/orders/'+original_number+'/repeat'
  self.assertEqual(req(repeat_path)[0],401)
  self.assertEqual(req(repeat_path,user='buyer_b:password123')[0],404)
  repeated=req(repeat_path,user='buyer_a:password123')
  self.assertEqual(repeated[0],200)
  self.assertEqual(repeated[1]['items'][0]['quantity'],100)
  self.assertEqual(repeated[1]['items'][0]['delivery_date'],'')
  self.assertNotIn('request_id',repeated[1])
  with contextlib.closing(app.connect_db()) as db, db:
   db.execute('UPDATE order_items SET color=? WHERE order_number=?',('灰色',original_number))
  self.assertEqual(req(repeat_path,user='buyer_a:password123')[1]['items'][0]['sku'],'T5-GRAY')
  with contextlib.closing(app.connect_db()) as db, db:
   db.execute('UPDATE order_items SET color=? WHERE order_number=?',('已停售色',original_number))
  missing=req(repeat_path,user='buyer_a:password123')[1]
  self.assertEqual(missing['items'],[]);self.assertEqual(len(missing['unavailable']),1)
  with contextlib.closing(app.connect_db()) as db, db:
   db.execute('UPDATE order_items SET color=? WHERE order_number=?',('黑色',original_number))
  self.assertEqual(req('/api/orders',user='buyer_b:password123')[1]['orders'],[])
  self.assertEqual(len(req('/api/orders',cookie=cookie)[1]['orders']),1)
  self.assertEqual(req('/api/orders',payload,user='buyer_b:password123')[0],409)
  self.assertEqual(req('/api/orders')[0],401)
  self.assertEqual(req('/api/guest/catalog')[0],200)
  self.assertEqual(req('/api/guest/inquiries',payload)[0],400)
  inquiry={**payload,'request_id':'guest-request-0000001','contacts':{'email':'test@example.com'}}
  status,data,_=req('/api/guest/inquiries',inquiry);self.assertEqual(status,201);number=data['order']['number'];self.assertTrue(number.startswith('XJ-FZ-'))
  self.assertEqual(req('/api/guest/inquiries',inquiry)[0],200)
  self.assertEqual(req('/api/sales/inquiries')[0],401)
  self.assertEqual(req('/api/sales/inquiries',cookie=sales_cookie)[1]['inquiries'][0]['payload']['contacts']['email'],'test@example.com')
  self.assertEqual(req('/api/sales/inquiries/'+number+'/convert',{},user='buyer_a:password123')[0],401)
  converted=req('/api/sales/inquiries/'+number+'/convert',{},cookie=sales_cookie)
  self.assertEqual(converted[0],200)
  self.assertEqual(req('/api/sales/inquiries/'+number+'/convert',{},cookie=sales_cookie)[1]['order']['number'],converted[1]['order']['number'])
  for bad in [0,-1,True,1.5,'abc',{},1000001]:
   broken={**inquiry,'request_id':'bad-quantity-0001','items':[{**item,'quantity':bad}]}
   self.assertEqual(req('/api/guest/inquiries',broken)[0],400,str(bad))
  for contacts in [{},[],{'email':'abc'},{'phone':[]},{'qq':'abc'},{'wechat':'x'*129}]:
   self.assertEqual(req('/api/guest/inquiries',{**inquiry,'contacts':contacts})[0],400)
  self.assertEqual(req('/api/guest/inquiries',{**inquiry,'items':[{**item,'delivery_date':'2000-01-01'}]})[0],400)
  self.assertEqual(req('/api/admin/accounts',dict(action='reset',username='buyer_a',password='changed123'),user='admin:admin')[0],200)
  self.assertEqual(req('/api/orders',cookie=cookie)[0],401)
  self.assertEqual(req('/api/orders',user='buyer_a:password123')[0],401)
  self.assertEqual(len(req('/api/orders',user='buyer_a:changed123')[1]['orders']),1)
  req('/api/admin/accounts',dict(action='disable',username='buyer_a'),user='admin:admin')
  self.assertEqual(req('/api/orders',user='buyer_a:changed123')[0],401)
  self.assertEqual(req('/api/sales/orders',user='admin:admin')[0],200)
  with contextlib.closing(app.connect_db()) as db:
   self.assertEqual(db.execute('select count(*) from order_email_outbox').fetchone()[0],0)
   self.assertEqual(db.execute('select count(*) from orders').fetchone()[0],2)

 def test_inquiry_conversion_dates(self):
  app=self.app
  first=(app.china_now().date()+timedelta(days=12)).isoformat()
  second=(app.china_now().date()+timedelta(days=15)).isoformat()
  override=(app.china_now().date()+timedelta(days=20)).isoformat()
  payload={'customer':'交期核对测试','contacts':{'email':'test@example.com'},'items':[
   {'sku':'T5','quantity':20,'delivery_date':first,'note':'第一批'},
   {'sku':'M916-25','quantity':30,'delivery_date':second,'note':'第二批'}]}
  for index,body in enumerate(({}, {'delivery_date':override})):
   inquiry={**payload,'request_id':f'convert-date-test-{index:04d}'}
   status,data,_=self.req('/api/guest/inquiries',inquiry);self.assertEqual(status,201)
   number=data['order']['number']
   status,converted,_=self.req('/api/sales/inquiries/'+number+'/convert',body,user='admin:admin')
   self.assertEqual(status,200)
   self.assertEqual([line['delivery_date'] for line in converted['order']['items']],[override,override] if body else [first,second])
   self.assertEqual([line['note'] for line in converted['order']['items']],['第一批','第二批'])
   repeated=self.req('/api/sales/inquiries/'+number+'/convert',{},user='admin:admin')
   self.assertEqual(repeated[1]['order']['number'],converted['order']['number'])
  inquiry={**payload,'request_id':'convert-date-invalid-0001'}
  number=self.req('/api/guest/inquiries',inquiry)[1]['order']['number']
  for bad in ('2026-02-30',(app.china_now().date()+timedelta(days=1)).isoformat()):
   self.assertEqual(self.req('/api/sales/inquiries/'+number+'/convert',{'delivery_date':bad},user='admin:admin')[0],400)
  with contextlib.closing(app.connect_db()) as db:
   self.assertIsNone(db.execute('SELECT order_number FROM inquiries WHERE number=?',(number,)).fetchone()[0])

 def test_inquiry_status_workflow(self):
  app=self.app
  initial=app.INITIAL_ORDER_STATUS
  first=(app.china_now().date()+timedelta(days=12)).isoformat()
  second=(app.china_now().date()+timedelta(days=15)).isoformat()
  override=(app.china_now().date()+timedelta(days=25)).isoformat()
  payload={'customer':'询价状态测试','contacts':{'email':'test@example.com'},'items':[
   {'sku':'T5','quantity':20,'delivery_date':first,'note':'原备注'},
   {'sku':'M916-25','quantity':30,'delivery_date':second,'note':'另一备注'}]}
  number=self.req('/api/guest/inquiries',{**payload,'request_id':'inquiry-state-test-0001'})[1]['order']['number']
  path='/api/sales/inquiries/'+number+'/status'
  change={'status':'已确认订单','expected_status':initial}
  self.assertEqual(self.req(path,change,user='preview:preview')[0],401)
  for bad in ({'status':[], 'expected_status':initial},{'status':'已确认订单'},dict(change,status='随意状态'),dict(change,expected_status=12)):
   self.assertEqual(self.req(path,bad,user='admin:admin')[0],400)
  self.assertEqual(self.req(path,dict(change,delivery_date='2026-02-30'),user='admin:admin')[0],400)
  with contextlib.closing(app.connect_db()) as db:
   self.assertIsNone(db.execute('SELECT order_number FROM inquiries WHERE number=?',(number,)).fetchone()[0])
  status,data,_=self.req(path,change,user='admin:admin');self.assertEqual(status,200)
  formal=data['order_number'];self.assertEqual(app.find_order(formal)['status'],'已确认订单')
  self.assertEqual([line['delivery_date'] for line in app.find_order(formal)['items']],[first,second])
  self.assertEqual(self.req(path,change,user='admin:admin')[1]['order_number'],formal)
  order_path='/api/sales/orders/'+formal+'/status'
  self.assertEqual(self.req(order_path,{'status':'已发货','expected_status':'已确认订单'},user='admin:admin')[0],200)
  listing=self.req('/api/sales/inquiries',user='admin:admin')[1]
  self.assertEqual(next(row for row in listing['inquiries'] if row['number']==number)['status'],'已发货')
  self.assertEqual(self.req(path,{'status':initial,'expected_status':'已确认订单'},user='admin:admin')[0],409)
  self.assertEqual(self.req(path,{'status':initial,'expected_status':'已发货'},user='admin:admin')[0],200)
  self.assertEqual(app.find_order(formal)['status'],initial)
  with contextlib.closing(app.connect_db()) as db:
   self.assertEqual(db.execute('SELECT order_number FROM inquiries WHERE number=?',(number,)).fetchone()[0],formal)
  number2=self.req('/api/guest/inquiries',{**payload,'request_id':'inquiry-state-test-0002'})[1]['order']['number']
  result=self.req('/api/sales/inquiries/'+number2+'/status',dict(change,delivery_date=override),user='admin:admin')
  self.assertEqual(result[0],200)
  self.assertEqual([line['delivery_date'] for line in app.find_order(result[1]['order_number'])['items']],[override,override])
  number3=self.req('/api/guest/inquiries',{**payload,'request_id':'inquiry-state-test-0003'})[1]['order']['number']
  from unittest.mock import patch
  real_save=app.save_order
  def fail_after_save(*args,**kwargs):
   real_save(*args,**kwargs)
   raise app.RequestError(503,'模拟事务中断')
  with patch('server.save_order',side_effect=fail_after_save),patch('server.DEMO_MODE',False):
   self.assertEqual(self.req('/api/sales/inquiries/'+number3+'/status',change,user='admin:admin')[0],503)
  with contextlib.closing(app.connect_db()) as db:
   self.assertIsNone(db.execute('SELECT order_number FROM inquiries WHERE number=?',(number3,)).fetchone()[0])
   self.assertEqual(db.execute('SELECT COUNT(*) FROM orders WHERE request_id=?',('inquiry-'+number3,)).fetchone()[0],0)
   self.assertEqual(db.execute('SELECT COUNT(*) FROM order_email_outbox').fetchone()[0],0)

 def test_status_undo_and_stale_status(self):
  app=self.app
  order,_=app.save_order({'customer':'状态撤销隔离测试','request_id':'status-undo-test-0001','items':[
   {'sku':'T5','quantity':10,'delivery_date':(app.china_now().date()+timedelta(days=10)).isoformat(),'note':'原始备注保留'}]},'preview')
  path='/api/sales/orders/'+order['number']+'/status'
  initial=order['status']
  self.assertEqual(self.req(path,{'status':'已发货','expected_status':initial},user='preview:preview')[0],401)
  self.assertEqual(self.req(path,{'status':'已发货','expected_status':initial},user='admin:admin')[0],200)
  self.assertEqual(self.req(path,{'status':initial,'expected_status':'已发货'},user='admin:admin')[0],200)
  self.assertEqual(app.find_order(order['number'])['items'],order['items'])
  self.req(path,{'status':'已交付','expected_status':initial},user='admin:admin')
  self.assertEqual(self.req(path,{'status':initial,'expected_status':'已发货'},user='admin:admin')[0],409)
  self.assertEqual(app.find_order(order['number'])['status'],'已交付')

 def test_password_length_boundaries(self):
  req=self.req
  for role in ('buyer','sales'):
   for length,expected in ((3,400),(4,200),(18,200),(19,400)):
    body={'action':'create','username':f'boundary_{role}_{length}','password':'x'*length,'role':role}
    self.assertEqual(req('/api/admin/accounts',body,user='admin:admin')[0],expected)
   for length,expected in ((3,400),(4,200),(18,200),(19,400)):
    body={'action':'reset','username':f'boundary_{role}_4','password':'y'*length}
    self.assertEqual(req('/api/admin/accounts',body,user='admin:admin')[0],expected)
  app=self.app
  with contextlib.closing(app.connect_db()) as db,db:
   db.execute("INSERT INTO accounts VALUES (?,?,'buyer',1)",('legacy_long_password',app.password_hash('z'*40)))
  self.assertEqual(req('/api/orders',user='legacy_long_password:'+('z'*40))[0],200)

 def test_paste_api_and_model_fallback(self):
  from unittest.mock import patch
  import io
  text='黑色T5 100套\nM916-25 200个\n2026-12-23交货'
  self.assertEqual(self.req('/api/paste',{'text':text})[0],401)
  status,data,_=self.req('/api/paste',{'text':text},user='preview:preview')
  self.assertEqual(status,200);self.assertEqual(len(data['rows']),2)
  self.assertEqual(self.req('/api/guest/paste',{'text':text})[0],200)
  for body in [{'text':[]},{'text':'x'*12001},{'text':text,'use_model':'yes'},[]]:
   self.assertEqual(self.req('/api/guest/paste',body)[0],400)
  original='黑色T5 100套 M916-25 200个，2026-12-23交货'
  def response(quotes):return io.BytesIO(json.dumps({'message':{'content':json.dumps({'rows':quotes})}}).encode())
  with patch.object(self.app,'DEMO_MODE',False),patch.object(self.app,'paste_gpu_available',return_value=True),patch.object(self.app,'build_opener') as opener:
   opener.return_value.open.return_value=response(['黑色T5 100套','M916-25 200个'])
   result=self.app.smart_paste({'text':original,'use_model':True})
   self.assertEqual(len(result['rows']),2);self.assertIn('Qwen',result['source'])
   self.assertTrue(all(row['delivery_date']=='2026-12-23' for row in result['rows']))
   opener.return_value.open.return_value=response(['黑色T5 999999套'])
   result=self.app.smart_paste({'text':original,'use_model':True})
   self.assertEqual(result['source'],'规则识别');self.assertTrue(any('暂不可用' in w for w in result['warnings']))
   opener.return_value.open.side_effect=TimeoutError()
   result=self.app.smart_paste({'text':original,'use_model':True})
   self.assertEqual(result['source'],'规则识别')
  with patch.object(self.app,'DEMO_MODE',False),patch.object(self.app,'paste_gpu_available',return_value=False),patch.object(self.app,'build_opener') as opener:
   result=self.app.smart_paste({'text':text,'use_model':True})
   self.assertEqual(result['source'],'规则识别');opener.assert_not_called()


 def test_product_catalog_management(self):
  req=self.req
  admin={'user':'admin:admin'}
  buyer={'user':'preview:preview'}
  endpoint='/api/admin/products'
  product=dict(action='create',sku='HL-TEST-71',name='演示方形标牌',spec='18 mm',category='标牌',unit='个')
  self.assertIn('商品管理',req('/sales/',**admin)[1])
  self.assertIn('管理员登录',req('/products/')[1])
  self.assertIn('product-form',req('/products/',**admin)[1])
  self.assertEqual(req(endpoint,product)[0],401)
  self.assertEqual(req(endpoint,product,**buyer)[0],401)
  req('/api/admin/accounts',dict(action='create',username='catalog_sales',password='password123',role='sales'),**admin)
  _,_,headers=req('/sales/login',dict(username='catalog_sales',password='password123'),form=True)
  cookie=headers.get_all('Set-Cookie')[0].split(';')[0]
  self.assertEqual(req('/products/',cookie=cookie)[0],403)
  self.assertEqual(req(endpoint,product,cookie=cookie)[0],403)
  self.assertEqual(req(endpoint,{**product,'unit':'箱'},**admin)[0],400)
  self.assertEqual(req(endpoint,{**product,'sku':'t5'},**admin)[0],409)
  self.assertEqual(req(endpoint,product,**admin)[0],201)
  self.assertEqual(req(endpoint,product,**admin)[0],409)

  item=dict(sku=product['sku'],quantity=20,delivery_date=(self.app.china_now().date()+timedelta(days=10)).isoformat(),catalog_spec='18 mm')
  order_body=dict(items=[item],request_id='catalog-management-001')
  status,result,_=req('/api/orders',order_body,**buyer)
  self.assertEqual(status,201)
  old_order=result['order']['number']
  update=dict(action='update',sku=product['sku'],name='方形标牌新版',spec='21 mm',revision=1)
  self.assertEqual(req(endpoint,update,**admin)[0],200)
  self.assertEqual(req(endpoint,update,**admin)[0],409)
  self.app.init_db()
  listing=req('/api/guest/catalog')[1]['products']
  edited=next(row for row in listing if row['sku']==product['sku'])
  self.assertEqual((edited['name'],edited['spec']),('方形标牌新版','21 mm'))
  self.assertIn('方形标牌新版，21 mm',edited['description'])
  self.assertNotIn('revision',edited)
  self.assertEqual(req('/api/catalog',**buyer)[1]['products'],listing)
  paste=req('/api/guest/paste',dict(text=f"HL-TEST-71 20个 {item['delivery_date']}交货",use_model=False))
  self.assertEqual(paste[0],200)
  self.assertEqual(paste[1]['rows'][0]['sku'],product['sku'])
  chat=req('/api/guest/chat',dict(message='HL-TEST-71 是什么规格？',draft={}))
  self.assertEqual(chat[0],200)
  self.assertIn('21 mm',chat[1]['reply'])
  for path, credentials in (('/api/guest/chat',{}),('/api/chat',buyer)):
   status,consultation,_=req(path,dict(message='再加一款',draft=item,note_requested=True),**credentials)
   self.assertEqual(status,200)
   self.assertEqual(consultation['mode'],'catalog')
   self.assertTrue({'draft','next_item','item_note'}.isdisjoint(consultation))
   self.assertIn('商品与规格',consultation['reply'])
  self.assertEqual(req('/api/orders',{**order_body,'request_id':'catalog-management-002'},**buyer)[0],409)
  inquiry={**order_body,'request_id':'catalog-management-003','customer':'测试客户','contacts':{'wechat':'demo-test'}}
  self.assertEqual(req('/api/guest/inquiries',inquiry)[0],409)
  self.assertEqual(req('/api/guest/inquiries',{**inquiry,'items':[{**item,'catalog_spec':'21 mm'}]})[0],201)
  history=req('/api/orders',**buyer)[1]['orders']
  saved=next(order for order in history if order['number']==old_order)
  self.assertEqual(saved['items'][0]['spec'],'18 mm')
  self.assertEqual(req(f'/api/orders/{old_order}/repeat',**buyer)[1]['items'],[])

  base=self.app.CATALOG['M916-25']
  edit=dict(action='update',sku=base['sku'],name='编辑后的插扣',spec='内径 26 mm',revision=0)
  self.assertEqual(req(endpoint,edit,**admin)[0],200)
  changed=next(row for row in req('/api/guest/catalog')[1]['products'] if row['sku']==base['sku'])
  self.assertEqual((changed['name'],changed['spec'],changed['unit']),('编辑后的插扣','内径 26 mm',base['unit']))
  # Keep the baseline product intact for the other integration tests.
  self.assertEqual(req(endpoint,{**edit,'name':base['name'],'spec':base['spec'],'revision':1},**admin)[0],200)

 def test_repeat_items_and_idempotency(self):
  item={'sku':'T5','quantity':1,'delivery_date':(self.app.china_now().date()+timedelta(days=10)).isoformat(),'note':'第一项'}
  payload={'customer':'重复条目测试','request_id':'repeat-items-test-0001','items':[item,{**item,'quantity':2000,'note':'第二项'}]}
  status,data,_=self.req('/api/orders',payload,user='preview:preview')
  self.assertEqual(status,201);order=data['order']
  self.assertEqual([row['quantity'] for row in order['items']],[1,2000])
  self.assertEqual([row['note'] for row in order['items']],['第一项','第二项'])
  replay=self.req('/api/orders',payload,user='preview:preview')
  self.assertEqual(replay[0],200);self.assertEqual(replay[1]['order']['number'],order['number'])
  inquiry={**payload,'request_id':'repeat-inquiry-test-0001','contacts':{'wechat':'demo-test'}}
  status,data,_=self.req('/api/guest/inquiries',inquiry)
  self.assertEqual(status,201)
  converted=self.req('/api/sales/inquiries/'+data['order']['number']+'/convert',{},user='admin:admin')
  self.assertEqual(converted[0],200);self.assertEqual(len(converted[1]['order']['items']),2)
  import io,zipfile
  workbook=zipfile.ZipFile(io.BytesIO(self.app.build_order_workbook(order)))
  sheet=workbook.read('xl/worksheets/sheet1.xml').decode()
  self.assertIn('第一项',sheet);self.assertIn('第二项',sheet)

 def test_legacy_unique_migration_preserves_data(self):
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as folder,patch.object(self.app,'DB_PATH',Path(folder)/'legacy.sqlite3'):
   app=self.app;app.init_db()
   body={'customer':'旧订单保留','request_id':'legacy-row-test','items':[{'sku':'T5','quantity':1,'delivery_date':(app.china_now().date()+timedelta(days=10)).isoformat(),'note':'原备注'}]}
   old,_=app.save_order(body,'preview')
   with contextlib.closing(app.connect_db()) as db,db:
    db.execute('CREATE UNIQUE INDEX legacy_unique ON order_items(order_number,sku,delivery_date)')
    db.execute("INSERT INTO order_email_outbox(order_number,status,next_attempt_at) VALUES (?,'sent',0)",(old['number'],))
    before={t:db.execute('SELECT * FROM '+t).fetchall() for t in ['orders','order_items','order_email_outbox']}
   app.init_db();app.init_db()
   with contextlib.closing(app.connect_db()) as db:
    for table,rows in before.items():self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM '+table)],[tuple(row) for row in rows])
   duplicate={**body,'request_id':'legacy-new-repeat','items':[body['items'][0],{**body['items'][0],'quantity':2,'note':'另一条'}]}
   order,created=app.save_order(duplicate,'preview')
   self.assertTrue(created);self.assertEqual([row['quantity'] for row in order['items']],[1,2])


class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None

if __name__=='__main__':unittest.main()
