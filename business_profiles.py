"""Private, versioned business profiles shared by the workbench and daily collector."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import string
import threading
import time
from urllib.parse import urlparse
import uuid
import mail_transport
import private_store

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
FILE = DATA / 'business-profiles.json'
LEGACY_ID = 'bedsetco-bedding'
LOCK = threading.RLock()
VARIABLES = ('company', 'name', 'brand', 'category', 'product_intro', 'sender_name',
             'sender_email', 'website', 'buyer_role', 'target_market')
FIELDS = ('name', 'brand', 'category', 'search_terms', 'product_intro', 'product_facts',
          'website', 'sender_name', 'language', 'target_markets', 'buyer_types',
          'buyer_role', 'target', 'exclude_countries', 'exclude_industries',
          'queries', 'websites', 'subject_template', 'body_template')
SINGLE_LINE = ('name', 'brand', 'category', 'website', 'sender_name', 'buyer_role')
FACT_RULE = ('Use only the product facts confirmed in this business profile. Never infer '
             'materials, components, colours, sizes, packaging, certifications, prices, MOQ, '
             'manufacturing capacity or lead times. Do not reuse another category or variant\'s facts.')
TEMPLATES = {
    'en': (
        '{category} sourcing contact at {company}',
        'Hi {name},\n\nI’m reaching out from {brand}. {product_intro}\n\n'
        'Could you point me to the person who handles {category} sourcing at {company}? '
        'If relevant, I’d be happy to share a short product overview with a couple of relevant options '
        'based on your requirements.\n\nIf this isn’t relevant, just let me know and I won’t follow up.\n\n'
        'Best regards,\n{sender_name}\n{brand}\n{sender_email}\n{website}\n\n'
        'To opt out, please reply “unsubscribe”.'),
    'zh': (
        '请教 {company} 的{category}采购联系人',
        '{name}，您好！\n\n我是{brand}的{sender_name}。{product_intro}\n\n'
        '想请教贵司负责{category}采购的同事如何联系。如有需要，我可以根据贵司需求，'
        '提供简短的产品介绍和相关产品选项。\n\n若不相关，请直接告知，我将不再跟进。\n\n'
        '{sender_name}\n{brand}\n{sender_email}\n{website}\n\n'
        '如不希望收到后续邮件，请回复 unsubscribe。')}


@contextmanager
def locked():
    """Serialize profile writes across the UI and separate daily processes."""
    private_store.prepare()
    with LOCK, (DATA / 'business-profiles.lock').open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            until = time.monotonic() + 20
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= until:
                        raise ValueError('业务配置正在保存，请稍后再试')
                    time.sleep(0.05)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def stamp():
    return datetime.now().astimezone().isoformat()


def blank(name='新业务', profile_id=None):
    profile = dict.fromkeys(FIELDS, '')
    profile.update(id=profile_id or uuid.uuid4().hex, name=name, language='en',
                   target_markets='United States', buyer_types='retailers\ndistributors\nimporters',
                   buyer_role='purchasing and sourcing', daily_enabled=False, revision=1,
                   created_at=stamp(), updated_at=stamp())
    profile['subject_template'], profile['body_template'] = TEMPLATES['en']
    return profile


def legacy_snapshot():
    """Historical rows retain the original From label, regardless of later profile edits."""
    p = blank('BedSetCo 床品', LEGACY_ID)
    p.update(brand='BedSetCo', category='bedding sets', search_terms='bedding\nhome textiles',
             product_intro='BedSetCo is a home textile business focused on bedding sets.',
             product_facts=FACT_RULE, sender_name='BedSetCo', website='https://bedsetco.com')
    return p


def _write(state):
    temp = FILE.with_name(FILE.name + '.' + uuid.uuid4().hex + '.tmp')
    with temp.open('w', encoding='utf-8', newline='\n') as out:
        json.dump(state, out, ensure_ascii=False, indent=2)
        out.write('\n')
        out.flush()
        os.fsync(out.fileno())
    temp.replace(FILE)


def _read(legacy_config=None):
    if not FILE.exists():
        # Only an explicitly configured old deployment is migrated as BedSetCo.
        # A fresh queue or an AI key does not establish the user's industry.
        if mail_transport.legacy_ops():
            if legacy_config is None:
                from finder import credentials
                legacy_config = credentials()
            p = legacy_snapshot()
            p.update(sender_name='Andy Yu', daily_enabled=True,
                     target_markets='United States\nCanada\nEurope\nAustralia',
                     buyer_types='bedding retailers\nhome textile distributors\nbedding brands\nimporters',
                     exclude_countries='India',
                     exclude_industries='unrelated industrial goods\nfood\nelectronics\nmedical\nchemical',
                     queries=legacy_config.get('queries', ''), websites=legacy_config.get('websites', ''))
            if (ROOT / 'target.md').exists():
                p['target'] = (ROOT / 'target.md').read_text(encoding='utf-8')
        else:
            p = blank('默认业务')
        _write({'version': 1, 'active_id': p['id'], 'profiles': [p]})
    state = json.loads(FILE.read_text(encoding='utf-8'))
    if state.get('version') != 1 or not state.get('profiles'):
        raise ValueError('业务配置格式异常，请恢复原配置文件')
    ids = [p['id'] for p in state['profiles']]
    if len(set(ids)) != len(ids) or state.get('active_id') not in ids:
        raise ValueError('业务配置编号异常，请恢复原配置文件')
    return state


def initialize(legacy_config=None):
    with locked():
        return deepcopy(_read(legacy_config))


def get(profile_id=None):
    state = initialize()
    wanted = profile_id or state['active_id']
    for p in state['profiles']:
        if p['id'] == wanted:
            return p
    raise ValueError('业务不存在，请刷新页面')


def missing(profile):
    required = {'name': '业务名称', 'brand': '公司／品牌', 'category': '产品品类',
                'product_intro': '已确认的产品介绍', 'sender_name': '署名姓名',
                'target_markets': '目标市场', 'buyer_types': '目标客户类型'}
    return [label for key, label in required.items() if not profile.get(key, '').strip()]


def require_ready(profile):
    if gaps := missing(profile):
        raise ValueError('请先完善业务配置：' + '、'.join(gaps))
    validate(profile)
    return profile


def lines(value):
    return list(dict.fromkeys(s.strip() for s in value.splitlines() if s.strip()))


def generated_queries(profile):
    terms = lines(profile.get('search_terms', '')) or [profile.get('category', '')]
    markets = lines(profile.get('target_markets', ''))
    buyers = lines(profile.get('buyer_types', ''))
    if not all((terms[0], markets, buyers)):
        return ''
    return '\n'.join(dict.fromkeys(
        f'{markets[i % len(markets)]} {terms[i % len(terms)]} {buyers[i % len(buyers)]} contact'
        for i in range(4)))


def search_config(profile):
    require_ready(profile)
    return {'queries': profile.get('queries', '').strip() or generated_queries(profile),
            'websites': profile.get('websites', ''), 'profile_id': profile['id'],
            'profile_name': profile['name'], 'category': profile['category']}


def template_check(value):
    try:
        for _, field, spec, conversion in string.Formatter().parse(value):
            if field is not None and (field not in VARIABLES or spec or conversion):
                raise ValueError('邮件模板只支持：' + '、'.join('{' + k + '}' for k in VARIABLES))
    except ValueError as exc:
        raise ValueError('邮件模板格式不正确：' + str(exc)) from exc


def validate(profile):
    for key in FIELDS:
        value = profile.get(key, '')
        if not isinstance(value, str) or '\x00' in value or len(value) > 20000:
            raise ValueError('业务字段格式不正确或内容过长')
    for key in SINGLE_LINE:
        if '\n' in profile[key] or '\r' in profile[key] or len(profile[key]) > 250:
            raise ValueError('业务名称、品牌、品类、网址和署名等简短字段不能换行或超过 250 字符')
    if not profile['name']:
        raise ValueError('请填写业务名称')
    if profile['language'] not in TEMPLATES:
        raise ValueError('请选择中文或英文，其他语言可自行修改邮件模板')
    if profile['website']:
        p = urlparse(profile['website'])
        if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
            raise ValueError('公司网址须为完整的 http:// 或 https:// 地址')
    if not profile['subject_template'] or not profile['body_template']:
        raise ValueError('请填写邮件主题与正文模板')
    if '\n' in profile['subject_template'] or '\r' in profile['subject_template']:
        raise ValueError('邮件主题模板不能换行')
    template_check(profile['subject_template'])
    template_check(profile['body_template'])
    if 'unsubscribe' not in profile['body_template'].lower():
        raise ValueError('邮件正文模板须保留回复 unsubscribe 的退订说明')
    if len(lines(profile['queries'])) > 4 or len(lines(profile['websites'])) > 30:
        raise ValueError('自定义搜索词最多 4 组，公司官网最多 30 个')


def create(name):
    name = name.strip()
    if not name or len(name) > 100 or '\n' in name or '\r' in name:
        raise ValueError('新业务名称须为 1–100 字符，不能换行')
    with locked():
        state = _read()
        p = blank(name)
        state['profiles'].append(p)
        state['active_id'] = p['id']
        _write(state)
    return f'已新建「{name}」。请填写品类、产品介绍和目标客户后保存。'


def select(profile_id):
    with locked():
        state = _read()
        p = next((p for p in state['profiles'] if p['id'] == profile_id), None)
        if p is None:
            raise ValueError('业务不存在')
        state['active_id'] = profile_id
        _write(state)
    return f'已切换到「{p["name"]}」。新采集和导入按此业务生成草稿。'


def save(values):
    with locked():
        state = _read()
        p = next((p for p in state['profiles'] if p['id'] == values.get('profile_id')), None)
        if p is None or str(p['revision']) != values.get('profile_revision'):
            raise ValueError('此业务配置已变更，请刷新页面后重新保存')
        new = deepcopy(p)
        for key in FIELDS:
            if key in values:
                new[key] = values[key].strip()
        if (new['language'] in TEMPLATES and new['language'] != p['language']
                and (new['subject_template'], new['body_template']) == TEMPLATES[p['language']]):
            new['subject_template'], new['body_template'] = TEMPLATES[new['language']]
        new['daily_enabled'] = values.get('daily_enabled') == 'on'
        validate(new)
        if new['daily_enabled']:
            require_ready(new)
        new.update(revision=p['revision'] + 1, updated_at=stamp())
        state['profiles'][state['profiles'].index(p)] = new
        _write(state)
    return f'「{new["name"]}」配置已保存。已有草稿保留生成时的业务信息，新配置用于新客户。'


def reset_templates(profile_id, revision):
    with locked():
        state = _read()
        p = next((p for p in state['profiles'] if p['id'] == profile_id), None)
        if p is None or str(p['revision']) != revision:
            raise ValueError('业务配置已变更，请刷新页面')
        p['subject_template'], p['body_template'] = TEMPLATES[p['language']]
        p.update(revision=p['revision'] + 1, updated_at=stamp())
        _write(state)
    return '已按保存的邮件语言恢复通用品类模板。'


def render(profile, company, name='', sender_email=None):
    require_ready(profile)
    values = {k: profile.get(k, '') for k in VARIABLES}
    values.update(company=company, name=name or ('there' if profile['language'] == 'en' else '采购负责人'),
                  sender_email=(mail_transport.sender_email() if sender_email is None else sender_email)
                               or '[请先配置发件邮箱]',
                  target_market=profile['target_markets'].replace('\n', ', '))
    subject = profile['subject_template'].format_map(values).strip()
    body = profile['body_template'].format_map(values).strip()
    if not subject or '\n' in subject or '\r' in subject or not body or len(body) > 30000:
        raise ValueError('生成的主题或正文无效，请检查业务模板')
    return subject, body


def product_docs(profile):
    return '\n\n'.join((profile['brand'], profile['product_intro'], profile['product_facts'], FACT_RULE))


def target_docs(profile):
    return (f'Category: {profile["category"]}\nMarkets: {profile["target_markets"]}\n'
            f'Buyer types: {profile["buyer_types"]}\nBuyer role: {profile["buyer_role"]}\n'
            f'Exclude countries: {profile["exclude_countries"]}\n'
            f'Exclude industries: {profile["exclude_industries"]}\n{profile["target"]}\n'
            'Confirm fit and the public contact on the official company website. Never invent purchasing needs.')


def daily_profiles():
    state = initialize()
    enabled = [require_ready(p) for p in state['profiles'] if p.get('daily_enabled')]
    if enabled:
        offset = datetime.now().astimezone().date().toordinal() % len(enabled)
        enabled = enabled[offset:] + enabled[:offset]
    return enabled


def tag(record, profile):
    record.update(profile_id=profile['id'], profile_name=profile['name'], category=profile['category'],
                  profile_revision=profile['revision'], profile_snapshot=deepcopy(profile))
    return record


def main():
    parser = argparse.ArgumentParser(description='查看业务配置（不含 API 密钥）')
    parser.add_argument('--active', action='store_true')
    parser.add_argument('--daily', action='store_true')
    parser.add_argument('--profile')
    args = parser.parse_args()
    if args.profile or args.active:
        result = get(args.profile)
    elif args.daily:
        result = daily_profiles()
    else:
        state = initialize()
        result = {'active_id': state['active_id'], 'profiles': [
            {'id': p['id'], 'name': p['name'], 'category': p['category'], 'revision': p['revision'],
             'daily_enabled': p['daily_enabled'], 'missing': missing(p)} for p in state['profiles']]}
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()
