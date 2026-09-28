"""User-facing art directions shared by import, planning and rendering."""
from copy import deepcopy

STYLES = {
    'brand_launch': {'id': 'brand_launch', 'name': '品牌发布',
        'description': '深色视觉页穿插浅色信息页，橙色强调，适合产品发布与品牌提案',
        'swatches': ['0D0D0D', 'FF6900', 'F5F5F5'],
        'direction': '参考用户提供的Mi Duo HTML的视觉规律，不借用其产品与文字。深色摄影封面，4–6个深色章节主张页，浅色功能信息页，橙色收尾。章节页是section布局，每页最多一个信息项，标题14字以内。章节先定主张再展开；同模块一致、跨模块换原型。数据页不贴图片。最多3张图片：cover封面独用，scene1/scene2各对应一个清晰场景，可在同场景复用。以大标题、留白、细分隔线建立层次，不做整套同样分栏。1280画布正文22px，信息标签26px，标题44px，主视觉80px。',
        'theme': {'background':'F5F5F5','text':'1A1A1A','accent':'FF6900','muted':'646464',
                  'typography':{'body':22,'label':26,'title':44,'section':16,'caption':14}}},
    'auto': {'id': 'auto', 'name': '智能匹配', 'description': '根据文稿主题选择配色与构图',
             'swatches': ['F4F3EE', '31453B', 'CBD6B6'], 'direction': '根据文稿和行业选择合适的视觉方向。'},
    'natural': {'id': 'natural', 'name': '自然品牌', 'description': '暖白与植物绿，适合消费品牌、生活方式',
                'swatches': ['F6F3E9', '143C30', 'C8D45A'],
                'direction': '自然品牌提案：暖白留白、植物绿、少量明亮强调；场景叙事和大字主张，避免每页重复卡片。',
                'theme': {'background': 'F6F3E9', 'text': '143C30', 'accent': 'C8D45A', 'muted': '536359'}},
    'business': {'id': 'business', 'name': '理性商务', 'description': '冷白与深蓝，适合策略汇报、数据提案',
                 'swatches': ['F4F7FC', '172F50', 'B6CDF5'],
                 'direction': '理性商务提案：冷白、深蓝、浅蓝强调；规整网格、清晰比较、信息屋与数据图表，克制装饰。',
                 'theme': {'background': 'F4F7FC', 'text': '172F50', 'accent': 'B6CDF5', 'muted': '4E6178'}},
    'editorial': {'id': 'editorial', 'name': '暖调杂志', 'description': '奶油色与陶土棕，适合创意传播、品牌故事',
                  'swatches': ['FAF5EC', '442D28', 'EEC69A'],
                  'direction': '暖调杂志提案：奶油底色、陶土棕、杏色强调；大胆标题、不对称留白、横向叙事，避免过多盒状卡片。',
                  'theme': {'background': 'FAF5EC', 'text': '442D28', 'accent': 'EEC69A', 'muted': '786151'}},
}


def get_style(style_id='auto'):
    if style_id not in STYLES:
        raise ValueError('请选择有效的 PPT 风格')
    return deepcopy(STYLES[style_id])


def apply_style(theme, style_id):
    style = get_style(style_id)
    return {**theme, **style.get('theme', {}), 'style_id': style_id,
            **({'rationale': style['direction']} if style_id != 'auto' else {})}
