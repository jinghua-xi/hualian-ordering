"""Public search for procurement references and explicit keyword requests."""
from html.parser import HTMLParser
import ipaddress
import json
import re
from threading import Lock
import time
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from xml.etree import ElementTree

TERMS = ('POM','尼龙','PA','塑料','树脂','金属','织带','背包','肩带','童装','衣服','户外','耐洗','水洗','防水','紫外线','耐寒','开合','调节','承重','材质','尺寸','安装')
SEARCH_TERMS = {
    '插扣': 'side release buckle', '调节扣': 'webbing adjustment buckle', 'D环扣': 'D ring webbing',
    '钩扣': 'webbing snap hook', '四合扣': 'snap fastener', '对扣': 'buckle',
    '打扣工具': 'plastic snap fastener hand pliers press dies', '扣件': 'buckle',
}
BING_SEARCH_URL = 'https://www.bing.com/search'
BING_FEED_ENDPOINTS = (BING_SEARCH_URL, 'https://cn.bing.com/search')
SEARCH_360_URL = 'https://www.so.com/s'
BAIKE_HOST = 'baike.baidu.com'
PRIVATE_QUERY = re.compile(
    r'https?://|www\.|\b\S+@\S+\.\S+|\d{7,}|'
    r'(?:订单号|订单编号|客户名称|客户名|收货地址|联系方式|身份证|银行卡|手机号|电话号码|密码|验证码|API\s*key)\s*[:：=为是]?'
    r'\s*[^\s，。；、,;]', re.I,
)
_cache = {}
_lock = Lock()

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None

class TextOnly(HTMLParser):
    def __init__(self): super().__init__(); self.parts=[]
    def handle_data(self,data): self.parts.append(data)


class SearchResults(HTMLParser):
    """Extract links, titles and snippets from 360's ordinary web results."""

    def __init__(self):
        super().__init__()
        self.items = []
        self.field = None
        self.title = []
        self.snippet = []
        self.link = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "h3" and "res-title" in classes:
            self.title, self.snippet, self.link = [], [], ""
            self.field = self.title
        elif tag == "a" and self.field is self.title:
            self.link = attrs.get("data-mdurl") or attrs.get("href", "")
        elif tag == "p" and "res-desc" in classes and self.link:
            self.field = self.snippet

    def handle_data(self, data):
        if self.field is not None:
            self.field.append(data)

    def handle_endtag(self, tag):
        if tag == "h3" and self.field is self.title:
            self.field = None
        elif tag == "p" and self.field is self.snippet:
            title = re.sub(r"\s+", " ", "".join(self.title)).strip()
            snippet = re.sub(r"\s+", " ", "".join(self.snippet)).strip()
            if title and snippet:
                self.items.append({"title": title[:120], "url": self.link, "snippet": snippet[:600]})
            self.field = None
            self.link = ""


def plain(value):
    parser=TextOnly(); parser.feed(value or '')
    return re.sub(r'\s+',' ',' '.join(parser.parts)).strip()


def safe_query(message, category=''):
    # Never forward raw messages, conversation history, names, account IDs or quantities.
    chosen=[term for term in TERMS if term in message][:6]
    family=category if category in SEARCH_TERMS else next((item for item in SEARCH_TERMS if item in message),'扣件')
    materials=[english for word,english in (('POM','POM acetal'),('尼龙','nylon'),('PA','polyamide'),('塑料','plastic'),('金属','metal')) if word in chosen]
    kind = SEARCH_TERMS[family]
    if family == '打扣工具':
        size = ' T5 size 20' if re.search(r'(?<![A-Za-z0-9])T5(?![A-Za-z0-9])', message, re.I) else ''
        operation = 'removal' if re.search(r'拆(?:卸|除|扣)|起扣', message) else 'installation'
        return f'{kind}{size} {operation}'
    return ' '.join(materials+[kind,'material properties' if materials else 'backpack webbing selection'])


def public_url(url):
    try:
        parsed=urlsplit(url)
        if parsed.scheme not in ('http','https') or parsed.username or parsed.password or not parsed.hostname or parsed.port not in (None,80,443): return False
        host=parsed.hostname.lower()
        if '.' not in host or host.endswith(('.local','.internal','.localhost')): return False
        try: return ipaddress.ip_address(host).is_global
        except ValueError: return bool(re.fullmatch(r'[a-z0-9.-]+',host))
    except ValueError: return False


def public_keyword(topic):
    topic = ' '.join(topic.split())
    if 1 <= len(topic) <= 160 and not PRIVATE_QUERY.search(topic):
        return topic
    return ''


def keyword_search_url(query, *, baike=False):
    return BING_SEARCH_URL + '?' + urlencode({'q': _keyword_query(query, baike)})


def _keyword_query(query, baike):
    return f'{query} site:{BAIKE_HOST}' if baike else query


def public_keyword_search(query, *, baike=False):
    if not public_keyword(query):
        return []
    search_query = _keyword_query(query, baike)
    site = re.search(r'(?:^|\s)site:([a-z0-9-]+(?:\.[a-z0-9-]+)+)(?=\s|$)', search_query, re.I)
    domain = BAIKE_HOST if baike else site[1].lower() if site else ''
    if re.search(r'[\u3400-\u9fff]', query):
        results = _search_360(search_query, domain) or _search_feed(search_query, domain=domain)
    else:
        results = _search_feed(search_query, domain=domain) or _search_360(search_query, domain)
    return results if results or domain else _encyclopedia_search(query)


def _search_360(query, domain):
    key = ('360', query, domain)
    now = time.monotonic()
    with _lock:
        previous = _cache.get(key)
        if previous and now - previous[0] < 300:
            return previous[1]
    request = Request(SEARCH_360_URL + '?' + urlencode({'q': query}),
                      headers={'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.7'})
    results = []
    try:
        with build_opener(NoRedirect()).open(request, timeout=5) as response:
            raw = response.read(1_000_000)
        parser = SearchResults()
        parser.feed(raw.decode('utf-8', errors='replace'))
        seen = set()
        for item in parser.items:
            link = item['url']
            if link in seen or not public_url(link):
                continue
            host = urlsplit(link).hostname.lower()
            if host == 'so.com' or host.endswith('.so.com') or (domain and host != domain and not host.endswith('.' + domain)):
                continue
            if not _topic_matches(query.split(' site:', 1)[0], item['title'] + ' ' + item['snippet']):
                continue
            results.append({**item, 'snippet': _relevant_excerpt(query, item['snippet'])})
            seen.add(link)
            if len(results) == 3:
                break
    except (OSError, ValueError):
        pass
    with _lock:
        if len(_cache) >= 128:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (now, results)
    return results


def _topic_matches(query, result):
    normalized = result.casefold()
    generic_words = {'about', 'and', 'current', 'latest', 'news', 'official', 'org', 'release', 'source',
                     'stable', 'the', 'today', 'version', 'what', 'when', 'where', 'with', 'www'}
    words = set(re.findall(r'[a-z\d]{3,}', query.casefold())) - generic_words
    if words and not any(re.search(rf'\b{re.escape(word)}\b', normalized) for word in words):
        return False
    chinese = set(re.findall(r'(?=([\u3400-\u9fff]{2}))', query))
    terms = words | chinese
    return not terms or sum(term in normalized for term in terms) >= min(2, len(terms))


def _relevant_excerpt(query, snippet):
    # A search result can splice previews of several unrelated posts together.
    terms = set(re.findall(r'(?=([\u3400-\u9fff]{3}))', query.split(' site:', 1)[0]))
    if not terms:
        return snippet
    sentences = re.split(r'(?<=[。！？])|(?<=[.!?])(?=\s|[\u3400-\u9fff])', snippet)
    relevant = [part.strip() for part in sentences if any(term in part for term in terms)]
    return ' '.join(relevant) or snippet


def _encyclopedia_search(query):
    language = 'zh' if re.search(r'[\u3400-\u9fff]', query) else 'en'
    base = f'https://{language}.wikipedia.org'
    request = Request(base + '/w/rest.php/v1/search/page?' + urlencode({'q': query, 'limit': 3}),
                      headers={'User-Agent': 'HualianOrdering/1.0 public reference lookup'})
    try:
        with build_opener(NoRedirect()).open(request, timeout=5) as response:
            raw = response.read(128001)
        pages = json.loads(raw)['pages'] if len(raw) <= 128000 else []
        results = []
        for page in pages[:3]:
            title = plain(page['title'])[:120]
            snippet = plain(page.get('description') or page.get('excerpt'))[:600]
            if title and snippet and _topic_matches(query, title + ' ' + snippet):
                results.append({'title': title + ' · 维基百科', 'url': base + '/wiki/' + quote(page['key']), 'snippet': snippet})
        return results
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []


def public_search(message, category=''):
    return _search_feed(safe_query(message, category), procurement=True)


def _bing_feed(query):
    params = urlencode({'format': 'rss', 'q': query, 'mkt': 'zh-CN'})
    opener = build_opener(NoRedirect())
    for endpoint in BING_FEED_ENDPOINTS:
        request = Request(endpoint + '?' + params, headers={'User-Agent': 'HualianOrdering/1.0'})
        try:
            with opener.open(request, timeout=4) as response:
                raw = response.read(128001)
            if len(raw) <= 128000 and b'<!DOCTYPE' not in raw.upper() and b'<!ENTITY' not in raw.upper():
                root = ElementTree.fromstring(raw)
                if root.tag == 'rss':
                    return root
        except (OSError, ElementTree.ParseError):
            continue
    return ElementTree.Element('rss')


def _search_feed(query, *, procurement=False, domain=''):
    key = (query, procurement, domain)
    now=time.monotonic()
    with _lock:
        previous=_cache.get(key)
        if previous and now-previous[0]<300: return previous[1]
        # At most 128 recent queries, including a short negative cache on failure.
        if len(_cache)>=128: _cache.pop(next(iter(_cache)))
        _cache[key]=(now,[])
    try:
        root = _bing_feed(query)
        results=[]
        seen=set()
        for item in root.findall('./channel/item'):
            link=(item.findtext('link') or '').strip()
            if not public_url(link): continue
            host=urlsplit(link).hostname.lower()
            if link in seen or (domain and host != domain and not host.endswith('.' + domain)): continue
            if procurement and any(host==store or host.endswith('.'+store) for store in ('taobao.com','tmall.com','1688.com','yiwugo.com','alibaba.com','jd.com','amazon.com')): continue
            title=plain(item.findtext('title'))[:120]; snippet=plain(item.findtext('description'))[:600]
            if not title or not snippet: continue
            if procurement and not re.search(r'\b(?:POM|acetal|nylon|polyamide|polyoxymethylene|buckle|fastener|webbing|triglide|D[- ]?ring|snap|pliers)\b|聚甲醛|尼龙|扣件|织带|四合扣|打扣',title+' '+snippet,re.I): continue
            if not procurement and not _topic_matches(query.split(' site:', 1)[0], title + ' ' + snippet): continue
            results.append({'title':title,'url':link,'snippet':_relevant_excerpt(query, snippet)})
            seen.add(link)
            if len(results)==3: break
    except Exception:
        results=[]
    with _lock: _cache[key]=(now,results)
    return results
