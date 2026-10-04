"""Synchronous collector for the existing daily automation; never sends mail."""
import argparse
import csv
import io
import json
from datetime import datetime
from pathlib import Path
import time
import bedsetco
import finder
import business_profiles as businesses
from free_finder import find

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--count',type=int,default=5,choices=range(1,6))
    parser.add_argument('--profile',help='仅采集指定业务编号；默认轮流采集每日已启用的业务')
    args=parser.parse_args()
    finder.settings()  # Preserve the old private search settings on first migration.
    profiles=[businesses.require_ready(businesses.get(args.profile))] if args.profile else businesses.daily_profiles()
    profiles=profiles[:args.count]
    excluded=bedsetco.history_blocks()
    with bedsetco.db() as c:
        excluded.update(r[0] for r in c.execute('SELECT domain FROM blocks'))
        excluded.update(r[0] for r in c.execute('SELECT domain FROM leads'))
    records=[]
    jobs=[]
    deadline=time.monotonic()+600
    for index,profile in enumerate(profiles):
        remaining=args.count-len(records)
        quota=max(1,(remaining+len(profiles)-index-1)//(len(profiles)-index))
        config=businesses.search_config(profile)
        config['budget_seconds']=max(1,deadline-time.monotonic())/(len(profiles)-index)
        if time.monotonic()>=deadline:
            jobs.append({'profile_id':profile['id'],'profile_name':profile['name'],'notice':'本轮采集时间预算已用完'})
            break
        found=find(quota,config,exclude_domains=excluded)
        for record in found:
            businesses.tag(record,profile)
            record['discovered_at']=datetime.now().astimezone().isoformat()
            excluded.add(record['email'].split('@')[-1])
        text=io.StringIO()
        writer=csv.DictWriter(text,fieldnames=['email','company','name'])
        writer.writeheader()
        for record in found:writer.writerow({'email':record['email'],'company':record['company'],'name':''})
        notice=bedsetco.import_csv(text.getvalue(),profile=profile) if found else '未找到可用公开邮箱，未导入或发送'
        jobs.append({'profile_id':profile['id'],'profile_name':profile['name'],'count':len(found),'notice':notice})
        records.extend(found)
    with bedsetco.db() as c:
        for record in records:
            row=c.execute('SELECT id,status,profile_id FROM leads WHERE email=?',(record['email'],)).fetchone()
            if row and row['profile_id']==record['profile_id']:
                record.update(lead_id=row['id'],queue_status=row['status'])
    finder.DATA.mkdir(exist_ok=True)
    with (finder.DATA/'found.jsonl').open('a',encoding='utf-8') as out:
        for record in records:out.write(json.dumps(record,ensure_ascii=False)+'\n')
    report={'at':datetime.now().astimezone().isoformat(),'mode':'free_public_website',
            'notice':f'共找到 {len(records)} 家候选，按各自业务导入草稿。' if profiles else '没有加入每日自动开发的完整业务，未采集或发送。',
            'profiles':profiles,'businesses':jobs,'records':records,'sent_by_collector':0}
    (finder.DATA/'daily-candidates.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True))

if __name__=='__main__':main()
