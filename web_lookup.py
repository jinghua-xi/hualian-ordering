"""Bounded public search: only allowlisted procurement terms leave the server."""
from html.parser import HTMLParser
import ipaddress
import re
from threading import Lock
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from xml.etree import ElementTree

TERMS = ('POM','尼龙','PA','塑料','树脂','金属','织带','背包','肩带','童装','衣服','户外','耐洗','水洗','防水','紫外线','耐寒','开合','调节','承重','材质','尺寸','安装')
CATEGORIES = ('插扣','调节扣','D环扣','钩扣','四合扣','对扣')
_cache = {}
_lock = Lock()

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None

class TextOnly(HTMLParser):
    def __init__(self): super().__init__(); self.parts=[]
    def handle_data(self,data): self.parts.append(data)


def plain(value):
    parser=TextOnly(); parser.feed(value or '')
    return re.sub(r'\s+',' ',' '.join(parser.parts)).strip()


def safe_query(message, category=''):
    # Never forward raw messages, conversation history, names, account IDs or quantities.
    chosen=[term for term in TERMS if term in message][:6]
    family=category if category in CATEGORIES else next((item for item in CATEGORIES if item in message),'扣件')
    materials=[english for word,english in (('POM','POM acetal'),('尼龙','nylon'),('PA','polyamide'),('塑料','plastic'),('金属','metal')) if word in chosen]
    kind={'插扣':'side release buckle','调节扣':'webbing adjustment buckle','D环扣':'D ring webbing','钩扣':'webbing snap hook','四合扣':'snap fastener','对扣':'buckle','扣件':'buckle'}[family]
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


def public_search(message, category=''):
    query=safe_query(message,category)
    now=time.monotonic()
    with _lock:
        previous=_cache.get(query)
        if previous and now-previous[0]<300: return previous[1]
        # At most 128 recent queries, including a short negative cache on failure.
        if len(_cache)>=128: _cache.pop(next(iter(_cache)))
        _cache[query]=(now,[])
    request=Request('https://cn.bing.com/search?'+urlencode({'format':'rss','q':query}),headers={'User-Agent':'HualianOrdering/1.0'})
    try:
        with build_opener(ProxyHandler({}),NoRedirect()).open(request,timeout=8) as response:
            raw=response.read(128001)
        if len(raw)>128000 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper(): return []
        root=ElementTree.fromstring(raw)
        results=[]
        for item in root.findall('./channel/item'):
            link=(item.findtext('link') or '').strip()
            if not public_url(link): continue
            host=urlsplit(link).hostname.lower()
            if any(host==domain or host.endswith('.'+domain) for domain in ('taobao.com','tmall.com','1688.com','yiwugo.com','alibaba.com','jd.com','amazon.com')): continue
            title=plain(item.findtext('title'))[:120]; snippet=plain(item.findtext('description'))[:600]
            if not title or not snippet: continue
            if not re.search(r'\b(?:POM|acetal|nylon|polyamide|polyoxymethylene|buckle|fastener|webbing|triglide|D[- ]?ring)\b|聚甲醛|尼龙|扣件|织带',title+' '+snippet,re.I): continue
            results.append({'title':title,'url':link,'snippet':snippet})
            if len(results)==3: break
    except Exception:
        results=[]
    with _lock: _cache[query]=(now,results)
    return results
