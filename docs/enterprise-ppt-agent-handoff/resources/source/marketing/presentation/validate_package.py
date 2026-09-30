"""Normalize PptxGenJS orphan master declarations and validate OPC references."""
import json
import io
import posixpath
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


def validate_chart_data(parts, plan):
    ns={'c':'http://schemas.openxmlformats.org/drawingml/2006/chart','x':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    charts=sorted((n for n in parts if re.fullmatch(r'ppt/charts/chart\d+\.xml',n)),key=lambda n:int(re.search(r'(\d+)\.xml',n)[1]))
    expected=[p['chart'] for p in plan['pages'] if p['layout']=='chart']
    if len(charts)!=len(expected):raise ValueError('Native chart count differs from plan')
    for name,chart in zip(charts,expected):
        root=ET.fromstring(parts[name]);series=root.findall('.//c:ser',ns)
        if len(series)!=len(chart['series']):raise ValueError('Native chart series count differs')
        for actual,wanted in zip(series,chart['series']):
            labels=[x.text or '' for x in actual.findall('./c:cat//c:pt/c:v',ns)]
            values=[float(x.text) for x in actual.findall('./c:val/c:numRef/c:numCache/c:pt/c:v',ns)]
            title=actual.findtext('./c:tx/c:strRef/c:strCache/c:pt/c:v',namespaces=ns)
            if labels!=chart['categories'] or values!=wanted['values'] or title!=wanted['name']:
                raise ValueError('Native chart data differs from source-bound plan: '+name)
        relname=posixpath.join(posixpath.dirname(name),'_rels',posixpath.basename(name)+'.rels')
        rels=ET.fromstring(parts[relname])
        workbooks=[posixpath.normpath(posixpath.join(posixpath.dirname(name),r.attrib['Target'])) for r in rels if r.attrib['Target'].endswith('.xlsx')]
        if len(workbooks)!=1:raise ValueError('Chart requires an embedded editable workbook')
        with zipfile.ZipFile(io.BytesIO(parts[workbooks[0]])) as book:
            strings=[]
            if 'xl/sharedStrings.xml' in book.namelist():
                strings=[''.join(si.itertext()) for si in ET.fromstring(book.read('xl/sharedStrings.xml'))]
            sheet=ET.fromstring(book.read('xl/worksheets/sheet1.xml'));cells={}
            for cell in sheet.findall('.//x:sheetData/x:row/x:c',ns):
                value=cell.findtext('x:v',namespaces=ns)
                if cell.get('t')=='s':value=strings[int(value)]
                elif cell.get('t')=='inlineStr':value=''.join(cell.find('x:is',ns).itertext())
                elif value is not None:value=float(value)
                cells[cell.get('r')]=value
            for i,label in enumerate(chart['categories'],2):
                if cells.get(f'A{i}')!=label:raise ValueError('Workbook category mismatch')
            for j,series in enumerate(chart['series'],1):
                column=chr(ord('A')+j)
                if cells.get(column+'1')!=series['name']:raise ValueError('Workbook series name mismatch')
                if [cells.get(f'{column}{i}') for i in range(2,len(chart['categories'])+2)]!=series['values']:
                    raise ValueError('Workbook numbers mismatch')
    return len(charts)


def validate_package(filename, plan=None):
    filename = Path(filename)
    with zipfile.ZipFile(filename) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    ns = 'http://schemas.openxmlformats.org/package/2006/content-types'
    ET.register_namespace('', ns)
    root = ET.fromstring(parts['[Content_Types].xml'])
    removed = []
    for entry in list(root):
        name = entry.get('PartName', '').lstrip('/')
        if name and name not in parts:
            # Only this known exporter defect may be repaired automatically.
            if re.fullmatch(r'ppt/slideMasters/slideMaster\d+\.xml', name):
                root.remove(entry)
                removed.append(name)
            else:
                raise ValueError(f'Missing content-type part: {name}')
    relationships = 0
    for name, content in parts.items():
        if name.endswith('.xml') or name.endswith('.rels'):
            element = ET.fromstring(content)
            if name.endswith('.rels'):
                base = posixpath.dirname(posixpath.dirname(name))
                for rel in element:
                    if rel.get('TargetMode') == 'External':
                        continue
                    target = rel.attrib['Target'].split('#')[0]
                    resolved = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(base, target))
                    if resolved not in parts:
                        raise ValueError(f'Missing relationship: {name} -> {resolved}')
                    relationships += 1
    if removed:
        parts['[Content_Types].xml'] = ET.tostring(root, encoding='utf-8', xml_declaration=True)
        temporary = filename.with_suffix('.validated.tmp')
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, content in parts.items():
                archive.writestr(name, content)
        temporary.replace(filename)
    chart_count=validate_chart_data(parts,plan) if plan else None
    return {'passed': True, 'native_charts_checked':chart_count,'normalized_orphan_masters': len(removed), 'internal_relationships': relationships,
            'slides': sum(bool(re.fullmatch(r'ppt/slides/slide\d+\.xml', name)) for name in parts)}


if __name__ == '__main__':
    print(json.dumps(validate_package(sys.argv[1],json.loads(Path(sys.argv[2]).read_text()) if len(sys.argv)>2 else None)))
