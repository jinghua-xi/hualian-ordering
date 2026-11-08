"""Deletion integration tests: isolated databases, no real orders or email."""
import base64
from contextlib import closing
from datetime import timedelta
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as app


class RecordDeletion(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.config = patch.multiple(app, DB_PATH=Path(self.folder.name) / 'orders.sqlite3',
                                     DEMO_MODE=True, PREVIEW_USER='preview', PREVIEW_PASSWORD='preview',
                                     SALES_USER='admin', SALES_PASSWORD='admin')
        self.config.start()
        app.init_db()
        app.manage_account({'action':'create', 'username':'staff', 'password':'staffpass', 'role':'sales'})
        self.http = app.ThreadingHTTPServer(('127.0.0.1', 0), app.OrderingHandler)
        self.worker = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.worker.start()
        self.base = 'http://127.0.0.1:' + str(self.http.server_port)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.counter = 0

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.config.stop()
        self.folder.cleanup()

    def req(self, path, body=None, user='admin:admin', cookie=None, method=None, **headers):
        if user:
            headers['Authorization'] = 'Basic ' + base64.b64encode(user.encode()).decode()
        if cookie:
            headers['Cookie'] = cookie
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            response = self.opener.open(request, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        raw = response.read()
        try:
            result = json.loads(raw)
        except ValueError:
            result = raw.decode()
        return response.status, result

    def payload(self):
        self.counter += 1
        return {'request_id':f'delete-isolated-test-{self.counter:04d}', 'customer':'隔离删除测试',
                'contacts':{'email':'test@example.com'}, 'items':[
                    {'sku':'T5', 'quantity':100, 'note':'保留原明细',
                     'delivery_date':(app.china_now().date() + timedelta(days=20)).isoformat()}]}

    def order(self):
        return app.save_order(self.payload(), 'preview')[0]

    def deletion(self, kind, number, status=app.INITIAL_ORDER_STATUS, **kwargs):
        return self.req(f'/api/sales/{kind}/{number}/delete',
                        {'confirm_number':number, 'expected_status':status}, **kwargs)

    def staff_cookie(self):
        # Use the existing signed-session format without a real browser or login CAPTCHA.
        nonce = 'd' * 32
        expires = int(time.time()) + 3600
        with closing(app.connect_db()) as db, db:
            db.execute('INSERT INTO account_sessions VALUES (?,?,?,?)', (nonce, 'staff', 'sales', expires))
        value = f'{expires}.{nonce}'
        signature = app.OrderingHandler.session_signature('sales', value)
        return f'__Host-ordering-sales={value}.{signature}'

    def test_permissions_validation_and_staff_deletion(self):
        order = self.order(); number = order['number']
        path = f'/api/sales/orders/{number}/delete'
        valid = {'confirm_number':number, 'expected_status':order['status']}
        for user in (None, 'preview:preview'):
            self.assertEqual(self.req(path, valid, user=user)[0], 401)
        for site in ('cross-site', 'same-site'):
            self.assertEqual(self.req(path, valid, **{'Sec-Fetch-Site':site})[0], 403)
        for body in ([], 'delete', {}, {'confirm_number':'wrong', 'expected_status':order['status']},
                     {'confirm_number':number, 'expected_status':0}):
            self.assertEqual(self.req(path, body)[0], 400)
        self.assertEqual(self.req(path, method='GET')[0], 404)
        self.assertIsNotNone(app.find_order(number))
        result = self.deletion('orders', number, user=None, cookie=self.staff_cookie())
        self.assertEqual(result[0], 200)
        with closing(app.connect_db()) as db:
            self.assertEqual(db.execute('SELECT deleted_by FROM record_deletions').fetchone()[0], 'staff')

    def test_order_hidden_but_original_data_and_identifiers_retained(self):
        payload = self.payload(); order = app.save_order(payload, 'preview')[0]
        other = self.order(); number = order['number']
        self.assertEqual(self.deletion('orders', number)[0], 200)
        self.assertEqual(self.deletion('orders', number)[0], 200)
        self.assertIsNone(app.find_order(number))
        self.assertEqual([r['number'] for r in app.sales_orders()], [other['number']])
        self.assertEqual([r['number'] for r in app.customer_orders('preview')], [other['number']])
        self.assertEqual(self.req('/api/sales/orders', user='admin:admin')[1]['orders'][0]['number'], other['number'])
        self.assertEqual(self.req(f'/api/orders/{number}/repeat', user='preview:preview')[0], 404)
        self.assertEqual(self.req(f'/sales/orders/{number}.xlsx')[0], 404)
        self.assertEqual(self.req(f'/api/sales/orders/{number}/status',
                                 {'status':'已发货', 'expected_status':order['status']})[0], 404)
        self.assertEqual(self.req('/api/orders', payload, user='preview:preview')[0], 409)
        app.init_db()
        self.assertIsNone(app.find_order(number))
        with closing(app.connect_db()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM orders').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT note FROM order_items WHERE order_number=?', (number,)).fetchone()[0], '保留原明细')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM record_deletions').fetchone()[0], 1)
        new_order = self.order()
        self.assertGreater(new_order['number'], other['number'])
        self.assertEqual(self.deletion('orders', 'HL-FZ-20000101-9999')[0], 404)

    def test_last_deleted_number_is_not_reused(self):
        first = self.order()
        self.deletion('orders', first['number'])
        self.assertEqual(int(self.order()['number'][-4:]), int(first['number'][-4:]) + 1)
        body = self.payload(); number = app.save_inquiry(body)[0]
        self.deletion('inquiries', number)
        next_number = app.save_inquiry(self.payload())[0]
        self.assertEqual(int(next_number[-4:]), int(number[-4:]) + 1)

    def test_stale_status_blocks_deletion(self):
        order = self.order(); number = order['number']
        app.update_order_status(number, {'status':'已发货', 'expected_status':order['status']})
        self.assertEqual(self.deletion('orders', number)[0], 409)
        self.assertEqual(app.find_order(number)['status'], '已发货')
        self.assertEqual(self.deletion('orders', number, status='已发货')[0], 200)

    def test_inquiry_deletion_does_not_delete_linked_order(self):
        payload = self.payload(); number = app.save_inquiry(payload)[0]
        order = app.convert_inquiry(number, {})
        self.assertEqual(self.deletion('inquiries', number)[0], 200)
        self.assertEqual(self.req('/api/sales/inquiries')[1]['inquiries'], [])
        self.assertIsNotNone(app.find_order(order['number']))
        self.assertEqual(self.req(f'/api/sales/inquiries/{number}/convert', {})[0], 404)
        self.assertEqual(self.req(f'/api/sales/inquiries/{number}/status',
                                 {'status':'已确认订单', 'expected_status':order['status']})[0], 404)
        self.assertEqual(self.req('/api/guest/inquiries', payload, user=None)[0], 409)
        with closing(app.connect_db()) as db:
            self.assertEqual(db.execute('SELECT order_number FROM inquiries WHERE number=?', (number,)).fetchone()[0], order['number'])

    def test_deleted_order_cannot_be_recreated_from_inquiry(self):
        number = app.save_inquiry(self.payload())[0]
        order = app.convert_inquiry(number, {})
        self.deletion('orders', order['number'])
        inquiry = self.req('/api/sales/inquiries')[1]['inquiries'][0]
        self.assertEqual(inquiry['number'], number)
        self.assertTrue(inquiry['order_deleted'])
        self.assertEqual(self.req(f'/api/sales/inquiries/{number}/convert', {})[0], 409)
        self.assertEqual(self.req(f'/api/sales/inquiries/{number}/status',
                                 {'status':'已发货', 'expected_status':order['status']})[0], 409)
        self.assertEqual(self.deletion('inquiries', number)[0], 200)

    def test_unsent_email_stops_and_sent_email_is_retained(self):
        order = self.order(); number = order['number']
        with closing(app.connect_db()) as db, db:
            db.execute("INSERT INTO order_email_outbox (order_number,status,next_attempt_at) VALUES (?,'pending',0)", (number,))
        self.assertEqual(self.deletion('orders', number)[0], 200)
        with closing(app.connect_db()) as db, db:
            self.assertEqual(db.execute('SELECT retryable FROM order_email_outbox WHERE order_number=?', (number,)).fetchone()[0], 0)
            # A stale queue update must not make a deleted order eligible for delivery again.
            db.execute('UPDATE order_email_outbox SET retryable=1 WHERE order_number=?', (number,))
        with patch.object(app, 'mail_enabled', return_value=True), patch.object(app, 'send_order_email') as mail:
            self.assertIsNone(app.claim_order_email())
            self.assertFalse(app.deliver_next_order_email())
            mail.assert_not_called()
        sent = self.order()
        with closing(app.connect_db()) as db, db:
            db.execute("INSERT INTO order_email_outbox (order_number,status,next_attempt_at) VALUES (?,'sent',0)", (sent['number'],))
        self.assertEqual(self.deletion('orders', sent['number'])[0], 200)
        with closing(app.connect_db()) as db:
            self.assertEqual(db.execute('SELECT status FROM order_email_outbox WHERE order_number=?', (sent['number'],)).fetchone()[0], 'sent')

    def test_inflight_email_and_transaction_failure_keep_record(self):
        order = self.order(); number = order['number']
        with closing(app.connect_db()) as db, db:
            db.execute("INSERT INTO order_email_outbox (order_number,status,next_attempt_at,lease_until) VALUES (?,'pending',0,?)", (number, int(time.time()) + 60))
        self.assertEqual(self.deletion('orders', number)[0], 409)
        self.assertIsNotNone(app.find_order(number))
        with closing(app.connect_db()) as db, db:
            db.execute('UPDATE order_email_outbox SET lease_until=0 WHERE order_number=?', (number,))
            db.execute("""CREATE TRIGGER reject_deletion BEFORE INSERT ON record_deletions
                BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END""")
        self.assertEqual(self.deletion('orders', number)[0], 503)
        self.assertIsNotNone(app.find_order(number))
        with closing(app.connect_db()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM record_deletions').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT retryable FROM order_email_outbox WHERE order_number=?', (number,)).fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
