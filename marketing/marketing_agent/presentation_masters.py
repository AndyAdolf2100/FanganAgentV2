"""Semantic masters that adapt to the real item count instead of exact demo keys."""
import hashlib


def adaptive_master(page):
    name=page.get('composition')
    items=page.get('items',[])
    if name not in {'audience_bands','platform_bands','time_grid','message_house','hero_statement'} or not 2<=len(items)<=4:
        return None
    def ref(key,tag,cls):
        value=page.get(key) if not key.startswith('item_') else items[int(key.split('_')[1])].get(key.split('_')[2])
        return f"<{tag} class='{cls}' data-ref='{key}'></{tag}>" if value else ''
    header=ref('section','div','eyebrow')+ref('title','h1','headline')+ref('subtitle','p','subhead')
    cards=[]
    for i,item in enumerate(items):
        cards.append("<article class='entry'>"+ref(f'item_{i}_value','div','value')+ref(f'item_{i}_label','h2','label')+ref(f'item_{i}_text','p','detail')+'</article>')
    kind='bands' if name in {'audience_bands','platform_bands'} else 'timeline' if name=='time_grid' else 'hero' if name=='hero_statement' else 'house'
    body=f"<main class='semantic {kind} count-{len(items)}'><div class='page-header'>{header}</div><section class='entries'>{''.join(cards)}</section></main>"
    css='''
    .semantic{position:relative;width:1280px;height:650px;padding:42px 64px;color:var(--ink);background:var(--bg)}
    .semantic .eyebrow{font-size:17px;line-height:25px;color:var(--muted);margin-bottom:21px}
    .semantic .headline{font-size:46px;line-height:1.28;letter-spacing:-1px;font-weight:700}
    .semantic .subhead{font-size:28px;line-height:1.4;color:var(--muted);margin-top:14px}
    .semantic .entries{position:absolute;left:64px;right:64px;top:256px;bottom:32px}
    .semantic .label{font-size:30px;line-height:1.35;font-weight:700}
    .semantic .detail{font-size:28px;line-height:1.45}
    .semantic .value{font-size:36px;line-height:1.2;font-weight:700;color:var(--ink)}
    .bands .entries{display:flex;flex-direction:column;gap:8px;top:248px;bottom:24px}
    .bands .entry{flex:1;display:flex;align-items:center;gap:24px;border-top:1px solid var(--rule);padding:4px 18px;min-height:0}
    .bands .entry:first-child{background:var(--tint);border-top:3px solid var(--ink)}
    .bands .value{flex:0 0 140px;font-size:36px}
    .bands .label{flex:0 0 255px}
    .bands .detail{flex:1;min-width:0}
    .timeline .entries,.house .entries{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:28px}
    .timeline.count-4 .entries,.house.count-4 .entries{grid-template-columns:repeat(4,minmax(0,1fr))}
    .timeline.count-2 .entries,.house.count-2 .entries{grid-template-columns:repeat(2,minmax(0,1fr))}
    .timeline .entry{border-top:7px solid var(--accent);padding:24px 0 0}
    .timeline .value{font-size:48px;margin-bottom:24px}
    .timeline .label{margin-bottom:22px}
    .house .entries{border-top:28px solid var(--ink);padding-top:20px}
    .house .entry{padding:0 20px;border-left:4px solid var(--accent)}
    .house .label{margin-bottom:22px}
    .house .value{margin-bottom:16px}
    .semantic.hero{height:720px;background:var(--ink);color:var(--bg)}
    .hero .headline{font-size:64px;line-height:1.22;max-width:1100px;text-wrap:balance}
    .hero .eyebrow,.hero .subhead{color:var(--bg)}
    .hero .entries{top:400px;bottom:98px;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:40px}
    .hero.count-2 .entries{grid-template-columns:repeat(2,minmax(0,1fr))}
    .hero.count-4 .entries{grid-template-columns:repeat(4,minmax(0,1fr))}
    .hero .entry{border-top:3px solid var(--accent);padding-top:20px}
    .hero .label{margin-bottom:16px}
    .hero .value{color:var(--bg);margin-bottom:12px}
    .semantic.hero + .custom-footer{background:var(--ink);color:var(--bg);border-color:var(--muted)}
    '''
    return {'body':body,'css':css,'master':'adaptive-'+name,'master_sha256':hashlib.sha256((body+css).encode()).hexdigest()}
