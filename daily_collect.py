"""Synchronous collector for the existing daily automation; never sends mail."""
import argparse
import csv
import io
import json
from datetime import datetime
from pathlib import Path
import bedsetco
import finder
from free_finder import find

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--count',type=int,default=5,choices=range(1,6))
    args=parser.parse_args()
    excluded=bedsetco.history_blocks()
    with bedsetco.db() as c:
        excluded.update(r[0] for r in c.execute('SELECT domain FROM blocks'))
        excluded.update(r[0] for r in c.execute('SELECT domain FROM leads'))
    records=find(args.count,finder.settings(),exclude_domains=excluded)
    for record in records:
        record['discovered_at']=datetime.now().astimezone().isoformat()
    text=io.StringIO()
    writer=csv.DictWriter(text,fieldnames=['email','company','name'])
    writer.writeheader()
    for record in records:writer.writerow({'email':record['email'],'company':record['company'],'name':''})
    notice=bedsetco.import_csv(text.getvalue())
    with bedsetco.db() as c:
        for record in records:
            row=c.execute('SELECT id,status FROM leads WHERE email=?',(record['email'],)).fetchone()
            if row:record.update(lead_id=row['id'],queue_status=row['status'])
    finder.DATA.mkdir(exist_ok=True)
    with (finder.DATA/'found.jsonl').open('a',encoding='utf-8') as out:
        for record in records:out.write(json.dumps(record,ensure_ascii=False)+'\n')
    report={'at':datetime.now().astimezone().isoformat(),'mode':'free_public_website',
            'notice':notice,'records':records,'sent_by_collector':0}
    (finder.DATA/'daily-candidates.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True))

if __name__=='__main__':main()
