from copy import deepcopy

import pytest

from marketing_agent.enterprise.repair_policy import build_repair_policy, classify_error


def observation(version='v1', kind='text_clipping', detail='文本框右侧空间不足，完整标签被裁切'):
    return {'slide_id': 'stable-slide', 'slide_version': version, 'html_sha256': version,
            'phase': 'candidate', 'verdict': 'fix',
            'issues': [{'severity': 'medium', 'type': kind, 'detail': detail, 'issue_id': version + '-issue',
                        'origin_issue_id': 'first-issue', 'issue_key': 'stable-key', 'text_anchors': ['标签'],
                        'uncertainty': .1}], 'rechecks': []}


@pytest.mark.parametrize(('issue', 'category'), [
    ({'type': 'tool_error', 'detail': '完整HTML回答不是合法JSON：Expecting delimiter'}, 'protocol'),
    ({'type': 'tool_error', 'detail': '原文或目录内容不完整：原文包含“模板宽度不足”'}, 'source_integrity'),
    ({'type': 'template_geometry_changed', 'detail': '标题实际宽度偏离批准合同'}, 'protected_region'),
    ({'type': 'visual_unverified', 'detail': 'Expecting property name enclosed in double quotes'}, 'review_evidence'),
    ({'type': 'visual_unverified', 'detail': '视觉诊断未逐项复验全部原问题'}, 'review_evidence'),
    ({'type': 'tool_error', 'detail': '固定元素校验失败：正文标签裁切'}, 'protected_region'),
    ({'type': 'missing_image', 'detail': '图片没有解码'}, 'asset'),
    ({'type': 'tool_error', 'detail': '局部修复越界：非目标原文被改写'}, 'protocol'),
    ({'type': 'tool_error', 'detail': '局部修复范围不一致：标记遗漏'}, 'protocol'),
    ({'type': 'tool_error', 'detail': '局部修复须保持已有分页；容量不足须先升级正文布局策略'}, 'protocol'),
])
def test_current_technical_error_cannot_escalate_from_old_visual_history(issue, category):
    row = {'origin': 'tool', 'issues': [{'severity': 'high', **issue}]}
    original = deepcopy(row)
    policy = build_repair_policy({'role': 'body'}, [row], attempt=3,
                                 history=[{'findings': [observation('v1')]}, {'findings': [observation('v2')]}])
    assert policy['category'] == category
    assert not policy['allow_template_switch'] and not policy['allow_contract_reanalysis']
    assert not policy['allow_repagination'] and policy['preserve_page_count']
    assert row == original


def test_repeated_current_capacity_can_escalate_on_second_repair_and_preserves_evidence():
    first, second = observation(), observation('v2')
    before = deepcopy([first, second])
    initial = build_repair_policy({'role': 'body'}, [first], attempt=1)
    assert initial['scope'] == 'local' and not initial['allow_template_switch']
    policy = build_repair_policy({'role': 'body'}, [second], attempt=2, history=[{'findings': [first]}])
    assert policy['scope'] == 'body_layout' and policy['allow_template_switch']
    assert policy['allow_contract_reanalysis'] and policy['allow_repagination']
    assert policy['capacity_evidence'][0]['evidence'] == second['issues'][0]['detail']
    assert policy['current_issue_refs'][0]['text_anchors'] == ['标签']
    assert policy['repeated_issue_refs'][0]['issue_id'] == 'v2-issue'
    assert [first, second] == before
    last = build_repair_policy({'role': 'body'}, [observation('v3')], attempt=3,
                               history=[{'findings': [first]}, {'findings': [second]}])
    assert last['scope'] == 'body_template' and last['allow_template_switch']


@pytest.mark.parametrize(('kind', 'detail'), [
    ('awkward_wrapping', '目录短尾换行，文字宽度不足但均完整可读'),
    ('text_contrast', '正文颜色接近实际背景，应调整颜色'),
    ('readability', '局部标签视觉不协调'),
    ('text_clipping', '文字似乎缺了一点，无法判断是否被裁切'),
])
def test_style_or_ambiguous_symptom_is_not_capacity(kind, detail):
    policy = build_repair_policy({'role': 'body'}, [observation('v2', kind, detail)], attempt=3,
                                 history=[{'findings': [observation('v1', kind, detail)]}])
    assert policy['scope'] == 'local' and not policy['allow_template_switch']


def test_cache_replay_unrelated_issue_and_uncertain_evidence_never_count_as_repeat():
    prior = observation()
    same = build_repair_policy({'role': 'body'}, [prior], attempt=3, history=[{'findings': [deepcopy(prior)]}])
    assert same['scope'] == 'local'
    unrelated = observation('v2')
    unrelated['issues'][0].update(issue_key='another', origin_issue_id='another', text_anchors=['其他标签'])
    assert build_repair_policy({'role': 'body'}, [unrelated], attempt=3,
        history=[{'findings': [prior]}])['scope'] == 'local'
    uncertain = observation('v2')
    uncertain['issues'][0]['uncertainty'] = .75
    policy = build_repair_policy({'role': 'body'}, [uncertain], attempt=3,
        history=[{'findings': [prior]}])
    # Uncertain current evidence is re-verified, never escalated as a repeat.
    assert policy['scope'] == 'review_only' and not policy['allow_template_switch']
    assert not policy['repeated_issue_refs']


def test_model_returning_unchanged_html_is_a_failed_attempt_not_a_bare_cache_replay():
    row = observation()
    policy = build_repair_policy({'role': 'body'}, [row], attempt=2, history=[
        {'findings': [deepcopy(row)], 'generated': False},
        {'findings': [deepcopy(row)], 'generated': True}])
    assert policy['scope'] == 'body_layout' and policy['allow_template_switch']


def test_recheck_must_supply_current_capacity_evidence_not_copy_old_condition():
    prior = observation()
    current = observation('v2')
    current['issues'] = []
    current['rechecks'] = [{'status': 'persists', 'original_issue': deepcopy(prior['issues'][0]),
                            'evidence': '仍有视觉问题，但本图无法确认空间是否不足'}]
    # Merely discussing uncertainty is not a positive capacity observation.
    assert build_repair_policy({'role': 'body'}, [current], attempt=2,
        history=[{'findings': [prior]}])['scope'] == 'local'
    current['rechecks'][0]['evidence'] = '当前文本框空间不足，右端最后一字仍被裁切'
    assert build_repair_policy({'role': 'body'}, [current], attempt=2,
        history=[{'findings': [prior]}])['scope'] == 'body_layout'


@pytest.mark.parametrize('role', ['cover', 'section', 'ending'])
def test_fixed_pages_never_escalate_to_body_template(role):
    policy = build_repair_policy({'role': role}, [observation('v2')], attempt=3,
                                 history=[{'findings': [observation()]}])
    assert policy['scope'] == 'local' and not policy['allow_repagination']


def test_contents_capacity_can_repaginate_without_body_template_switch():
    policy = build_repair_policy({'role': 'contents'}, [observation('v2')], attempt=2,
                                 history=[{'findings': [observation()]}])
    assert policy['allow_repagination'] and not policy['allow_template_switch']
    assert not policy['allow_contract_reanalysis']


def test_unknown_error_remains_conservative():
    assert classify_error('unexpected implementation exception') == 'unknown'


def test_location_refs_extract_observed_ids_without_mutating_diagnosis():
    row = observation()
    issue = row['issues'][0]
    issue.update(source_candidates=[{'source_id': 'source-7', 'rect': {'x': 2}}],
                 element_candidates=[{'element_id': 'title', 'rect': {'x': 3}}])
    before = deepcopy(row)
    ref = build_repair_policy({'role': 'body'}, [row])['current_issue_refs'][0]
    assert ref['source_candidates'] == ['source-7'] and ref['element_candidates'] == ['title']
    assert row == before
    browser = {'origin': 'browser_probe', 'issues': [{'severity': 'high', 'type': 'out_of_slot',
                'source': 'source-8', 'element': 2, 'text': '明确测量到的文字'}]}
    ref = build_repair_policy({'role': 'body'}, [browser])['current_issue_refs'][0]
    assert ref['source_candidates'] == ['source-8'] and ref['element_candidates'] == ['2']
    assert ref['text_anchors'] == ['明确测量到的文字']


def test_repeated_browser_overflow_matches_normalized_source_and_text_locations():
    from marketing_agent.enterprise.pipeline import browser_rows
    def rows(version, source='source-8', text='完整标签'):
        return browser_rows({'pages':[{'page':1,'slide_id':'stable-slide','html_sha256':version,
            'screenshot_sha':'screenshot-'+version,'issues':[{'type':'out_of_slot','source':source,
            'text':text,'bounds':{'x':90,'y':10,'w':50,'h':20},'frame':{'x':0,'y':0,'w':100,'h':50}}]}]})
    prior,current=rows('v1'),rows('v2')
    before=deepcopy([prior,current])
    policy=build_repair_policy({'role':'body'},current,attempt=2,history=[{'findings':prior,'generated':True}])
    assert policy['scope']=='body_layout' and policy['allow_template_switch']
    assert policy['capacity_evidence'][0]['kind']=='measured_overflow'
    assert current[0]['html_sha256']=='v2' and current[0]['screenshot_sha']=='screenshot-v2'
    assert [prior,current]==before
    unrelated=build_repair_policy({'role':'body'},rows('v3','source-9','另一标签'),attempt=3,history=[{'findings':prior}])
    assert unrelated['scope']=='local'


def test_seed_candidate_geometry_changes_do_not_break_same_source_identity():
    prior,current=observation('v1'),observation('v2')
    for row in (prior,current):
        row['issues'][0].update(issue_key='',issue_id='',origin_issue_id='',text_anchors=[],
            source_candidates=[{'source_id':'b7','rect':{'x':10 if row is prior else 20}}])
    assert build_repair_policy({'role':'body'},[current],attempt=2,history=[{'findings':[prior]}])['scope']=='body_layout'


def test_uncertain_only_rechecks_review_html_but_mixed_confirmed_defect_is_repaired():
    prior=observation()
    row=observation('v2');row['issues']=[]
    row['rechecks']=[{'status':'uncertain','original_issue':prior['issues'][0],'evidence':'无法确认边缘是否被裁切'}]
    policy=build_repair_policy({'role':'body'},[row],attempt=2)
    assert policy['scope']=='review_only' and policy['preserve_page_count']
    row['issues']=[deepcopy(prior['issues'][0])]
    assert build_repair_policy({'role':'body'},[row],attempt=2)['scope']=='local'
    row['issues'][0]['uncertainty']=1
    assert build_repair_policy({'role':'body'},[row],attempt=2)['scope']=='review_only'
