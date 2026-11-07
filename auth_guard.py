"""Persistent login throttling and single-use, browser-bound image challenges."""
import hashlib
import hmac
import secrets
import time

WINDOW = 900
CAPTCHA_AFTER = 5

class LoginGuardError(Exception):
    def __init__(self, message, status=401, retry_after=0):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


def init_auth_tables(db):
    db.execute('CREATE TABLE IF NOT EXISTS login_limits (key TEXT PRIMARY KEY, failures INTEGER NOT NULL, window_start INTEGER NOT NULL, blocked_until INTEGER NOT NULL DEFAULT 0)')
    db.execute('CREATE TABLE IF NOT EXISTS login_captchas (token TEXT PRIMARY KEY, browser TEXT NOT NULL, answer_hash TEXT NOT NULL, image TEXT NOT NULL, expires INTEGER NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS login_limits_expiry ON login_limits(window_start)')
    db.execute('DELETE FROM login_limits WHERE window_start < ?', (int(time.time())-86400,))
    db.execute('DELETE FROM login_captchas WHERE expires < ?', (int(time.time()),))


def account_key(username):
    return 'account:' + hashlib.sha256(username.encode()).hexdigest()


def needs_captcha(db, username):
    row = db.execute('SELECT failures,window_start FROM login_limits WHERE key=?', (account_key(username),)).fetchone()
    return bool(row and row['window_start'] + WINDOW > time.time() and row['failures'] >= CAPTCHA_AFTER)


def reserve_attempt(db, username, peer, browser='', token='', answer=''):
    """Called under BEGIN IMMEDIATE before password hashing; fail closed on parallel attempts."""
    now = int(time.time())
    db.execute('DELETE FROM login_limits WHERE window_start < ?', (now-86400,))
    peer_key = 'peer:' + hashlib.sha256(peer.encode()).hexdigest()
    row = db.execute('SELECT * FROM login_limits WHERE key=?', (peer_key,)).fetchone()
    start = row['window_start'] if row and now-row['window_start'] < 60 else now
    count = row['failures'] if row and now-row['window_start'] < 60 else 0
    if count >= 120:
        return LoginGuardError('登录请求过于频繁，请一分钟后重试。', 429, max(1,start+60-now))
    db.execute('INSERT OR REPLACE INTO login_limits VALUES (?,?,?,0)', (peer_key,count+1,start))
    key = account_key(username)
    row = db.execute('SELECT * FROM login_limits WHERE key=?', (key,)).fetchone()
    failures = row['failures'] if row and now-row['window_start'] < WINDOW else 0
    if row and failures and row['blocked_until'] > now:
        return LoginGuardError('尝试次数过多，请稍后再试。',429,row['blocked_until']-now)
    if failures >= CAPTCHA_AFTER:
        challenge = db.execute('SELECT * FROM login_captchas WHERE token=?',(token,)).fetchone()
        if challenge:
            db.execute('DELETE FROM login_captchas WHERE token=?',(token,))
        expected = hashlib.sha256((token+':'+answer.strip()).encode()).hexdigest()
        if not challenge or challenge['expires'] <= now or not browser or not hmac.compare_digest(challenge['browser'],browser) or not hmac.compare_digest(challenge['answer_hash'],expected):
            return LoginGuardError('连续失败五次，请填写新的图片验证码后登录。')
    start = row['window_start'] if failures else now
    failures += 1
    blocked = now + 60 if failures >= 10 else 0
    db.execute('INSERT OR REPLACE INTO login_limits VALUES (?,?,?,?)',(key,failures,start,blocked))
    return None


def login_success(db, username, peer=None):
    db.execute('DELETE FROM login_limits WHERE key=?',(account_key(username),))
    if peer:
        key='peer:'+hashlib.sha256(peer.encode()).hexdigest()
        db.execute('UPDATE login_limits SET failures=MAX(0,failures-1) WHERE key=?',(key,))


DIGITS = {
 '0':['01110','10001','10011','10101','11001','10001','01110'],
 '1':['00100','01100','00100','00100','00100','00100','01110'],
 '2':['01110','10001','00001','00010','00100','01000','11111'],
 '3':['11110','00001','00001','01110','00001','00001','11110'],
 '4':['00010','00110','01010','10010','11111','00010','00010'],
 '5':['11111','10000','10000','11110','00001','00001','11110'],
 '6':['01110','10000','10000','11110','10001','10001','01110'],
 '7':['11111','00001','00010','00100','01000','01000','01000'],
 '8':['01110','10001','10001','01110','10001','10001','01110'],
 '9':['01110','10001','10001','01111','00001','00001','01110']}


def create_captcha(db, browser):
    now = int(time.time())
    db.execute('DELETE FROM login_captchas WHERE expires<=?',(now,))
    db.execute('DELETE FROM login_captchas WHERE browser=?',(browser,))
    # Bounded storage even if a bot continually requests replacement images.
    db.execute('DELETE FROM login_captchas WHERE token IN (SELECT token FROM login_captchas ORDER BY expires DESC LIMIT -1 OFFSET 1000)')
    answer=''.join(secrets.choice('0123456789') for _ in range(5))
    token=secrets.token_hex(24)
    shapes=['<svg xmlns="http://www.w3.org/2000/svg" width="190" height="58" viewBox="0 0 190 58"><rect width="190" height="58" rx="8" fill="#f4f5f8"/>']
    for i,char in enumerate(answer):
        x=12+i*34; y=12+secrets.randbelow(5)
        path=[]
        for row,bits in enumerate(DIGITS[char]):
            for col,bit in enumerate(bits):
                if bit=='1': path.append(f'M{x+col*4} {y+row*4}h4v4h-4z')
        shapes.append('<path fill="#65182c" d="'+''.join(path)+'"/>')
    for _ in range(12):
        shapes.append(f'<path d="M{secrets.randbelow(190)} {secrets.randbelow(58)}l12 4" stroke="#b6bcc8" stroke-width="1"/>')
    shapes.append('</svg>')
    db.execute('INSERT INTO login_captchas VALUES (?,?,?,?,?)',(token,browser,hashlib.sha256((token+':'+answer).encode()).hexdigest(),''.join(shapes),now+300))
    return token
