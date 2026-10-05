"""Daily outreach quotas, separate from the SMTP identity bound to a draft."""
from __future__ import annotations

import argparse
from datetime import datetime
import json

import mail_transport as mail
import private_store

BOUNDS = {'daily_target': (0, 10000), 'daily_limit': (1, 10000),
          'new_company_limit': (1, 10000), 'interval_seconds': (0, 86400)}
LABELS = {'daily_target': '每天自动发信数量', 'daily_limit': '每日合计提交上限',
          'new_company_limit': '每天新增候选公司上限', 'interval_seconds': '发送间隔'}


def settings(mail_config=None):
    account = mail_config if mail_config is not None else mail.settings()
    # Preserve the old installation's manual cap and interval until explicitly saved.
    config = {'revision': 0, 'daily_target': 1, 'new_company_limit': 5,
              'daily_limit': account['daily_limit'],
              'interval_seconds': account['interval_seconds']}
    config.update(private_store.load('daily', config))
    for key, (lower, upper) in BOUNDS.items():
        value = config[key]
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError(f'{LABELS[key]}须为 {lower}–{upper} 的整数，请重新保存每日设置')
    if type(config['revision']) is not int or config['revision'] < 0:
        raise ValueError('每日配置版本异常，请恢复原配置')
    if config['daily_target'] > config['daily_limit']:
        raise ValueError('每日合计提交上限不能小于自动发信数量')
    return config


def save(values):
    # Serialize quota changes with the irreversible submission step.
    with mail.locked('mail'):
        old = settings()
        if str(old['revision']) != values.get('daily_revision'):
            raise ValueError('每日设置已变更，请刷新页面后保存')
        config = dict(old)
        for key, (lower, upper) in BOUNDS.items():
            try:
                config[key] = int(values.get(key, ''))
            except (TypeError, ValueError):
                raise ValueError(f'{LABELS[key]}须为整数') from None
            if not lower <= config[key] <= upper:
                raise ValueError(f'{LABELS[key]}须在 {lower}–{upper} 之间')
        raised = []
        for key in ('daily_limit', 'new_company_limit'):
            if config[key] < config['daily_target']:
                config[key] = config['daily_target']
                raised.append(f'{LABELS[key]}已同步提高到 {config[key]}')
        config['revision'] += 1
        private_store.save('daily', config)
    notice = ('已暂停每日自动发送，免费采集仍可按已启用业务运行。' if not config['daily_target'] else
              f'每日设置已保存：每天自动发信最多 {config["daily_target"]} 封，间隔 {config["interval_seconds"]} 秒。')
    if raised:
        notice += ' ' + '；'.join(raised) + '。'
    return notice + ' 当天已有提交继续计数；保存设置不会立即发信。'


def _today_stamp(value):
    if not value:
        return None
    stamp = datetime.fromisoformat(value).astimezone()
    return stamp if stamp.date() == datetime.now().astimezone().date() else None


def collected_today():
    """Count unique newly found companies, including an opt-in legacy candidate pool."""
    found = {}
    path = private_store.DATA / 'found.jsonl'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            stamp = _today_stamp(item.get('discovered_at'))
            domain = item.get('email', '').rsplit('@', 1)[-1].lower()
            if stamp and domain:
                found[domain] = min(stamp, found.get(domain, stamp))
    domains = set(found)
    baseline = 0
    ops = mail.legacy_ops()
    if ops and (ops / 'importyeti-ready-prospects.json').is_file():
        pool = json.loads((ops / 'importyeti-ready-prospects.json').read_text(encoding='utf-8-sig'))
        legacy_domains = set()
        all_legacy_domains = set()
        for item in pool.get('prospects', []):
            candidates = [d.lower() for d in item.get('domains', []) if d]
            all_legacy_domains.update(candidates)
            if _today_stamp(item.get('qualification_date')) and candidates:
                legacy_domains.add(candidates[0])
                # Use one canonical company domain when an email matches an alias.
                for domain in candidates[1:]:
                    if domain in domains:
                        domains.remove(domain)
                        domains.add(candidates[0])
        domains.update(legacy_domains)
        updated = _today_stamp(pool.get('updated_at'))
        if updated:
            baseline = int(pool.get('new_companies_added_today', 0))
            baseline += sum(stamp > updated and domain not in all_legacy_domains
                            for domain, stamp in found.items())
    return max(len(domains), baseline)


def status():
    """Read-only scheduler interface; never connects to a mail server or sends."""
    import bedsetco
    config = settings()
    with bedsetco.db() as connection:
        used = bedsetco.shared_submissions_today(connection)
    collected = collected_today()
    return {**config, 'date': datetime.now().astimezone().date().isoformat(),
            'submitted_today': used, 'automatic_remaining': max(0, config['daily_target'] - used),
            'submission_remaining': max(0, config['daily_limit'] - used),
            'collected_today': collected,
            'collection_remaining': max(0, config['new_company_limit'] - collected)}


def main():
    parser = argparse.ArgumentParser(description='读取每日开发设置和剩余额度，不发信')
    parser.add_argument('--status', action='store_true', help='同时核对本机日期的提交与新增公司数量')
    args = parser.parse_args()
    print(json.dumps(status() if args.status else settings(), ensure_ascii=True))


if __name__ == '__main__':
    main()
