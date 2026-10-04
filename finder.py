"""Free public website finder with optional, locally stored AI credentials."""
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import threading
import business_profiles as businesses
import mail_transport
import private_store
from private_store import protect  # Compatibility for existing local migration helpers.

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'data'
JOB={'running':False,'notice':'免费官网邮箱采集已就绪，填写搜索词或公司网址后开始。'}
LOCK=threading.Lock()

def credentials():
    return private_store.load('finder', {'model':'openai_compatible:YOUR_MODEL',
            'base':'https://api.openai.com/v1','llm_key':'','bettercontact_key':''})

def settings(profile=None):
    config=credentials()
    businesses.initialize(config)
    profile=profile or businesses.get()
    config.update(queries=profile.get('queries','') or businesses.generated_queries(profile),
                  websites=profile.get('websites',''),profile_id=profile['id'],profile_name=profile['name'])
    return config

def save(values):
    with LOCK:
        if JOB['running']:
            raise ValueError('找客户正在运行，请完成后再更改配置')
        config=credentials()
        for k in ('model','base','llm_key'):
            if values.get(k,'').strip(): config[k]=values[k].strip()
        # Search and product settings belong to individual business profiles.
        for k in ('queries','websites','profile_id','profile_name'):
            config.pop(k,None)
        config['bettercontact_key']=''
        if not config['base'].startswith('https://'):
            raise ValueError('AI 接口地址须为 https://')
        if ':' not in config['model']:
            raise ValueError('模型格式应为 openai_compatible:模型名称')
        private_store.save('finder',config)
    return '配置已保存在本机私有数据目录；密钥不在页面回显。'

def environment(config,profile=None):
    profile=businesses.require_ready(profile or businesses.get(config.get('profile_id')))
    env=os.environ.copy()
    for key in list(env):
        if key.startswith(('OPENOUTFIND_','OUTSEND_','DJANGO_')): env.pop(key)
    env.update({'PYTHONIOENCODING':'utf-8','OPENOUTFIND_DB':str(DATA/'finder.sqlite3'),
        'OPENOUTFIND_AI_MODEL':config['model'],'OPENOUTFIND_LLM_API_BASE':config['base'],
        'OPENOUTFIND_LLM_API_KEY':config['llm_key'],
        'OPENOUTFIND_BETTERCONTACT_API_KEY':'',
        'OPENOUTFIND_OPERATOR_EMAIL':mail_transport.sender_email(),'OPENOUTFIND_OPERATOR_COUNTRY':'',
        'OPENOUTFIND_CONTACTS_API_TOKEN':'','OPENOUTFIND_NEWSLETTER':'false',
        'OPENOUTFIND_PRODUCT_DOCS':businesses.product_docs(profile),
        'OPENOUTFIND_CAMPAIGN_TARGET':businesses.target_docs(profile)})
    return env

def start(count,paid=False,profile_id=None,revision=None):
    count=int(count)
    if count<1 or count>10:
        raise ValueError('每次找客户数量为 1–10')
    profile=businesses.require_ready(businesses.get(profile_id))
    if revision is not None and str(profile['revision'])!=str(revision):
        raise ValueError('业务配置已变更，请刷新页面后重新开始')
    config=businesses.search_config(profile)
    if not config.get('queries','').strip() and not config.get('websites','').strip():
        raise ValueError('请先填写搜索词或公司官网网址并保存')
    with LOCK:
        if JOB['running']: raise ValueError('已有找客户任务正在运行')
        JOB.update(running=True,notice=f'正在为「{profile["name"]}」采集公开官网邮箱。')
    def run():
        try:
            from free_finder import find
            from bedsetco import db,history_blocks,import_csv
            excluded=history_blocks()
            with db() as c:
                excluded.update(r[0] for r in c.execute('SELECT domain FROM blocks'))
                excluded.update(r[0] for r in c.execute('SELECT domain FROM leads'))
            records=find(count,config,lambda message: JOB.update(notice=f'「{profile["name"]}」：'+message),exclude_domains=excluded)
            for record in records:
                businesses.tag(record,profile)
            DATA.mkdir(exist_ok=True)
            with (DATA/'found.jsonl').open('a',encoding='utf-8') as out:
                for record in records: out.write(json.dumps(record,ensure_ascii=False)+'\n')
            output=io.StringIO()
            writer=csv.DictWriter(output,fieldnames=['email','company','name'])
            writer.writeheader()
            for record in records:
                writer.writerow({'email':record['email'],'company':record['company'],'name':''})
            notice=import_csv(output.getvalue(),profile=profile) if records else '未找到公开邮箱。搜索站可能限制访问，或官网未公开邮箱；可填写具体公司网址再采集。'
            JOB['notice']=f'「{profile["name"]}」：'+notice+' 本次找到 '+str(len(records))+' 家公司。仅生成待审核草稿，未发送邮件。'
        except Exception:
            JOB['notice']='官网采集未完成，请检查网络与网址。没有发送邮件。'
        finally:
            JOB['running']=False
    threading.Thread(target=run,daemon=True).start()
    return JOB['notice']
