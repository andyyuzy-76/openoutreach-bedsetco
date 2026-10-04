"""Multi-category outreach queue with shared accounting and restricted transport."""
from __future__ import annotations
import argparse
import csv
import hashlib
import html
import io
import json
import re
import secrets
import os
import shutil
import sqlite3
import subprocess
import threading
import finder
import business_profiles as businesses
from profile_ui import editor as business_editor
import webbrowser
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime, make_msgid
from email.headerregistry import Address
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parent
OPS = ROOT / 'ops'
DATA = ROOT / 'data'
SENDER = 'sales@bedsetco.com'
TOKEN = secrets.token_urlsafe(32)
LIMIT = 3
LABELS = {'draft':'待审核','approved':'已审核','submitting':'提交中／需核查',
          'accepted':'服务器已接收（待投递核查）','uncertain':'结果不明，禁止重发',
          'suppressed':'已停用','delivered':'对方服务器已接收'}

def db():
    DATA.mkdir(exist_ok=True)
    c = sqlite3.connect(DATA / 'queue.sqlite3', timeout=20)
    c.row_factory = sqlite3.Row
    c.executescript('''CREATE TABLE IF NOT EXISTS leads (
      id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, company TEXT NOT NULL,
      domain TEXT NOT NULL, name TEXT, subject TEXT NOT NULL, body TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'draft', approval TEXT, message_id TEXT,
      submitted_at TEXT, note TEXT DEFAULT '');
      CREATE TABLE IF NOT EXISTS blocks (domain TEXT PRIMARY KEY, reason TEXT);
      CREATE TABLE IF NOT EXISTS events (at TEXT, lead_id INTEGER, action TEXT, detail TEXT);''')
    columns={r['name'] for r in c.execute('PRAGMA table_info(leads)')}
    if not {'profile_id','profile_name','profile_snapshot'}.issubset(columns):
        c.execute('BEGIN IMMEDIATE')
        columns={r['name'] for r in c.execute('PRAGMA table_info(leads)')}
        for name,definition in (('profile_id',"TEXT NOT NULL DEFAULT 'bedsetco-bedding'"),
                                ('profile_name',"TEXT NOT NULL DEFAULT 'BedSetCo 床品'"),
                                ('profile_snapshot',"TEXT NOT NULL DEFAULT ''")):
            if name not in columns:
                c.execute(f'ALTER TABLE leads ADD COLUMN {name} {definition}')
        snapshot=json.dumps(businesses.legacy_snapshot(),ensure_ascii=False,sort_keys=True)
        c.execute("UPDATE leads SET profile_snapshot=? WHERE profile_snapshot=''",(snapshot,))
        # Include the frozen business identity in new approvals. Re-review old approvals.
        c.execute("UPDATE leads SET status='draft',approval=NULL WHERE status='approved'")
        c.commit()
    return c

def event(c, row_id, action, detail=''):
    c.execute('INSERT INTO events VALUES (?,?,?,?)',
              (datetime.now().astimezone().isoformat(), row_id, action, detail))

def digest(row):
    return hashlib.sha256(json.dumps([row['email'],row['subject'],row['body'],row['profile_snapshot']],
                         ensure_ascii=False).encode()).hexdigest()

def lead_business(row):
    profile=json.loads(row['profile_snapshot'])
    if profile.get('id')!=row['profile_id']:
        raise ValueError('客户业务记录异常，请核查原草稿')
    return profile

def automated_business(row):
    frozen=lead_business(row)
    current=businesses.require_ready(businesses.get(row['profile_id']))
    if not current.get('daily_enabled') or current['revision']!=frozen['revision']:
        raise ValueError('此业务已停用或配置已变更，自动任务须重新核对并准备新草稿')
    return frozen

def address(value):
    value = value.strip().lower()
    if not re.fullmatch(r'[a-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}', value):
        raise ValueError('邮箱格式不正确，请只填一个邮箱地址')
    if value.endswith('@bedsetco.com'):
        raise ValueError('此队列仅用于外部联系人')
    return value

def history_blocks():
    """Read existing suppression plus server audit exports afresh before every send."""
    blocked = set()
    p = OPS / 'importyeti-gmail-suppression-ledger.json'
    if not p.exists():
        raise ValueError('找不到现有客户去重台账，暂停导入和发送')
    data = json.loads(p.read_text(encoding='utf-8-sig'))
    blocked.update(d.lower() for d in data['suppressed_domains'])
    blocked.update(e.lower().split('@')[-1] for e in data['suppressed_emails'])
    ledger=OPS/'importyeti-submission-ledger.jsonl'
    if ledger.exists():
        for line in ledger.read_text(encoding='utf-8-sig').splitlines():
            if not line.strip():continue
            item=json.loads(line)
            blocked.update(d.lower() for d in item.get('domains',[]) if d)
            if item.get('recipient'):blocked.add(item['recipient'].lower().split('@')[-1])
    for p in list(OPS.glob('importyeti*outreach*audit*.json')) + list(DATA.glob('server-history.json')):
        data = json.loads(p.read_text(encoding='utf-8-sig'))
        for line in data.get('lines', []) + data.get('external_accept_lines', []):
            for email in re.findall(r'to=<([^>]+)>',line):
                blocked.add(email.lower().split('@')[-1])
    return blocked

def ledger_append(item):
    with (OPS/'importyeti-submission-ledger.jsonl').open('a',encoding='utf-8') as out:
        out.write(json.dumps(item,ensure_ascii=False)+'\n')
        out.flush()
        os.fsync(out.fileno())

def shared_submissions_today(c):
    today=datetime.now().astimezone().date()
    message_ids=set()
    ledger=OPS/'importyeti-submission-ledger.jsonl'
    if ledger.exists():
        for line in ledger.read_text(encoding='utf-8-sig').splitlines():
            if not line.strip():continue
            item=json.loads(line)
            if item.get('event')!='submission_started':continue
            stamp=datetime.fromisoformat(item['timestamp'])
            if stamp.astimezone().date()==today:
                message_ids.add(item.get('message_id') or item['recipient'])
    for row in c.execute('SELECT message_id,submitted_at FROM leads WHERE submitted_at IS NOT NULL'):
        if datetime.fromisoformat(row['submitted_at']).astimezone().date()==today:
            message_ids.add(row['message_id'])
    return len(message_ids)

def blocked_reason(c, row):
    domain = row['domain']
    historical = history_blocks()
    if any(domain == d or domain.endswith('.'+d) or d.endswith('.'+domain) for d in historical):
        return '现有发信／停用台账已有此公司域名'
    if any(domain == b[0] or domain.endswith('.'+b[0]) or b[0].endswith('.'+domain)
           for b in c.execute('SELECT domain FROM blocks')):
        return '此公司已停用'
    if c.execute("SELECT 1 FROM leads WHERE domain=? AND id<>? AND status IN ('submitting','accepted','uncertain','delivered')",(domain,row['id'])).fetchone():
        return '此公司已有提交记录，不再发送首封开发信'
    return ''

def import_csv(text,profile=None):
    profile=businesses.require_ready(profile or businesses.get())
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    if not reader.fieldnames or not {'email','company'}.issubset(reader.fieldnames):
        raise ValueError('CSV 必须包含 email、company 列；可选 name、subject、body')
    count = skipped = 0
    historical=history_blocks()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        existing={r[0] for r in c.execute('SELECT domain FROM leads')}
        existing.update(r[0] for r in c.execute('SELECT domain FROM blocks'))
        for row in reader:
            selected=profile
            if (row.get('profile_id') or '').strip():
                if row['profile_id'].strip()!=profile['id']:
                    raise ValueError('CSV 中的 profile_id 与本次业务不同，请按业务分别导入')
            email = address(row.get('email') or '')
            company = (row.get('company') or '').strip()
            name = (row.get('name') or '').strip()
            if not company or '\n' in company or '\r' in company:
                raise ValueError('公司名称不能为空或包含换行')
            default_subject,default_body=businesses.render(selected,company,name)
            subject = (row.get('subject') or '').strip() or default_subject
            body = (row.get('body') or '').strip() or default_body
            if '\n' in subject or '\r' in subject or len(body)>30000:
                raise ValueError('主题不能换行，正文不得超过 30000 字符')
            domain=email.split('@')[-1]
            if any(domain==d or domain.endswith('.'+d) or d.endswith('.'+domain) for d in historical|existing):
                skipped += 1
                continue
            cur = c.execute('INSERT OR IGNORE INTO leads (email,company,domain,name,subject,body,profile_id,profile_name,profile_snapshot) VALUES (?,?,?,?,?,?,?,?,?)',
                            (email,company,domain,name,subject,body,selected['id'],selected['name'],
                             json.dumps(selected,ensure_ascii=False,sort_keys=True)))
            existing.add(domain)
            count += cur.rowcount
            skipped += not cur.rowcount
    return f'已导入 {count} 位联系人，跳过 {skipped} 位已有记录的联系人。所有新草稿均待审核。'

def update(row_id, values):
    with db() as c:
        row = c.execute('SELECT * FROM leads WHERE id=?',(row_id,)).fetchone()
        if not row or row['status'] not in ('draft','approved'):
            raise ValueError('仅可修改未提交的草稿')
        subject,body = values['subject'].strip(),values['body'].strip()
        if not subject or not body or '\n' in subject or '\r' in subject or len(body)>30000:
            raise ValueError('请填写有效主题与正文')
        c.execute("UPDATE leads SET subject=?,body=?,status='draft',approval=NULL WHERE id=?",(subject,body,row_id))
        event(c,row_id,'edit')

def approve(row_id, *, automated=False):
    with db() as c:
        row = c.execute('SELECT * FROM leads WHERE id=?',(row_id,)).fetchone()
        if not row or row['status']!='draft':
            raise ValueError('仅可审核待审核草稿')
        if automated:
            automated_business(row)
        if reason := blocked_reason(c,row):
            raise ValueError(reason)
        c.execute("UPDATE leads SET status='approved',approval=? WHERE id=?",(digest(row),row_id))
        event(c,row_id,'approve',digest(row))

def suppress(row_id):
    with db() as c:
        row = c.execute('SELECT * FROM leads WHERE id=?',(row_id,)).fetchone()
        if not row:
            raise ValueError('联系人不存在')
        c.execute('INSERT OR REPLACE INTO blocks VALUES (?,?)',(row['domain'],'用户停用／退订'))
        c.execute("UPDATE leads SET status='suppressed',approval=NULL WHERE domain=? AND status IN ('draft','approved')",(row['domain'],))
        event(c,row_id,'suppress',row['domain'])
        ledger_append({'event':'company_suppressed','timestamp':datetime.now().astimezone().isoformat(),
            'company':row['company'],'domains':[row['domain']],'recipient':row['email'],
            'reason':'用户停用／退订','action':'禁止再联系'})

def send(row_id, *, automated=False):
    if (DATA / 'sender-migration-pending.json').exists():
        raise ValueError('sales@bedsetco.com 发件切换待服务器规则同步，当前暂停发送。')
    script = OPS / 'importyeti-mail-send.ps1'
    pwsh = shutil.which('pwsh')
    if not script.exists() or not pwsh:
        raise ValueError('缺少现有受限发信脚本或 PowerShell 7')
    # Refresh live server history so the existing daily mail flow is also considered.
    history=subprocess.run([pwsh,'-NoProfile','-NonInteractive','-File',
        str(OPS/'importyeti-mail-audit.ps1'),'-Operation','recent_outbound'],
        capture_output=True,text=True,encoding='utf-8',timeout=35,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if history.returncode:
        raise ValueError('无法读取服务器发信记录，暂停发送以免重复联系')
    report=json.loads(history.stdout.lstrip('\ufeff'))
    if not report.get('ok') or 'lines' not in report:
        raise ValueError('服务器发信记录格式异常，暂停发送')
    (DATA/'server-history.json').write_text(json.dumps(report,ensure_ascii=False),encoding='utf-8')
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT * FROM leads WHERE id=?',(row_id,)).fetchone()
        if not row or row['status']!='approved' or row['approval']!=digest(row):
            raise ValueError('须先审核当前版本；已提交的邮件不能重发')
        if automated:
            automated_business(row)
        if reason := blocked_reason(c,row):
            raise ValueError(reason)
        n = shared_submissions_today(c)
        if n >= LIMIT:
            raise ValueError(f'每日合计提交上限为 {LIMIT} 封，今天已用完')
        last=c.execute('SELECT max(submitted_at) FROM leads').fetchone()[0]
        if last and datetime.now().astimezone()-datetime.fromisoformat(last)<timedelta(minutes=4):
            raise ValueError('两封新邮件至少间隔 4 分钟，请稍后发送')
        message = EmailMessage(policy=SMTP)
        profile=lead_business(row)
        message['From'] = Address(display_name=profile.get('sender_name') or profile['brand'],addr_spec=SENDER)
        message['Reply-To'] = SENDER
        message['To'] = row['email']
        message['Subject'] = row['subject']
        message['Date'] = format_datetime(datetime.now().astimezone())
        message['Message-ID'] = make_msgid(domain='bedsetco.com')
        message['List-Unsubscribe'] = f'<mailto:{SENDER}?subject=unsubscribe>'
        body = row['body']
        if 'unsubscribe' not in body.lower():
            raise ValueError('正文须保留回复 unsubscribe 的退订说明；补充后重新审核')
        message.set_content(body)
        raw = message.as_bytes()
        if len(raw)>65536:
            raise ValueError('邮件超过现有服务器单封大小上限')
        out = DATA / 'messages'
        out.mkdir(exist_ok=True)
        eml = out / f'{row_id}.eml'
        eml.write_bytes(raw)
        c.execute("UPDATE leads SET status='submitting',message_id=?,submitted_at=? WHERE id=?",(message['Message-ID'],datetime.now().astimezone().isoformat(),row_id))
        event(c,row_id,'submit',message['Message-ID'])
        ledger_append({'event':'submission_started','timestamp':datetime.now().astimezone().isoformat(),
            'company':row['company'],'domains':[row['domain']],'recipient':row['email'],
            'from':SENDER,'message_id':message['Message-ID'],'message_path':str(eml),
            'profile_id':row['profile_id'],'profile_name':row['profile_name'],
            'category':profile['category'],'profile_revision':profile['revision'],
            'channel':'restricted-self-hosted-wrapper','action':'未发/待验收'})
    # State is persisted before transport; crash/timeout must never cause an automatic retry.
    state,note = 'uncertain','提交结果不明，请按 Message-ID 查询服务器记录，勿重发。'
    try:
        result = subprocess.run([pwsh,'-NoLogo','-NoProfile','-NonInteractive','-File',str(script),
            '-MessagePath',str(eml),'-ExpectedRecipient',row['email']],capture_output=True,text=True,
            encoding='utf-8',timeout=50,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode==0:
            ack = json.loads(result.stdout.lstrip('\ufeff'))
            if ack.get('wrapper_accepted') is True and ack.get('recipient')==row['email'] and ack.get('message_id')==message['Message-ID']:
                state,note = 'accepted','服务器已接收；对方服务器的最终投递状态尚未核查。'
    except (subprocess.TimeoutExpired,OSError,ValueError):
        pass
    with db() as c:
        c.execute('UPDATE leads SET status=?,note=? WHERE id=?',(state,note,row_id))
        event(c,row_id,state,note)
    return note

def audit(row_id):
    with db() as c:
        row = c.execute('SELECT * FROM leads WHERE id=?',(row_id,)).fetchone()
    if not row or not row['message_id']:
        raise ValueError('此联系人尚无提交记录')
    result = subprocess.run([shutil.which('pwsh') or 'pwsh','-NoProfile','-NonInteractive','-File',
        str(OPS/'importyeti-mail-audit.ps1'),'-Operation','lookup','-Needle',row['message_id']],
        capture_output=True,text=True,encoding='utf-8',timeout=35,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:
        raise ValueError('服务器核查失败，请稍后再查；不要重发')
    report = json.loads(result.stdout.lstrip('\ufeff'))
    (DATA/f'audit-{row_id}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    delivered = report.get('failure_seen') is False and any(f"to=<{row['email']}>" in line and 'status=sent' in line
                    and re.search(r'\(250[ -]',line) and 'relay=127.0.0.1' not in line
                    for line in report.get('external_accept_lines',[]))
    with db() as c:
        if delivered:
            c.execute("UPDATE leads SET status='delivered',note='对方服务器已接收（不代表进入收件箱或已读）' WHERE id=?",(row_id,))
        event(c,row_id,'audit','external MX accepted' if delivered else 'not confirmed')
    return '对方邮件服务器已接收。' if delivered else '暂未确认对方服务器接收，请查看核查记录，勿重发。'

def page(notice=''):
    if (DATA / 'sender-migration-pending.json').exists():
        notice = '发信暂停：sales@bedsetco.com 正在同步服务器发件规则，实际投递测试尚未完成。免费采集与草稿编辑可继续。 ' + notice
    esc = html.escape
    with db() as c:
        rows = c.execute('SELECT * FROM leads ORDER BY id DESC').fetchall()
    config=finder.settings()
    state=businesses.initialize()
    profile=next(p for p in state['profiles'] if p['id']==state['active_id'])
    business_form=business_editor(state,profile,TOKEN)
    profile_fields=(f'<input type="hidden" name="profile_id" value="{esc(profile["id"],quote=True)}">'
                    f'<input type="hidden" name="profile_revision" value="{profile["revision"]}">')
    daily_names='、'.join(p['name'] for p in state['profiles'] if p.get('daily_enabled')) or '暂无（请在业务配置中启用）'
    candidates=[]
    result_path=DATA/'found.jsonl'
    if result_path.exists():
        for line in result_path.read_text(encoding='utf-8').splitlines()[-20:]:
            record=json.loads(line)
            candidates.append('<tr>'+''.join(f'<td>{esc(str(record.get(k) or ""))}</td>'
                for k in ('profile_name','company','email','source_url','reason'))+'</tr>')
    results=('<section><h2>最近找到的候选客户</h2><table><tr><th>业务</th><th>公司</th><th>邮箱</th><th>官网来源</th><th>说明（需核对）</th></tr>'
             +''.join(candidates)+'</table></section>') if candidates else ''
    ai_status='AI 密钥已保存。' if config['llm_key'] else 'AI 密钥未配置。'
    test_path=finder.DATA/'ai-test.json'
    if test_path.exists():
        report=json.loads(test_path.read_text(encoding='utf-8'))
        if report.get('model')==config['model'] and report.get('base')==config['base'] and report.get('structured_test')=='passed':
            ai_status+=' NewAPI 实际调用与结构化输出测试通过。'
    data_status='免费官网采集已启用，不需要数据服务密钥，也不调用 AI。'
    finder_form=f'''<section><h2>免费找客户 · {esc(profile['name'])}</h2><p>{esc(data_status)}</p><p>{esc(finder.JOB['notice'])}</p>
    <p>按当前业务的品类、目标市场、客户类型及搜索设置采集。填写并保存业务配置后开始。</p>
    <form method="post"><input type="hidden" name="token" value="{TOKEN}">{profile_fields}
    <label>本次数量（1–10）<input name="count" type="number" min="1" max="10" value="3"></label>
    <button name="action" value="find">为当前业务免费找客户</button></form>
    <p><a href="/">刷新运行状态</a> · 每次最多扫描 30 家候选公司。官网公开邮箱仍需核对业务和用途；此按钮只生成草稿。</p>
    <details><summary>NewAPI 共用设置</summary><p>{esc(ai_status)}</p>
    <form method="post"><input type="hidden" name="token" value="{TOKEN}">
    <label>AI 模型（例如 openai_compatible:模型名称）<input name="model" value="{esc(config['model'],quote=True)}"></label>
    <label>AI 接口地址<input name="base" value="{esc(config['base'],quote=True)}"></label>
    <label>AI 密钥（留空保留已保存的密钥）<input type="password" name="llm_key" autocomplete="new-password"></label>
    <button name="action" value="config">保存 NewAPI 设置</button></form>
    <p>免费模式仅访问公开搜索页面和公司官网，遵守官网抓取规则。不会调用 BetterContact 或 AI，不产生这些服务的调用费用。NewAPI 配置保留供后续 AI 功能使用。</p>
    </details></section>'''
    cards=[]
    for r in rows:
        origin=lead_business(r)
        business_label=f'{r["profile_name"]} · {origin["category"]} · 生成时版本 {origin["revision"]}'
        fields=f'<input type="hidden" name="token" value="{TOKEN}"><input type="hidden" name="id" value="{r["id"]}">'
        editable=r['status'] in ('draft','approved')
        buttons='<button name="action" value="save">保存修改</button>' if editable else ''
        if r['status']=='draft': buttons+='<button name="action" value="approve">审核通过</button>'
        if r['status']=='approved': buttons+='<button name="action" value="send" onclick="return confirm(\'将向此联系人发送当前已审核邮件，确定发送？\')">发送此封</button>'
        if r['message_id']: buttons+='<button name="action" value="audit">核查投递</button>'
        buttons+='<button name="action" value="suppress">停用此公司／记录退订</button>'
        disabled='' if editable else ' readonly'
        cards.append(f'<section><h2>{esc(r["company"])} · {esc(r["email"])}</h2><p class="tag">{esc(business_label)}</p><p>{LABELS[r["status"]]} {esc(r["note"])}</p><form method="post">{fields}<label>邮件主题<input name="subject" value="{esc(r["subject"],quote=True)}"{disabled}></label><label>邮件正文<textarea name="body"{disabled}>{esc(r["body"])}</textarea></label><div>{buttons}</div></form><small>{esc(r["message_id"] or "")}</small></section>')
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>全品类客户开发</title>
    <style>body{{font:16px system-ui;background:#f7f4ef;color:#302d29;max-width:960px;margin:40px auto;padding:0 24px}}section{{background:white;border:1px solid #ded8cf;padding:24px;margin:20px 0;border-radius:14px}}h1{{font-size:32px}}h2{{font-size:19px}}h3{{font-size:16px;margin-top:24px}}label{{display:block;margin:12px 0}}input,textarea,select{{display:block;box-sizing:border-box;width:100%;padding:12px;font:15px system-ui;border:1px solid #ccc;border-radius:6px}}textarea{{height:140px}}textarea[name="body"],textarea[name="body_template"]{{height:300px}}button{{padding:10px 14px;margin:4px;border:0;border-radius:6px;background:#385747;color:white;cursor:pointer}}small{{color:#777}}.notice{{padding:15px;background:#e5eee7}}a{{color:#385747}}details{{margin:18px 0}}summary{{cursor:pointer}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f6f4;padding:16px}}.check{{display:flex;align-items:center;gap:8px}}.check input{{width:18px}}.tag{{color:#385747;background:#edf3ef;padding:8px}}table{{font-size:13px;width:100%;table-layout:fixed}}td,th{{overflow-wrap:anywhere;text-align:left;padding:6px}}</style>
    <h1>全品类客户开发</h1><p>发件邮箱：{SENDER} · 自有邮件服务器 · 当前业务：{esc(profile['name'])}</p>
    <section><h2>每日自动开发</h2><p>北京时间每天 09:30：按已启用的业务免费搜索官网，核对产品匹配和公开邮箱，去重后自动发送 1 封合格新客户首信，并核查投递。现有 Codex 每日任务执行，电脑及 Codex 须保持可运行。</p><p>已启用业务：{esc(daily_names)}</p><p>所有业务合计每天最多新增 5 家公司；自动任务与本工作台共享提交台账，总提交上限 {LIMIT} 封。没有合格客户或核查失败时记录原因。采集不调用付费数据服务；Codex 自动任务使用你的现有额度。</p></section>
    <p>手动导入的名单仍先审核再发送。回复与退订须从现有邮箱查看，并在这里记录停用；停用记录同步每日任务的共用台账。</p>
    <p class="notice">{esc(notice or '导入客户名单后，先修改草稿，再审核和发送。模板不包含未确认的产品参数。')}</p>
    {business_form}{finder_form}{results}<section><h2>导入客户 CSV · {esc(profile['name'])}</h2><p>列名：email,company,name；可选 subject,body。按当前业务生成草稿。<a href="/sample.csv">下载空白模板</a></p>
    <form method="post"><input type="hidden" name="token" value="{TOKEN}">{profile_fields}<textarea name="csv" placeholder="粘贴 CSV 内容"></textarea><button name="action" value="import">为当前业务导入并生成草稿</button></form></section>
    <h2>客户草稿与历史（所有业务）</h2>
    {''.join(cards) or '<p>暂无联系人。现有历史发信名单仅用于去重，不会自动加入新队列。</p>'}</html>'''

class Handler(BaseHTTPRequestHandler):
    def valid_host(self):
        return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'
    def do_GET(self):
        if not self.valid_host():
            self.send_error(403);return
        if self.path=='/sample.csv':
            raw=b'email,company,name,subject,body\r\n'
            self.send_response(200);self.send_header('Content-Type','text/csv');self.end_headers();self.wfile.write(raw);return
        if self.path!='/':
            self.send_error(404);return
        self.respond(page())
    def respond(self,content):
        raw=content.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Frame-Options','DENY')
        self.end_headers();self.wfile.write(raw)
    def do_POST(self):
        origin=self.headers.get('Origin')
        if not self.valid_host() or (origin and origin!=f'http://127.0.0.1:{self.server.server_port}'):
            self.send_error(403);return
        size=int(self.headers.get('Content-Length','0'))
        if size<1 or size>1000000:
            self.send_error(413);return
        values={k:v[0] for k,v in parse_qs(self.rfile.read(size).decode('utf-8'),keep_blank_values=True).items()}
        if not secrets.compare_digest(values.get('token',''),TOKEN):
            self.send_error(403);return
        try:
            action=values['action'];row_id=int(values.get('id') or 0)
            if action=='import':
                profile=businesses.get(values.get('profile_id'))
                if str(profile['revision'])!=values.get('profile_revision'):
                    raise ValueError('业务配置已变更，请刷新页面后重新导入')
                notice=import_csv(values['csv'],profile=profile)
            elif action=='profile_create': notice=businesses.create(values.get('new_profile_name',''))
            elif action=='profile_select': notice=businesses.select(values['profile_id'])
            elif action=='profile_save': notice=businesses.save(values)
            elif action=='profile_templates': notice=businesses.reset_templates(values['profile_id'],values['profile_revision'])
            elif action=='config': notice=finder.save(values)
            elif action=='find': notice=finder.start(values.get('count','3'),profile_id=values.get('profile_id'),revision=values.get('profile_revision'))
            elif action=='save': update(row_id,values);notice='已保存修改，请重新审核。'
            elif action=='approve': approve(row_id);notice='审核通过。点击发送此封才会发信。'
            elif action=='send': notice=send(row_id)
            elif action=='suppress': suppress(row_id);notice='已停用此公司，后续导入或发送将被阻止。'
            elif action=='audit': notice=audit(row_id)
            else: raise ValueError('未知操作')
        except Exception as exc:
            notice=f'操作未完成：{exc}'
        self.respond(page(notice))
    def log_message(self,*args):
        pass

def main():
    parser=argparse.ArgumentParser(description='全品类本地客户开发工作台')
    parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    finder.settings()  # Migrate the old search configuration before opening the queue.
    with db(): pass
    url=f'http://127.0.0.1:{args.port}'
    try:
        server=HTTPServer(('127.0.0.1',args.port),Handler)
    except OSError:
        print(f'本机端口已被使用，请查看 {url} 或选择其他端口。',flush=True)
        if not args.no_browser: webbrowser.open(url)
        return
    (DATA/'workbench.pid').write_text(str(os.getpid()),encoding='ascii')
    print(f'全品类客户开发工作台：{url}',flush=True)
    if not args.no_browser:
        threading.Timer(0.7,lambda:webbrowser.open(url)).start()
    server.serve_forever()

if __name__=='__main__':
    main()
