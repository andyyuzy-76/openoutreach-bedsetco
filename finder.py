"""Free public website finder; optional AI credentials use Windows DPAPI."""
import ctypes
from ctypes import wintypes
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import threading

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'data'
JOB={'running':False,'notice':'免费官网邮箱采集已就绪，填写搜索词或公司网址后开始。'}
LOCK=threading.Lock()

class Blob(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]

def protect(raw,decode=False):
    buffer=ctypes.create_string_buffer(raw)
    incoming=Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    output=Blob()
    fn=ctypes.windll.crypt32.CryptUnprotectData if decode else ctypes.windll.crypt32.CryptProtectData
    fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    fn.restype=wintypes.BOOL
    if not fn(ctypes.byref(incoming),None,None,None,None,1,ctypes.byref(output)):
        raise OSError('Windows 加密配置失败')
    try:
        return ctypes.string_at(output.data,output.size)
    finally:
        free=ctypes.windll.kernel32.LocalFree
        free.argtypes=[ctypes.c_void_p];free.restype=ctypes.c_void_p
        free(output.data)

def settings():
    path=DATA/'finder.dpapi'
    if path.exists():
        config=json.loads(protect(path.read_bytes(),True))
        config.setdefault('queries','United States bedding retailers wholesale contact\nUnited States home textile distributors contact')
        config.setdefault('websites','')
        return config
    return {'model':'openai_compatible:YOUR_MODEL','base':'https://api.orendaedu.cn/v1',
            'llm_key':'','bettercontact_key':'','queries':'United States bedding retailers wholesale contact\nUnited States home textile distributors contact','websites':''}

def save(values):
    with LOCK:
        if JOB['running']:
            raise ValueError('找客户正在运行，请完成后再更改配置')
        config=settings()
        for k in ('model','base','llm_key'):
            if values.get(k,'').strip(): config[k]=values[k].strip()
        for k in ('queries','websites'):
            if k in values: config[k]=values[k].strip()
        config['bettercontact_key']=''
        if not config['base'].startswith('https://'):
            raise ValueError('AI 接口地址须为 https://')
        if ':' not in config['model']:
            raise ValueError('模型格式应为 openai_compatible:模型名称')
        DATA.mkdir(exist_ok=True)
        temp=DATA/'finder.dpapi.tmp'
        temp.write_bytes(protect(json.dumps(config).encode()))
        temp.replace(DATA/'finder.dpapi')
        (ROOT/'target.md').write_text(values.get('target','').strip() or (ROOT/'target.md').read_text(encoding='utf-8'),encoding='utf-8')
    return '配置已保存；密钥使用当前 Windows 用户加密，页面不回显。'

def environment(config):
    env=os.environ.copy()
    for key in list(env):
        if key.startswith(('OPENOUTFIND_','OUTSEND_','DJANGO_')): env.pop(key)
    env.update({'PYTHONIOENCODING':'utf-8','OPENOUTFIND_DB':str(DATA/'finder.sqlite3'),
        'OPENOUTFIND_AI_MODEL':config['model'],'OPENOUTFIND_LLM_API_BASE':config['base'],
        'OPENOUTFIND_LLM_API_KEY':config['llm_key'],
        'OPENOUTFIND_BETTERCONTACT_API_KEY':'',
        'OPENOUTFIND_OPERATOR_EMAIL':'sales@bedsetco.com','OPENOUTFIND_OPERATOR_COUNTRY':'CN',
        'OPENOUTFIND_CONTACTS_API_TOKEN':'','OPENOUTFIND_NEWSLETTER':'false',
        'OPENOUTFIND_PRODUCT_DOCS':(ROOT/'product.md').read_text(encoding='utf-8'),
        'OPENOUTFIND_CAMPAIGN_TARGET':(ROOT/'target.md').read_text(encoding='utf-8')})
    return env

def start(count,paid=False):
    count=int(count)
    if count<1 or count>10:
        raise ValueError('每次找客户数量为 1–10')
    config=settings()
    if not config.get('queries','').strip() and not config.get('websites','').strip():
        raise ValueError('请先填写搜索词或公司官网网址并保存')
    with LOCK:
        if JOB['running']: raise ValueError('已有找客户任务正在运行')
        JOB.update(running=True,notice='正在采集公开官网邮箱；不调用付费数据服务或 AI。')
    def run():
        try:
            from free_finder import find
            records=find(count,config,lambda message: JOB.update(notice=message))
            DATA.mkdir(exist_ok=True)
            with (DATA/'found.jsonl').open('a',encoding='utf-8') as out:
                for record in records: out.write(json.dumps(record,ensure_ascii=False)+'\n')
            output=io.StringIO()
            writer=csv.DictWriter(output,fieldnames=['email','company','name'])
            writer.writeheader()
            for record in records:
                writer.writerow({'email':record['email'],'company':record['company'],'name':''})
            from bedsetco import import_csv
            notice=import_csv(output.getvalue()) if records else '未找到公开邮箱。搜索站可能限制访问，或官网未公开邮箱；可填写具体公司网址再采集。'
            JOB['notice']=notice+' 本次找到 '+str(len(records))+' 家公司。仅生成待审核草稿，未发送邮件。'
        except Exception:
            JOB['notice']='官网采集未完成，请检查网络与网址。没有发送邮件。'
        finally:
            JOB['running']=False
    threading.Thread(target=run,daemon=True).start()
    return JOB['notice']
