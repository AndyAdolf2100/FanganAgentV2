"""A deterministic, roomier variant for plain title-and-body templates."""
import copy

VARIANT = 'expanded-body-v1'


def expanded_body(template, page):
    if page['role'] != 'body':
        return None
    titles = []; bodies = []
    for i, element in enumerate(page['elements']):
        editable = not element.get('fixed') and element.get('binding') != 'fixed'
        if editable and element.get('kind') == 'text':
            if element.get('textRole') == 'title':
                titles.append(i)
            elif element.get('textRole') in {'body', 'itemBody'}:
                bodies.append(i)
            else:
                return None
            if element.get('rotation') or element.get('matrix', [1, 0, 0, 1]) != [1, 0, 0, 1]:
                return None
        # Only a full-canvas background can sit beneath the expanded text area.
        elif not (element.get('kind') == 'image' and element['x'] <= 0 and element['y'] <= 0
                  and element['width'] >= template['width'] and element['height'] >= template['height']):
            return None
    if len(titles) != 1 or len(bodies) != 1:
        return None
    result = copy.deepcopy(page)
    w, h = template['width'], template['height']
    for index, y, height in ((titles[0], .07, .12), (bodies[0], .22, .66)):
        element = result['elements'][index]
        element.update(x=round(w*.06, 2), y=round(h*y, 2), width=round(w*.88, 2), height=round(h*height, 2))
        element['textLayout'] = {'wrap': True, 'vertical': 'top', 'padding': [6, 12, 6, 12], 'overflow': False}
        if index == bodies[0]:
            element.update(fill='#FFFFFF', fillOpacity=.94)
        for paragraph in element.get('paragraphs', []):
            paragraph['align'] = 'left'
    return result


def resolve_layout(template, page, variant=None):
    if variant is None:
        return page
    expanded = expanded_body(template, page) if variant == VARIANT else None
    if expanded is None:
        raise ValueError('此模板不支持扩大正文区域')
    return expanded
