"""Quota-aware free collector for a daily automation; never sends mail."""
import argparse
import csv
from datetime import datetime
import io
import json
import os
import time

import bedsetco
import business_profiles as businesses
import daily_settings
import finder
import private_store
from free_finder import find

BATCH_LIMIT = 30


def collect(count=None, profile_id=None):
    if count is not None and (type(count) is not int or not 1 <= count <= BATCH_LIMIT):
        raise ValueError('每批采集数量须为 1–30 的整数')
    with private_store.locked('daily_collect'):
        return _collect(count, profile_id)


def _collect(count, profile_id):
    policy = daily_settings.settings()
    remaining = max(0, policy['new_company_limit'] - daily_settings.collected_today())
    requested = min(count if count is not None else BATCH_LIMIT, BATCH_LIMIT, remaining)
    finder.settings()  # Preserve the old private search settings on first migration.
    profiles = ([businesses.require_ready(businesses.get(profile_id))] if profile_id else
                businesses.daily_profiles())[:requested]
    excluded = bedsetco.history_blocks()
    with bedsetco.db() as connection:
        excluded.update(row[0] for row in connection.execute('SELECT domain FROM blocks'))
        excluded.update(row[0] for row in connection.execute('SELECT domain FROM leads'))
    records, jobs = [], []
    deadline = time.monotonic() + 600
    for index, profile in enumerate(profiles):
        policy = daily_settings.settings()
        remaining = min(requested - len(records),
                        policy['new_company_limit'] - daily_settings.collected_today())
        if remaining <= 0:
            break
        quota = max(1, (remaining + len(profiles) - index - 1) // (len(profiles) - index))
        config = businesses.search_config(profile)
        config['budget_seconds'] = max(1, deadline - time.monotonic()) / (len(profiles) - index)
        if time.monotonic() >= deadline:
            jobs.append({'profile_id': profile['id'], 'profile_name': profile['name'],
                         'notice': '本轮采集时间预算已用完'})
            break
        found = find(quota, config, exclude_domains=excluded)
        for record in found:
            # Preserve the title whitespace fix already used by this installation.
            record['company'] = ' '.join(str(record.get('company') or '').split())
            businesses.tag(record, profile)
            record['discovered_at'] = datetime.now().astimezone().isoformat()
        # Re-read limits after network discovery, in case the user reduced them.
        # Append each completed batch before releasing the submission/config lock.
        with bedsetco.mail.locked('mail'):
            policy = daily_settings.settings()
            remaining = max(0, policy['new_company_limit'] - daily_settings.collected_today())
            found = found[:min(remaining, requested - len(records))]
            with bedsetco.db() as connection:
                old_ids = {row[0] for row in connection.execute('SELECT id FROM leads')}
            text = io.StringIO()
            writer = csv.DictWriter(text, fieldnames=['email', 'company', 'name'])
            writer.writeheader()
            for record in found:
                writer.writerow({'email': record['email'], 'company': record['company'], 'name': ''})
            notice = (bedsetco._import_csv(text.getvalue(), profile=profile) if found else
                      '没有新增可用候选，或当日新增额度已用完。')
            imported = []
            with bedsetco.db() as connection:
                for record in found:
                    row = connection.execute('SELECT id,status,profile_id FROM leads WHERE email=?',
                                             (record['email'],)).fetchone()
                    if row and row['id'] not in old_ids and row['profile_id'] == profile['id']:
                        record.update(lead_id=row['id'], queue_status=row['status'])
                        imported.append(record)
            private_store.prepare()
            with (finder.DATA / 'found.jsonl').open('a', encoding='utf-8') as output:
                for record in imported:
                    output.write(json.dumps(record, ensure_ascii=False) + '\n')
                output.flush()
                os.fsync(output.fileno())
        for record in imported:
            excluded.add(record['email'].rsplit('@', 1)[-1])
        jobs.append({'profile_id': profile['id'], 'profile_name': profile['name'],
                     'count': len(imported), 'notice': notice})
        records.extend(imported)
    if not requested:
        notice = '当日新增候选额度已用完，未采集或发送。'
    elif not profiles:
        notice = '没有加入每日自动开发的完整业务，未采集或发送。'
    else:
        notice = f'共新增 {len(records)} 家候选，按各自业务导入草稿。'
    report = {'at': datetime.now().astimezone().isoformat(), 'mode': 'free_public_website',
              'notice': notice, 'requested': requested, 'daily_revision': policy['revision'],
              'new_company_limit': policy['new_company_limit'],
              'profiles': profiles, 'businesses': jobs, 'records': records, 'sent_by_collector': 0}
    private_store.prepare()
    (finder.DATA / 'daily-candidates.json').write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                                    encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--count', type=int, choices=range(1, BATCH_LIMIT + 1),
                        metavar='1–30', help='本批最多新增数量；省略时按已保存的当日剩余额度，最多 30 家')
    parser.add_argument('--profile', help='仅采集指定业务编号；默认轮流采集每日已启用的业务')
    args = parser.parse_args()
    print(json.dumps(collect(args.count, args.profile), ensure_ascii=True))


if __name__ == '__main__':
    main()
