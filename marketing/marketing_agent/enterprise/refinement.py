"""Prepare an auditable optimization run from an existing project-owned deck."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from . import template_html as html


def load_refinement_base(folder):
    """Load the immutable full optimization input, never the current prefix.

    Older optimization jobs can backfill this snapshot only from the recorded
    source job and exact original bytes. A changed source or local snapshot is
    rejected rather than silently used as a different optimization input.
    """
    folder = Path(folder)
    try:
        metadata = json.loads((folder / 'refinement-input.json').read_text())
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError('优化任务缺少可验证的原始输入记录') from exc
    if not isinstance(metadata, dict):
        raise ValueError('优化任务原始输入记录格式无效')
    source_id, expected = metadata.get('source_job_id'), metadata.get('source_plan_sha256')
    if not isinstance(source_id, str) or not re.fullmatch(r'[a-fA-F0-9]{32}', source_id):
        raise ValueError('优化来源任务ID须为32位十六进制')
    if not isinstance(expected, str) or not re.fullmatch(r'[a-fA-F0-9]{64}', expected):
        raise ValueError('优化来源计划SHA256无效')
    target = folder / 'refinement-base-plan.json'
    existing = target.exists()
    path = target if existing else folder.parent / source_id / 'plan.json'
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError('优化原始完整计划不可用，不能以当前已完成前缀代替') from exc
    if hashlib.sha256(raw).hexdigest() != expected.lower():
        raise ValueError('优化原始计划SHA256不匹配，拒绝使用被修改的来源或快照')
    try:
        plan = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError('优化原始完整计划不是有效JSON') from exc
    if not isinstance(plan, dict) or not isinstance(plan.get('pages'), list) or not plan['pages'] or any(not isinstance(page, dict) for page in plan['pages']):
        raise ValueError('优化原始完整计划缺少有效页面')
    if not existing:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile('wb', dir=folder, prefix='.refinement-base-', delete=False) as file:
                temporary = Path(file.name)
                file.write(raw)
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return plan


def source_snapshot(folder,plan):
    path=folder/'source-plan.json'
    if path.exists():return json.loads(path.read_text())
    metadata=json.loads((folder/'enterprise-design-contract.json').read_text())['metadata']
    summaries=[]
    for path in folder.glob('enterprise_brief-*.json'):
        try:
            summary=html.parse_json(json.loads(path.read_text())['choices'][0]['message']['content'])
            if summary.get('metadata')==metadata and isinstance(summary.get('agenda'),list):summaries.append(summary)
        except (ValueError,KeyError,TypeError):continue
    unique={json.dumps(s,sort_keys=True,ensure_ascii=False):s for s in summaries}
    if len(unique)!=1:raise ValueError('原任务缺少唯一且可验证的章节与元信息快照，不能直接优化')
    summary=next(iter(unique.values()));groups={}
    excluded={'html','layout','enterprise_charts','model_html','template_contract','design_status','generation_group','body_template_revision'}
    for p in plan['pages']:
        proposal={k:deepcopy(v) for k,v in p.items() if k not in excluded}
        if p.get('body_template_revision'):proposal['template_page']=p['body_template_revision']['original_template_page']
        gi=p['generation_group']
        if gi in groups and groups[gi]!=proposal:raise ValueError('原任务组内来源计划不一致')
        groups[gi]=proposal
    if list(groups)!=list(range(len(groups))):raise ValueError('原任务页面组不连续')
    return {**summary,'blocks':deepcopy(plan['source_blocks']),'planned':list(groups.values())}


def merge_feedback(reviews,feedback):
    """Keep reviewer observations distinct; never treat them as model verdicts."""
    result=deepcopy(reviews);by_page={p['page']:p for p in result}
    for entry in feedback:
        page=by_page.get(entry['page'])
        if page is None:raise ValueError('独立审查引用了不存在的页码')
        issues=entry.get('issues',[])
        if not isinstance(issues,list) or any(x.get('severity') not in {'low','medium','high'} or not isinstance(x.get('detail'),str) for x in issues):
            raise ValueError('独立审查问题格式无效')
        page['reviewer_observations']=deepcopy(issues)
        page['issues'].extend({**x,'origin':'independent_reviewer'} for x in issues)
        if any(x['severity'] in {'medium','high'} for x in issues):page['verdict']='fix'
    return result
