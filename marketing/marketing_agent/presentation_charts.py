"""Source-bound chart data. Models select cells; the host reads numeric values."""
import math
import re

CHART_TYPES = {'bar', 'column', 'line', 'donut'}


def scalar_cell(value):
    text = re.sub(r'\s+|\*\*|,', '', str(value))
    match = re.fullmatch(r'([+-]?\d+(?:\.\d+)?)(万元|亿元|万次|万人|万|亿|元|%|人|位|次|条|场|单)?', text)
    if not match:
        raise ValueError('图表仅接受明确单值，不能把区间、约数或空值变为单点：'+text[:60])
    number = float(match[1])
    if not math.isfinite(number):
        raise ValueError('图表数值必须有限')
    return number, match[2] or ''


def same_unit(cell_unit, chart_unit):
    # A bare cell inherits its table/header's declared chart unit. No rescaling.
    aliases = {'万':'万元', '亿':'亿元'}
    return not cell_unit or aliases.get(cell_unit, cell_unit) == aliases.get(chart_unit, chart_unit)


def validate_chart(chart, blocks, allowed_ids):
    if not isinstance(chart, dict) or chart.get('type') not in CHART_TYPES:
        raise ValueError('未知图表类型')
    categories, series = chart.get('categories'), chart.get('series')
    if not isinstance(categories, list) or not 2 <= len(categories) <= 8:
        raise ValueError('图表需要2–8个类别')
    if any(not isinstance(label, str) or not label or len(label)>14 for label in categories):
        raise ValueError('图表类别名称须为1–14字')
    if len(set(categories)) != len(categories):
        raise ValueError('图表类别不能重复')
    if not isinstance(series, list) or not 1 <= len(series) <= 3:
        raise ValueError('图表需要1–3个同单位序列')
    if chart['type']=='donut' and (len(series)!=1 or len(categories)>6):
        raise ValueError('环形图仅支持一个序列、至多6个类别')
    if chart['type'] in {'bar','column'} and (len(categories)>6 or len(series)>2):
        raise ValueError('条/柱图最多6类、2序列；更多数据应拆页')
    if chart['type']=='bar' and len(series)>1 and len(categories)>4:
        raise ValueError('双序列横条图最多4类，避免标签挤压')
    unit=chart.get('unit','')
    if not isinstance(unit,str) or not unit or len(unit)>8:
        raise ValueError('图表必须标明同一单位')
    normalized=[]
    for sequence in series:
        if not isinstance(sequence,dict):raise ValueError('图表序列必须是对象')
        name=sequence.get('name','')
        refs=sequence.get('source_refs',[])
        if not isinstance(name,str) or not name or len(name)>20 or not isinstance(refs,list) or len(refs)!=len(categories):
            raise ValueError('序列必须有名称，且每个数据点须绑定来源单元格')
        values=[]
        for point_index,ref in enumerate(refs):
            if not isinstance(ref,dict):raise ValueError('数据来源须为单元格引用对象')
            block_id=ref.get('block_id')
            if block_id not in allowed_ids or block_id not in blocks:
                raise ValueError('图表数据来源不在本页引用中')
            block=blocks[block_id]
            row,column=ref.get('row'),ref.get('column')
            if block.get('kind')!='table' or type(row) is not int or type(column) is not int or row<0 or column<0:
                raise ValueError('图表数据须引用原稿表格的有效行列')
            try:value,cell_unit=scalar_cell(block['rows'][row][column])
            except IndexError:raise ValueError('图表引用单元格越界') from None
            category=re.sub(r'\s+|\*\*','',block['rows'][row][0])
            if category!=re.sub(r'\s+','',categories[point_index]):
                raise ValueError('图表类别必须对应来源表格首列；不得错配行或改写类别')
            if not same_unit(cell_unit,unit):raise ValueError('图表单位混用或隐式换算')
            if not cell_unit and value!=0 and unit not in block.get('header',[])[column]:
                raise ValueError('无单位的非零单元格必须在列标题明确单位')
            values.append(value)
        if 'values' in sequence and sequence['values']!=values:
            raise ValueError('图表数值与来源单元格不一致')
        normalized.append({'name':name,'values':values,'source_refs':refs})
    if chart['type']=='donut' and (any(v<0 for v in normalized[0]['values']) or sum(normalized[0]['values'])<=0):
        raise ValueError('环形图数据须非负且合计大于零')
    if chart['type']=='line' and chart.get('ordered') is not True:
        raise ValueError('折线图必须声明类别具有真实时间或顺序关系')
    note=chart.get('source_note','原稿表格数据；口径与假设见本页备注')
    if not isinstance(note,str) or len(note)>80:raise ValueError('图表来源说明过长')
    return {'type':chart['type'],'categories':categories,'series':normalized,'unit':unit,
            'ordered':chart.get('ordered',False),'source_note':note}
