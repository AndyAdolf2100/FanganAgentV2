"""One persistent spending ceiling across the project's image and vision tools."""
import fcntl
import json
import os
import time
from decimal import Decimal


def can_reserve(cache,price,limit):
    paths={cache.parent/'image-cache/budget.json',cache.parent/'vision-cache/budget.json',cache/'budget.json'}
    ledgers={p:json.loads(p.read_text()) if p.exists() else {'reservations':[]} for p in paths}
    spent=sum((Decimal(x['reserved_rmb']) for d in ledgers.values() for x in d['reservations']),Decimal(0))
    cap=Decimal(os.getenv('MARKETING_PPT_BUDGET_RMB','5'))
    return spent+Decimal(str(price))<=cap and len(ledgers[cache/'budget.json']['reservations'])<limit


def reserve(cache, price, limit, digest=None, category_cap=None):
    cache.mkdir(parents=True,exist_ok=True)
    root=cache.parent
    cap=Decimal(os.getenv('MARKETING_PPT_BUDGET_RMB','5'))
    price=Decimal(str(price))
    if price<=0 or cap<=0:raise ValueError('项目图片/视觉预算未配置')
    # Same lock for both tools; existing ledgers are counted, never reset.
    with (root/'tool-budget.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        paths={root/'image-cache/budget.json',root/'vision-cache/budget.json',cache/'budget.json'}
        ledgers={p:json.loads(p.read_text()) if p.exists() else {'reservations':[]} for p in paths}
        total=sum((Decimal(x['reserved_rmb']) for d in ledgers.values() for x in d['reservations']),Decimal(0))
        data=ledgers[cache/'budget.json']
        spent=sum((Decimal(x['reserved_rmb']) for x in data['reservations']),Decimal(0))
        if total+price>cap:raise ValueError('项目图片与视觉累计预算已用完；保留草稿和待处理项')
        if len(data['reservations'])>=limit or (category_cap is not None and spent+price>category_cap):
            raise ValueError('工具预算或请求次数已用完')
        entry={'reserved_rmb':str(price),'time':time.time(),'state':'reserved_not_confirmed_charge'}
        if digest:entry['request_sha256']=digest
        data['reservations'].append(entry)
        path=cache/'budget.json';tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,indent=2));tmp.replace(path)
