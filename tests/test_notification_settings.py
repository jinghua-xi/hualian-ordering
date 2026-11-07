"""Notification permissions, persistence and queue behavior; SMTP is always mocked."""
import base64
from contextlib import closing
from datetime import timedelta
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class NotificationSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        os.environ.update(ORDERS_DB=str(Path(cls.folder.name) / 'orders.sqlite3'), DEMO_MODE='0',
                          PREVIEW_USER='preview', PREVIEW_PASSWORD='preview', SALES_USER='admin', SALES_PASSWORD='admin',
                          ORDER_RECIPIENT='original@example.com', SMTP_HOST='smtp.example.com', SMTP_USERNAME='sender@example.com',
                          SMTP_PASSWORD='not-a-real-password', SMTP_SECURITY='ssl', SMTP_PORT='465')
        import server
        cls.app = server
        server.init_db()
        server.manage_account(dict(action='create', username='sales_test', role='sales', password='password123'))
        cls.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.OrderingHandler)
        cls.worker = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.worker.start()
        cls.base = 'http://127.0.0.1:' + str(cls.http.server_port)
        cls.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        login = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            login.open(urllib.request.Request(cls.base + '/', data=urllib.parse.urlencode(
                dict(username='sales_test', password='password123')).encode(), headers={'Content-Type': 'application/x-www-form-urlencoded'}))
        except urllib.error.HTTPError as response:
            if response.code != 303:
                raise
            cls.sales_cookie = response.headers.get_all('Set-Cookie')[0].split(';')[0]

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.folder.cleanup()

    def setUp(self):
        with closing(self.app.connect_db()) as db, db:
            db.execute('DELETE FROM order_email_outbox')
            db.execute('DELETE FROM order_items')
            db.execute('DELETE FROM orders')
            db.execute("UPDATE order_notification_settings SET enabled=1,recipient='original@example.com',version=1 WHERE id=1")

    def req(self, body=None, user='admin:admin', path='/api/sales/email-settings', **extra_headers):
        headers = dict(extra_headers)
        if user == 'sales_test:password123':
            headers['Cookie'] = self.sales_cookie
        elif user:
            headers['Authorization'] = 'Basic ' + base64.b64encode(user.encode()).decode()
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(self.base + path, data=data, headers=headers)
        try:
            response = self.opener.open(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        return response.code, json.loads(response.read())

    def test_permissions_and_no_credentials_exposed(self):
        body = dict(enabled=False, recipient='changed@example.com', version=1)
        for user in (None, 'preview:preview'):
            self.assertEqual(self.req(user=user)[0], 401)
            self.assertEqual(self.req(body, user=user)[0], 401)
        status, settings = self.req(user='sales_test:password123')
        self.assertEqual(status, 200)
        self.assertTrue(settings['configured'])
        self.assertEqual(self.req(body, **{'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(self.req(body, user='sales_test:password123')[0], 200)
        self.assertNotIn('password', json.dumps(settings).lower())
        self.assertNotIn('smtp.example.com', json.dumps(settings))
        for path in ('/api/guest/sales/email-settings', '/api/guest/admin/accounts', '/api/guest/sales/orders'):
            self.assertEqual(self.req(user='preview:preview', path=path)[0], 404)

    def test_persistent_settings_and_optimistic_conflict(self):
        body = dict(enabled=False, recipient='new@example.com', version=1)
        status, updated = self.req(body)
        self.assertEqual(status, 200)
        self.assertEqual(updated['version'], 2)
        self.assertEqual(self.req(body)[0], 409)
        with patch.dict(os.environ, ORDER_RECIPIENT='different@example.com'):
            self.app.init_db()
        persisted = self.req()[1]
        self.assertFalse(persisted['enabled'])
        self.assertEqual(persisted['recipient'], 'new@example.com')
        self.assertEqual(persisted['updated_by'], 'admin')
        self.assertEqual(persisted['version'], 2)

    def test_bad_types_and_email_injection_leave_settings_intact(self):
        valid = dict(enabled=True, recipient='new@example.com', version=1)
        bad = [dict(valid, enabled=value) for value in (0, 1, 'false', None, [], {})]
        bad += [dict(valid, version=value) for value in (True, 0, -1, '1', 1.5, None)]
        bad += [dict(valid, recipient=value) for value in (None, [], 12, '', 'user@gmail', 'a@b.com,c@d.com',
                'Name <a@b.com>', 'a@b.com\r\nBcc: stolen@example.com', '.bad@example.com',
                'bad..name@example.com', 'a@-bad.com', 'x' * 255 + '@example.com')]
        for body in bad:
            with self.subTest(body=body):
                self.assertEqual(self.req(body)[0], 400)
        self.assertEqual(self.req()[1]['version'], 1)
        self.assertEqual(self.req()[1]['recipient'], 'original@example.com')

    def test_pause_resume_uses_saved_recipient_and_preserves_excel(self):
        app = self.app
        self.assertEqual(self.req(dict(enabled=False, recipient='original@example.com', version=1))[0], 200)
        payload = dict(customer='邮件通知隔离测试', request_id='notification-test-00001', items=[
            dict(sku='T5', quantity=66, delivery_date=(app.china_now().date() + timedelta(days=10)).isoformat(), note='保留备注')])
        order, created = app.save_order(payload, 'preview')
        self.assertTrue(created)
        self.assertEqual(order['email_status'], 'paused')
        self.assertEqual(app.find_order(order['number'])['email_status'], 'paused')
        with patch('server.send_order_email') as send:
            self.assertFalse(app.deliver_next_order_email())
            send.assert_not_called()
        with closing(app.connect_db()) as db:
            self.assertEqual(db.execute('SELECT attempts FROM order_email_outbox').fetchone()[0], 0)
        self.assertEqual(self.req(dict(enabled=True, recipient='new@example.com', version=2))[0], 200)
        with patch('order_delivery.smtplib.SMTP_SSL') as smtp:
            smtp.return_value.send_message.return_value = {}
            self.assertTrue(app.deliver_next_order_email())
            call = smtp.return_value.send_message.call_args
            message = call.args[0]
            self.assertEqual(str(message['To']), 'new@example.com')
            self.assertEqual(call.kwargs['to_addrs'], ['new@example.com'])
            attachments = list(message.iter_attachments())
            self.assertEqual(len(attachments), 1)
            self.assertTrue(attachments[0].get_filename().endswith('.xlsx'))
            import io
            from zipfile import ZipFile
            with ZipFile(io.BytesIO(attachments[0].get_payload(decode=True))) as archive:
                sheet = archive.read('xl/worksheets/sheet1.xml').decode()
                self.assertIn('保留备注', sheet)
                self.assertIn('66', sheet)
            self.assertFalse(app.deliver_next_order_email())
            self.assertEqual(smtp.return_value.send_message.call_count, 1)
        self.assertEqual(app.find_order(order['number'])['email_status'], 'sent')
        self.req(dict(enabled=False, recipient='new@example.com', version=3))
        self.req(dict(enabled=True, recipient='new@example.com', version=4))
        with patch('server.send_order_email') as send:
            self.assertFalse(app.deliver_next_order_email())
            send.assert_not_called()
        self.assertEqual(app.find_order(order['number'])['items'][0]['quantity'], 66)

    def test_recipient_snapshot_when_job_already_started(self):
        app = self.app
        order, _ = app.save_order(dict(customer='测试', request_id='notification-test-00002', items=[
            dict(sku='T5', quantity=1, delivery_date=(app.china_now().date() + timedelta(days=10)).isoformat())]), 'preview')
        job = app.claim_order_email()
        self.assertEqual(job[0], order['number'])
        self.req(dict(enabled=False, recipient='new@example.com', version=1))
        self.assertEqual(job[3], 'original@example.com')
        self.assertIsNone(app.claim_order_email())


if __name__ == '__main__':
    unittest.main()
