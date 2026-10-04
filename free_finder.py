"""Public website discovery. No paid data API or LLM calls."""
import ipaddress
import base64
import re
import socket
import time
from html.parser import HTMLParser
from urllib.parse import urlparse, urljoin, urlencode, parse_qs, unquote
from urllib.request import getproxies
from urllib.robotparser import RobotFileParser
import requests

UA = 'BedSetCoResearch/1.0'
MAX_BYTES = 4_000_000
EMAIL = re.compile(r'[A-Z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,}', re.I)
BLOCKED = ('bing.com','google.com','duckduckgo.com','facebook.com','linkedin.com',
           'instagram.com','youtube.com','amazon.com','pinterest.com','wikipedia.org',
           'manta.com','datanyze.com','tradewheel.com','alibaba.com','made-in-china.com',
           'indiamart.com','tradeindia.com')

def local_proxies():
    """Use the user's existing local proxy without reading .netrc credentials."""
    selected={}
    for scheme,value in getproxies().items():
        if scheme not in ('http','https'):continue
        try:
            parsed=urlparse(value)
            if (parsed.scheme=='http' and parsed.hostname in ('127.0.0.1','localhost','::1')
                    and parsed.port and not parsed.username and not parsed.password):
                selected[scheme]=value
        except ValueError:continue
    return selected

def public_url(url):
    p=urlparse(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:
        raise ValueError('仅支持公开网站地址')
    if p.port not in (None,80,443): raise ValueError('不支持此网站端口')
    for item in socket.getaddrinfo(p.hostname,p.port or 443,type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(item[4][0]).is_global:
            raise ValueError('不能访问本机或内网地址')
    return url

class Web:
    def __init__(self):
        self.session=requests.Session()
        self.session.trust_env=False
        self.session.proxies.update(local_proxies())
        self.robots={}
        self.last={}

    def fetch(self,url,robots=True):
        for _ in range(5):
            public_url(url)
            p=urlparse(url)
            origin=f'{p.scheme}://{p.netloc}'
            if robots:
                if origin not in self.robots:
                    rule=RobotFileParser()
                    try:
                        status,_,body=self.fetch(origin+'/robots.txt',False)
                        if status in (401,403): rule.parse(['User-agent: *','Disallow: /'])
                        elif status==404: rule.parse([])
                        elif status==200: rule.parse(body.splitlines())
                        else: rule.parse(['User-agent: *','Disallow: /'])
                    except Exception: rule.parse(['User-agent: *','Disallow: /'])
                    self.robots[origin]=rule
                if not self.robots[origin].can_fetch(UA,url):
                    return 403,url,''
            time.sleep(max(0,1.2-(time.monotonic()-self.last.get(origin,0))))
            self.last[origin]=time.monotonic()
            with self.session.get(url,headers={'User-Agent':UA},timeout=(8,15),
                                  stream=True,allow_redirects=False) as r:
                if r.status_code in (301,302,303,307,308):
                    url=urljoin(url,r.headers.get('Location',''));continue
                if 'text/' not in r.headers.get('Content-Type','') and 'xml' not in r.headers.get('Content-Type',''):
                    return r.status_code,url,''
                body=bytearray()
                for chunk in r.iter_content(16384):
                    body.extend(chunk)
                    if len(body)>MAX_BYTES: raise ValueError('网页过大')
                return r.status_code,url,body.decode(r.encoding or 'utf-8',errors='replace')
        raise ValueError('重定向过多')

class Page(HTMLParser):
    def __init__(self,raw):
        super().__init__(convert_charrefs=True)
        self.links=[];self.words=[];self.title=[];self.in_title=False;self.title_complete=False;self.hidden=0
        self.feed(raw)
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'): self.hidden+=1
        if tag=='title' and not self.title_complete:self.in_title=True
        if tag=='a':
            href=dict(attrs).get('href','')
            if href:self.links.append(href)
    def handle_endtag(self,tag):
        if tag in ('script','style'): self.hidden=max(0,self.hidden-1)
        if tag=='title' and self.in_title:
            self.in_title=False;self.title_complete=True
    def handle_data(self,data):
        if not self.hidden:self.words.append(data)
        if self.in_title:self.title.append(data)

def host(url):
    return (urlparse(url).hostname or '').lower().removeprefix('www.')

class SearchPage(HTMLParser):
    def __init__(self,raw,engine):
        super().__init__(convert_charrefs=True)
        self.engine=engine;self.heading=0;self.active=None;self.results=[]
        self.feed(raw)
    def handle_starttag(self,tag,attrs):
        values=dict(attrs)
        if tag=='h2':self.heading+=1
        if tag=='a' and ((self.engine=='duckduckgo' and 'result__a' in values.get('class','').split())
                        or (self.engine=='bing' and self.heading)):
            self.active=[values.get('href',''),[]]
    def handle_data(self,data):
        if self.active is not None:self.active[1].append(data)
    def handle_endtag(self,tag):
        if tag=='a' and self.active is not None:
            self.results.append((self.active[0],' '.join(self.active[1])))
            self.active=None
        if tag=='h2':self.heading=max(0,self.heading-1)

def search_destination(link,engine):
    parsed=urlparse(link)
    values=parse_qs(parsed.query)
    if engine=='duckduckgo' and values.get('uddg'):
        link=values['uddg'][0]
    elif engine=='bing' and parsed.path.startswith('/ck/a'):
        encoded=values.get('u',[''])[0]
        if not encoded.startswith('a1'):return ''
        try:
            material=encoded[2:]
            link=base64.urlsafe_b64decode(material+'='*(-len(material)%4)).decode('utf-8')
        except (ValueError,UnicodeError):return ''
    return link if urlparse(link).scheme in ('http','https') else ''

def discover(web,queries):
    urls=[]
    for query in queries[:4]:
        for engine,base in (('duckduckgo','https://html.duckduckgo.com/html/?'),
                            ('bing','https://www.bing.com/search?')):
            try:
                status,_,raw=web.fetch(base+urlencode({'q':query}),False)
                if status!=200:continue
                found=[search_destination(link,engine) for link,_ in SearchPage(raw,engine).results]
                found=[link for link in found if link and host(link)
                       and not any(host(link)==d or host(link).endswith('.'+d) for d in BLOCKED)]
                if found:
                    urls.extend(found);break
            except Exception:pass
    return list(dict.fromkeys(urls))

def find(count,config,progress=lambda _:None,exclude_domains=()):
    web=Web()
    queries=[q.strip() for q in config.get('queries','').splitlines() if q.strip()]
    seeds=[q.strip() for q in config.get('websites','').splitlines() if q.strip()]
    progress('正在搜索公开公司网站……')
    urls=seeds[:30]+discover(web,queries)
    domains=set();records=[];deadline=time.monotonic()+600
    excluded=set(exclude_domains)
    for url in urls[:30]:
        if len(records)>=count or time.monotonic()>deadline:break
        domain=host(url)
        if not domain or domain in domains or any(domain==d or domain.endswith('.'+d) for d in BLOCKED):continue
        if any(domain==d or domain.endswith('.'+d) or d.endswith('.'+domain) for d in excluded):continue
        domains.add(domain)
        progress(f'正在查看公司官网 {domain}（已找到 {len(records)} 家有公开邮箱的公司）')
        try:
            p=urlparse(url);root=f'{p.scheme}://{p.netloc}/'
            status,final,raw=web.fetch(root)
            if status!=200 or host(final)!=domain:continue
            page=Page(raw)
            title=' '.join(page.title).strip()[:160] or domain
            text=' '.join(page.words)
            # Searches are user-defined; candidate fit remains a human review decision.
            pages=[(final,page,text)]
            links=[]
            for link in page.links:
                absolute=urljoin(final,link)
                if host(absolute)==domain and any(k in absolute.lower() for k in ('contact','about','wholesale','trade')):
                    if absolute not in links:links.append(absolute)
            for link in links[:3]:
                try:
                    code,source,body=web.fetch(link)
                    if code==200 and host(source)==domain:
                        sub=Page(body);pages.append((source,sub,' '.join(sub.words)))
                except Exception:continue
            found={}
            for source,parsed,visible in pages:
                material=visible+' '+' '.join(unquote(l[7:].split('?')[0]) for l in parsed.links if l.lower().startswith('mailto:'))
                for email in EMAIL.findall(material):
                    email=email.lower().strip('.')
                    edomain=email.rsplit('@',1)[1]
                    if edomain!=domain and not edomain.endswith('.'+domain):continue
                    if any(w in email.split('@')[0] for w in ('noreply','no-reply','privacy','abuse')):continue
                    found.setdefault(email,source)
            if not found:continue
            def rank(email):
                local=email.split('@')[0]
                return (0 if any(w in local for w in ('wholesale','trade','sales','buy','purchas','sourc')) else 1,email)
            email=sorted(found,key=rank)[0]
            records.append({'company':title,'full_name':'','email':email,'website':final,
                'source_url':found[email],'reason':'公司官网公开邮箱；业务匹配与邮箱有效性需人工核对。',
                'email_verified':False,'provider':'public_website'})
        except Exception:continue
    return records
