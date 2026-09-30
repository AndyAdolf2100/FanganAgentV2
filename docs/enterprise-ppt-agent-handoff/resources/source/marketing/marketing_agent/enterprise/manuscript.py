"""Lossless manuscript blocks adapted from the enterprise template project."""
import re


def plain_text(value):
    """Convert presentation markup, retaining literal numbers and expressions."""
    value = re.sub(r'(?m)^[ \t]*(?:>[ \t]*)+', '', value)
    value = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', r'\1（\2）', value)
    value = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1（\2）', value)
    value = re.sub(r'(`+)([^`]+)\1', r'\2', value)
    for marker in ('**','__','~~','*','_'):
        escaped = re.escape(marker)
        boundaries = ('','') if marker in {'**','~~'} else (r'(?<!\w)',r'(?!\w)')
        value = re.sub(boundaries[0]+escaped+r'(\S(?:.*?\S)?)'+escaped+boundaries[1], r'\1', value)
    value = re.sub(r'(?m)^\s*[-*+]\s+\[([ xX])\]\s*', lambda m:'☑ ' if m[1].lower()=='x' else '☐ ', value)
    value = re.sub(r'(?m)^[ \t]*[-*+][ \t]+', '• ', value)
    value = re.sub(r'(?m)^[ \t]*#{1,6}[ \t]+', '', value)
    value = re.sub(r'(?m)^\s*\d+\.[ \t]+', lambda m:m[0].strip().rstrip('.')+'、',value)
    return value

def parse_markdown(source):
    """Keep fenced code/tables as blocks and split long prose at sentence boundaries."""
    source = source.replace('\r\n','\n').replace('\r','\n')
    chunks = re.split(r'(```[^\n]*\n[\s\S]*?(?:\n```|\Z)|~~~[^\n]*\n[\s\S]*?(?:\n~~~|\Z))',source)
    blocks=[]; chapter='引言'
    for chunk in chunks:
        fenced=chunk.startswith(('```','~~~'))
        for raw in ([chunk] if fenced else re.split(r'\n\s*\n',chunk)):
            if not raw.strip():continue
            # Headings remain separate even when authors omit blank lines.
            pieces=[raw] if fenced else re.split(r'(?m)(?=^#{1,6}\s+)',raw)
            for piece in pieces:
                if not piece.strip() or re.fullmatch(r'(?:\s*[-*_]){3,}\s*',piece):continue
                lines=piece.strip().splitlines()
                heading=re.match(r'^(#{1,6})\s+(.+)$',lines[0]) if not fenced else None
                groups=[]
                if heading:
                    level=len(heading[1]); label=heading[2].strip()
                    if level==2:chapter=label
                    groups.append(('heading',label,level))
                    if len(lines)>1:groups.append(('paragraph','\n'.join(lines[1:]),0))
                else:
                    table=len(lines)>1 and any(re.match(r'^\s*\|?\s*:?-{3,}',l) for l in lines[1:])
                    groups.append(('code' if fenced else 'table' if table else 'list' if re.match(r'^\s*(?:[-*+] |\d+[.)] )',lines[0]) else 'paragraph',piece.strip(),0))
                for kind,value,level in groups:
                    # Strip presentation markup only; original Markdown stays in the manifest.
                    plain=re.sub(r'^(```|~~~)[^\n]*\n|\n(?:```|~~~)\s*$', '', value) if kind=='code' else plain_text(value)
                    plain=plain.strip()
                    while plain:
                        end=min(900,len(plain))
                        if end<len(plain):
                            boundary=max(plain.rfind(c,450,end) for c in '\n。；！？')
                            if boundary>=450:end=boundary+1
                        blocks.append({'id':f'b{len(blocks)+1:04}','kind':kind,'chapter':chapter,'level':level,'text':plain[:end]})
                        plain=plain[end:]
    if not blocks:raise ValueError('Markdown文稿为空')
    if len(blocks)>3000:raise ValueError('文稿内容块过多，请拆分文稿后生成')
    return blocks

def batches(blocks):
    result=[];current=[];length=0
    for block in blocks:
        if current and (length+len(block['text'])>5500 or (block['level']==2 and length>1500)):
            result.append(current);current=[];length=0
        current.append(block);length+=len(block['text'])
    if current:result.append(current)
    return result


def preface_block_ids(blocks):
    """Explicit 序言 heading scope, ending at a same/higher-level heading.

    Prose mentions, template labels and unmarked opening paragraphs do not
    trigger a preface. Nested headings remain part of the marked section.
    """
    result=[];current=[];level=None
    def finish():
        if any(b['kind']!='heading' for b in current):
            result.extend(b['id'] for b in current)
    for block in blocks:
        if block['kind']=='heading':
            if level is not None and block['level']<=level:
                finish();current=[];level=None
            title=re.sub(r'\s+', '', block['text'])
            if level is None and re.fullmatch(r'(?:(?:[0-9零〇一二三四五六七八九十]+)[、.．:：])?序言[:：]?',title):
                level=block['level']
        if level is not None:current.append(block)
    finish()
    return result
