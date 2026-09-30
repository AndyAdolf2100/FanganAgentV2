"""Bound repair scope from current errors; retain history as evidence, not orders.

This policy never approves a page or changes geometry. GLM still emits complete
HTML, and source, protected-region and visual gates remain authoritative.
"""
from copy import deepcopy
import re


PROTOCOL_TYPES = {'json_error', 'invalid_json', 'invalid_schema', 'wrong_canvas', 'protocol_error'}
SOURCE_TYPES = {'source_validation_failed', 'source_integrity', 'source_missing', 'source_mismatch'}
PROTECTED_TYPES = {'template_geometry_changed', 'protected_style_changed', 'body_outside_contract',
                   'source_outside_body', 'out_of_template_text_frame', 'protected_region'}
REVIEW_TYPES = {'visual_unverified', 'visual_incomplete', 'review_evidence', 'candidate_regression'}
ASSET_TYPES = {'missing_image', 'asset_usage_failed', 'asset_unavailable', 'asset_quality', 'asset_missing'}


def classify_error(issue, *, origin=None):
    """Classify existing machine errors without interpreting quoted manuscript."""
    if isinstance(issue, str):
        issue = {'type': 'tool_error', 'detail': issue}
    kind = str(issue.get('type', ''))
    if kind in REVIEW_TYPES:
        return 'review_evidence' if kind != 'candidate_regression' else 'local_visual'
    if kind in PROTECTED_TYPES:
        return 'protected_region'
    if kind in SOURCE_TYPES:
        return 'source_integrity'
    if kind in PROTOCOL_TYPES:
        return 'protocol'
    if kind in ASSET_TYPES or kind.startswith('asset_'):
        return 'asset'
    # ValueErrors predate typed errors. Match their leading validation message,
    # never a word buried in a quoted source block or historical diagnosis.
    if kind in {'tool_error', 'error', ''} and origin not in {'browser_probe', 'deck_critic'}:
        reason = str(issue.get('detail', '')).strip()
        if re.match(r'(原文或目录内容不完整|原文块|表格 .+单元格与原文不一致|目录条目必须|block_ids|agenda_ids|来源标记|缺少本页已规划|章节序号|目录序号|每个目录条目)', reason):
            return 'source_integrity'
        if re.match(r'(固定元素校验失败|缺失或重复模板元素|模型改动了模板非文字|模型更改了文字区域|引用了未知品牌|必须保护用户|固定页|title_frame|正文自由区|正文合同|正文须保留顶部)', reason):
            return 'protected_region'
        if re.match(r'(完整HTML回答|模型输出须为JSON|模型必须|完整HTML缺失|返回完整单页HTML|生成组没有|页面数量无效|本次修复必须返回|正文必须有且仅有|当前节点只选择模板|模板选择须|局部修复越界|局部修复范围不一致|局部修复须保持已有分页|Expecting |Unterminated string|Invalid \\escape|Extra data)', reason):
            return 'protocol'
        if re.match(r'(视觉诊断|视觉未启用|视觉预算|图像分析|Seed)', reason):
            return 'review_evidence'
        if re.match(r'(完整HTML含未提供|模型引用了不存在的模板资源|页面只能引用模板图片|CSS含未知资源)', reason):
            return 'asset'
        return 'unknown'
    return 'local_visual'


def _active(rows):
    for row in rows or []:
        for issue in row.get('issues', []):
            if issue.get('severity') in {'medium', 'high'}:
                yield row, issue, False
        for check in row.get('rechecks', []):
            if check.get('status') == 'persists' and isinstance(check.get('original_issue'), dict):
                issue = deepcopy(check['original_issue'])
                issue['current_recheck_evidence'] = check.get('evidence', '')
                yield row, issue, True


def _ref(row, issue):
    def identifiers(key, identifier, direct):
        values = [item.get(identifier) if isinstance(item, dict) else item for item in issue.get(key, [])]
        if issue.get(direct) is not None:
            values.append(issue[direct])
        return list(dict.fromkeys(str(value) for value in values if isinstance(value, (str, int))))
    anchors = deepcopy(issue.get('text_anchors', []))
    if not anchors and isinstance(issue.get('text'), str) and issue['text'].strip():
        anchors = [issue['text']]
    return {'issue_id': issue.get('issue_id'), 'issue_key': issue.get('issue_key'),
            'type': issue.get('type'), 'slide_id': row.get('slide_id') or issue.get('slide_id'),
            'source_candidates': identifiers('source_candidates', 'source_id', 'source'),
            'element_candidates': identifiers('element_candidates', 'element_id', 'element'),
            'text_anchors': anchors,
            'region_hint': issue.get('region_hint', '')}


def _same_issue(left, right):
    ids = lambda i: {i[k] for k in ('issue_id', 'issue_key', 'origin_issue_id', 'prior_issue_id') if i.get(k)}
    if ids(left) & ids(right):
        return True
    # Browser findings use text/source/element, while Seed findings use anchor
    # arrays and candidate objects whose measured rectangles can change during
    # repair. Compare stable locations, not those changing geometry payloads.
    left, right = _ref({}, left), _ref({}, right)
    # An issue type alone is not a location. Avoid treating unrelated labels or
    # a later page's different overflow as failed repair of the first issue.
    return bool(left.get('type') == right.get('type') and any(
        left.get(key) and left.get(key) == right.get(key)
        for key in ('text_anchors', 'source_candidates', 'element_candidates')))


def _version(row):
    return row.get('html_sha256') or row.get('slide_version') or row.get('screenshot_sha')


def _capacity(row, issue, recheck):
    """Use explicit current structural evidence, not bad-looking text alone."""
    if issue.get('uncertainty', 0) >= .5:
        return None
    evidence = str(issue.get('current_recheck_evidence') if recheck else issue.get('detail', ''))
    kind = str(issue.get('type', ''))
    # Short tails, contrast and font/style drift are not evidence that an entire
    # body layout lacks capacity. No escalation based on a style score.
    if kind in {'awkward_wrapping', 'contrast', 'text_contrast', 'low_contrast', 'color', 'header_typography_consistency',
                'typography', 'style_drift', 'font_size'}:
        return None
    if re.search(r'(无法(确认|判断|确定)|不能(确认|确定|定位)|不确定|疑似|可能|uncertain|cannot confirm)', evidence, re.I):
        return None
    structural = re.search(r'(空间不足|宽度不足|高度不足|容器过窄|可用(空间|宽度|高度).{0,12}(不足|小于|不够)|承载区域.{0,20}(不足|裁切|裁剪)|'
                           r'放不下|容不下|内容过密|密集.{0,12}(重叠|遮挡)|内[边间]距不足|'
                           r'(容器|承载|载体|组件|文本框).{0,32}(裁切|裁剪|截断|切断)|'
                           r'insufficient (space|width|height)|container too (narrow|small)|overcrowd)', evidence, re.I)
    if structural:
        return {'kind': 'current_visual_capacity', 'evidence': evidence}
    # Browser bounds can establish overflow in this exact cited region. They
    # do not establish a filled SVG safe area or authorize fixed-frame changes.
    if row.get('origin') == 'browser_probe' and kind in {'out_of_slot', 'out_of_canvas'}:
        if isinstance(issue.get('bounds'), dict) and isinstance(issue.get('frame'), dict):
            return {'kind': 'measured_overflow', 'bounds': deepcopy(issue['bounds']), 'frame': deepcopy(issue['frame'])}
    return None


def build_repair_policy(proposal, findings, *, history=(), attempt=1, current=None):
    """Return a conservative adapter contract. Only current findings set scope."""
    active = list(_active(findings))
    categories = {classify_error(issue, origin=row.get('origin')) for row, issue, _ in active}
    uncertain_checks = [(row, check['original_issue']) for row in findings or []
                        for check in row.get('rechecks', []) if check.get('status') == 'uncertain'
                        and isinstance(check.get('original_issue'), dict)]
    # Uncertain evidence alone cannot authorize rewriting the candidate. A
    # coexisting confirmed defect still needs repair, so do not let an uncertain
    # recheck override a current source/protocol error or a concrete visual issue.
    uncertain_only = (not active and bool(uncertain_checks)) or (bool(active) and all(
        classify_error(issue, origin=row.get('origin')) == 'local_visual'
        and issue.get('uncertainty', 0) >= .5 and not recheck for row, issue, recheck in active))
    if uncertain_only:categories = {'review_evidence'}
    # Current technical/evidence failures take precedence over coexisting or
    # historic visual defects; their repair must not choose another template.
    priority = ('protocol', 'source_integrity', 'protected_region', 'review_evidence', 'asset', 'unknown')
    category = next((c for c in priority if c in categories), 'local_visual' if active else 'unknown')
    refs = [_ref(row, issue) for row, issue, _ in active]
    if not active:refs = [_ref(row, issue) for row, issue in uncertain_checks]
    capacity, repeated = [], []
    previous = [item for entry in history for item in _active(entry.get('findings', []))]
    # A model can return identical HTML. That is a failed local attempt when a
    # generation event sits between two observations, even if Seed uses cache.
    # Reading the same saved result twice without generation proves nothing.
    last_generated = bool(history and history[-1].get('generated'))
    before_last = [item for entry in list(history)[:-1] for item in _active(entry.get('findings', []))]
    for row, issue, recheck in active:
        evidence = _capacity(row, issue, recheck)
        if evidence:
            capacity.append({'issue_ref': _ref(row, issue), **evidence})
        def matches(old_row, old_issue):
            return (not row.get('slide_id') or not old_row.get('slide_id') or row['slide_id'] == old_row['slide_id']) and _same_issue(issue, old_issue)
        changed = any(_version(old_row) and _version(old_row) != _version(row)
                      and matches(old_row, old_issue) for old_row, old_issue, _ in previous)
        ineffective = last_generated and any(_version(old_row) == _version(row) and matches(old_row, old_issue)
                                            for old_row, old_issue, _ in before_last)
        if evidence and _version(row) and (changed or ineffective):
            repeated.append(_ref(row, issue))
    body = proposal.get('role') == 'body'
    flow = proposal.get('role') in {'body', 'contents', 'preface'}
    if category == 'local_visual' and flow and repeated and attempt >= 2:
        category = 'body_template' if attempt >= 3 else 'body_layout'
    scope = {'protocol': 'protocol_only', 'source_integrity': 'source_only', 'protected_region': 'restore_protected',
             'review_evidence': 'review_only', 'asset': 'asset_only', 'local_visual': 'local'}.get(category, category)
    escalate = category in {'body_layout', 'body_template'}
    instructions = {
        'protocol': '仅纠正当前JSON/HTML输出协议，保持当前模板、合同、原文与已有排版目标；旧视觉反馈仍待验收，不据此重选模板。',
        'source_integrity': '仅恢复来源原文、顺序、标记与完整性，保持当前模板、合同和非目标排版；逐字对照原始来源。',
        'protected_region': '仅恢复当前批准合同中的固定元素、文字外框和权限；不得换模板、重析合同或解锁品牌来绕过错误。',
        'review_evidence': '当前缺少有效视觉证据；保持同一候选HTML，重新进行有界Seed单图复验，不调用GLM改稿。',
        'asset': '仅纠正已验收素材的引用或使用问题，不因资源错误重选模板或生成未经批准的新素材。',
        'local_visual': '先局部修复当前明确视觉问题，保持当前模板、合同、分页和无关内容；纯短尾、颜色或字号差异不等于结构容量不足。',
        'body_layout': '同问题在修后当前版本仍有容量或结构实证；改换正文布局策略，可由GLM选择同套正文模板、重析合同或续页，保留全部来源和品牌。',
        'body_template': '已到最后一轮，当前重复问题仍有容量或结构实证；避免重复无效局部调整，由GLM及时选择更合适的同套正文布局/模板或续页。',
        'unknown': '错误类型尚不明确，依据原始错误做最小纠正；保持当前模板、合同和分页，不从旧视觉历史推断重选模板。'}
    return {'version': 1, 'category': category, 'scope': scope, 'attempt': attempt,
            'allow_template_switch': escalate and body, 'allow_contract_reanalysis': escalate and body,
            'allow_repagination': escalate, 'preserve_page_count': not escalate,
            'current_issue_refs': refs, 'capacity_evidence': capacity, 'repeated_issue_refs': repeated,
            'instruction': instructions[category]}
