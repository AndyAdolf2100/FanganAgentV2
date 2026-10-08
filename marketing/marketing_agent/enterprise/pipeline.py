"""Version 5: bounded group repair, independent audits and frozen delivery."""
from copy import deepcopy
from functools import partial
import hashlib
import json
import os
import subprocess
from threading import Event

from .revisions import RevisionStore, atomic_json, digest
from .repair_policy import build_repair_policy, classify_error
from .review_scheduler import ReviewBatch, run_review_workflows


ERRORS = (ValueError, KeyError, TypeError, OSError, RuntimeError, subprocess.SubprocessError)
MAX_GROUP_ATTEMPTS = 4  # Initial attempt plus three corrections.
MAX_DECK_REPAIR_ROUNDS = 1
MAX_CHAT_BATCHES = 3
LAYOUT_REVIEW_POLICY = (
    '结合layout_measurements的浏览器事实和Seed观察逐项复核：同用途且页眉兼容的顶部标题应有一致的字号、字重、文字起点和基线，'
    '比较titles的真实文字bounds及typography，不能只比较标题容器，也不要求封面/章节/正文强行一致。'
    '短标签无必要换行须检查实际行数、nowrap_width_px/available_width_px、white_space及br；不能从截图臆断CSS成因。'
    '正文内容图形及组合组件（例如圆环、箭头）优先保留原轮廓比例，作为一组等比调整并保持可读；'
    '区分图内文字与外置关联标签，外置标签不必塞入形状。SVG/CSS外接矩形、viewport或geometry_bounds不是真实填充安全区；'
    '缺少内部测量时结合截图证据判断，不能虚构安全坐标。图内文字须避开轮廓、镂空及裁剪边。'
    '结合body实际边界、左右留白及视觉权重判断整体平衡，不以间距不等自动判错。'
    '检查组件文字的字形完整及其实际所在填充背景的对比；DOM未越框不等于浅色字未落到白底，'
    '所有文字必须完整清晰；文字互相覆盖、裁切、被图形挡住或与实际背景低对比均须修复，不能以DOM过关代替可读性。'
    '复杂背景下无法确认字形或对比时保持待复核。文字与图形的间距按实际轮廓、图内padding、图外gap及同组/跨组层级，'
    '结合字号与模板尺度判断；不能一律将外置标签塞进形状，也不能任意把图内标签移到外部。所有问题须引用具体页和可复验事实。'
)
LAYOUT_REPAIR_GOAL = (
    '依据实测证据解决问题，保留完整来源，由GLM决定版式、区域重析或续页。'
    '同类顶部标题先对照accepted_typography_references，维持字号、字重、起点与基线；'
    '先检查可用宽度、内边距、white-space、无必要br及SVG/文字子容器，不用缩字掩盖窄框。'
    '正文非人工品牌标题如原外框过窄，先reanalyze_contract提出受限title_frame：'
    '按原模板对齐锚点受限扩宽（左/中/右），保留y/height、基线与品牌，避让固定前景；通过合同验证后方可使用，未经批准保持原框。'
    '正文内容图形和组合组件优先保留原轮廓比例并成组等比缩放，容不下可换用同套正文模板、重排或分页；'
    '区分图内文字与外置关联标签，后者不强塞进形状。SVG/CSS外接矩形不是真实填充安全区；'
    '复验图内文字实际区域、外置标签关联、字形完整、对比及左右视觉平衡。'
    '所有文字必须完整清晰，修复文字覆盖、裁切、图形遮挡和实际背景低对比，不得靠缩字号或近色隐藏内容解决。'
    '按实际轮廓、图内padding、图外gap和同组/跨组层级设置合适间距，结合字号与模板尺度，不强改标签内外归属；'
    'DOM通过不能代替截图可读性，复杂背景下仍不确定则保持待复核。'
    '不得解锁brand/fixed背景、横幅、logo或固定页；正文自由区不合理时重析合同，不能直接越界。'
)


def actionable(rows):
    return any(row.get('verdict') == 'fix'
               or any(issue.get('severity') in {'medium', 'high'} for issue in row.get('issues', []))
               or any(check.get('status') != 'resolved' for check in row.get('rechecks', [])) for row in rows)


def browser_rows(probe):
    from .diagnostics import browser_evidence
    return [{'page': page['page'], 'slide_id': page.get('slide_id'), 'verdict': 'fix',
             **{key: page[key] for key in ('html_sha256', 'slide_version', 'screenshot_sha') if page.get(key) is not None},
             'observed': '浏览器实测', 'origin': 'browser_probe',
             'probe_evidence': browser_evidence(page, screenshot_sha=page.get('screenshot_sha'),
                                                html_sha256=page.get('html_sha256')),
             'issues': [{**issue, 'severity': 'high', 'detail': json.dumps(issue, ensure_ascii=False)}
                        for issue in page['issues']]}
            for page in probe.get('pages', []) if page.get('issues')]


def _page_observation(page, row):
    """Forward measured facts for GLM decisions; do not infer layout defects."""
    from .diagnostics import compact_review
    row = compact_review(row)
    contract = page.get('template_contract') or {}
    return {'slide_id': page['slide_id'], 'slide_version': page.get('slide_version'),
            'role': page['role'], 'title': page.get('title', ''), 'template_page': page.get('template_page'),
            'layout_contract': {key: deepcopy(contract[key]) for key in
                                ('body_frame', 'title_element', 'title_frame') if key in contract},
            'layout_measurements': deepcopy(row.get('probe_evidence', {}).get('layout_measurements', {})),
            'observed': row.get('observed'), 'aesthetics': row.get('aesthetics'),
            'issues': row.get('issues', []), 'rechecks': row.get('rechecks', [])}


def _model_feedback(value):
    """Compact historical diagnostics only at the GLM request boundary.

    Checks, hash-bound records and the repair loop keep their full evidence.
    Manuscript, HTML, contracts and issue/recheck text are not token-truncated.
    """
    from .diagnostics import compact_review
    if isinstance(value, list):
        return [_model_feedback(item) for item in value]
    if not isinstance(value, dict):
        return deepcopy(value)
    if isinstance(value.get('probe_evidence'), dict) or (
        'raw_answer' in value and value.get('phase') in {'initial', 'candidate', 'final'}):
        value = compact_review(value)
    protected = {'html', 'source_blocks', 'sources', 'template_contract', 'layout_contract',
                 'issues', 'rechecks', 'original_issue', 'raw_issue'}
    return {key: deepcopy(child) if key in protected else _model_feedback(child) for key, child in value.items()}


def _prior_issues(rows, slide_id):
    """Carry unresolved evidence even if a recheck omitted a new issue row."""
    found = {}
    for row in rows:
        if row.get('slide_id') != slide_id or row.get('origin') == 'deck_critic':
            continue
        for issue in row.get('issues', []):
            if issue.get('issue_id') and issue.get('recheck_scope') != 'deck':
                found[issue['issue_id']] = deepcopy(issue)
        for check in row.get('rechecks', []):
            original = check.get('original_issue', {})
            if check.get('status') != 'resolved' and original.get('issue_id'):
                found[original['issue_id']] = deepcopy(original)
    return list(found.values())


def _budget_error(reason):
    # Manuscript/source errors often quote a budget figure. That is not a tool
    # spending limit and must still reach normal layout/content correction.
    return any(word in reason for word in ('预算不足', '预算已用完', '预算未配置',
        '调用上限已到', '请求次数已用完', '额度不足', '额度已用完', '预算或请求次数已用完'))


def run(jobs, job_id, agent, plan, source, groups, generated, generate, check_deck,
        call, renderer, corrections, log, asset_manifest=None):
    from .diagnostics import (diagnose_page, classify_persistence, RUBRIC_VERSION, SCHEMA_VERSION,
                              vision_workers, review_input_sha256, review_evidence_sha256)

    folder = jobs.root / job_id
    # html_only is an export result, never an inherited generation constraint.
    plan.pop('html_only', None)
    store = RevisionStore(folder, plan)
    cache = folder.parent / 'vision-cache'
    enabled = os.getenv('MARKETING_VISION_ENABLED', 'false').lower() == 'true'
    report = {'schema_version': 5, 'status': 'reviewing', 'pages': [], 'repairs': [],
              'issues': [], 'history': [], 'audits': [], 'notice': '逐组生成、检查和优化'}
    budget_exhausted = Event()
    workers = vision_workers()
    group_reviews, diagnostic_history, seed_groups = {}, {}, {}
    chat = getattr(jobs, 'chat', None)
    chat_resume = bool(chat and jobs.get(job_id).get('chat_resume'))

    def retain_seed_issues(gi, rows):
        """Keep original issue identities; new observations may resolve them."""
        retained = seed_groups.setdefault(gi, [])
        known = {issue['issue_id'] for row in retained for issue in row['issues']}
        keys = {issue.get('issue_key') for row in retained for issue in row['issues'] if issue.get('issue_key')}
        for row in rows:
            if row.get('phase') not in {'initial', 'candidate', 'final'}:
                continue
            originals = [i for i in row.get('issues', []) if i.get('severity') in {'medium', 'high'}]
            originals.extend(c['original_issue'] for c in row.get('rechecks', []) if isinstance(c.get('original_issue'), dict))
            for original in originals:
                identifier = original.get('issue_id')
                if (not identifier or identifier.startswith('reviewer-') or original.get('origin') == 'independent_reviewer'
                    or identifier in known or original.get('prior_issue_id') in known
                    or original.get('origin_issue_id') in known or (original.get('issue_key') and original['issue_key'] in keys)):
                    continue
                issue = deepcopy(original)
                slide_id = issue.get('original_slide_id', issue.get('slide_id', row.get('slide_id')))
                if not slide_id:
                    continue
                for key in ('original_slide_id', 'original_page', 'review_scope_sha256', 'review_target', 'review_scope_instruction'):
                    issue.pop(key, None)
                issue['slide_id'] = slide_id
                retained.append({'slide_id': slide_id, 'original_page': original.get('original_page', row.get('page')),
                    'observed_revision': original.get('slide_version', original.get('observed_revision')),
                    'origin': 'seed_diagnostic', 'verdict': 'fix', 'issues': [issue]})
                known.add(identifier)
                if issue.get('issue_key'):
                    keys.add(issue['issue_key'])

    def committed_evidence(gi):
        pages = store.committed_group(gi)
        entry = store.state['groups'].get(str(gi))
        if not entry:
            return None
        record = json.loads((folder / entry['check']).read_text())
        if digest(record) != entry['check_sha256'] or record.get('revision') != entry['revision']:
            raise ValueError('已提交版本的检查证据已改变')
        try:
            notes = json.loads(record.get('reason') or '{}')
        except (ValueError, TypeError):
            notes = {}
        return {'pages': pages, 'reviews': record.get('reviews', []),
                'probe': record.get('probe', {}), 'notes': notes if isinstance(notes, dict) else {}, **deepcopy(entry)}

    for gi in range(len(groups)):
        committed = committed_evidence(gi)
        if committed:
            group_reviews[gi] = deepcopy(committed['reviews'])
            diagnostic_history[gi] = deepcopy(committed['reviews'])
            if isinstance(committed['notes'].get('seed_issue_registry'), list):
                seed_groups[gi] = deepcopy(committed['notes']['seed_issue_registry'])
            else:
                # Legacy plain passes did not close issue IDs explicitly. Replay
                # only checks of the actual current revision, never observations
                # of abandoned candidates or another user-edited HTML version.
                for entry in store.state['history']:
                    if entry.get('group') != gi or entry.get('revision') != committed['revision']:
                        continue
                    record = json.loads((folder / entry['check']).read_text())
                    if digest(record) != entry.get('check_sha256') or record.get('revision') != committed['revision']:
                        raise ValueError('历史视觉问题检查证据已改变，拒绝复用')
                    retain_seed_issues(gi, record.get('reviews', []))
            retain_seed_issues(gi, committed['reviews'])
    reviewer_groups, reviewer_unmapped, feedback_sha = {}, [], None
    feedback_path = folder / 'reviewer-feedback.json'
    if feedback_path.exists():
        feedback = json.loads(feedback_path.read_text())
        if not isinstance(feedback, list):
            raise ValueError('独立审查反馈必须为页面数组')
        for entry in feedback:
            if not isinstance(entry, dict) or type(entry.get('page')) is not int or not isinstance(entry.get('issues'), list):
                raise ValueError('独立审查反馈格式无效')
            if any(not isinstance(i, dict) or i.get('severity') not in {'low', 'medium', 'high'}
                   or not isinstance(i.get('detail'), str) for i in entry['issues']):
                raise ValueError('独立审查问题格式无效')
        mapping_path = folder / 'reviewer-feedback-mapping.json'
        feedback_sha = digest(feedback)
        original_pages = []
        if (folder / 'refinement-input.json').exists():
            from .refinement import load_refinement_base
            original_pages = load_refinement_base(folder)['pages']
        elif not mapping_path.exists():
            original_pages = deepcopy(plan.get('pages') or [page for group in generated for page in group] or store.pages())
        else:
            # A materialized plan may already have different continuations.
            # Only an original page with its recorded version can recover scope.
            original_pages = deepcopy([page for group in generated for page in group])
        positions, scopes = {}, {}
        for position, page in enumerate(original_pages, 1):
            gi = page.get('generation_group')
            if type(gi) is not int:
                continue
            part = len(scopes.setdefault(gi, [])) + 1
            slide_id = page.get('slide_id', f'group-{gi:04d}-part-{part:03d}')
            scopes[gi].append(slide_id)
            positions[position] = (gi, slide_id, page.get('slide_version', hashlib.sha256(page['html'].encode()).hexdigest()))
        if mapping_path.exists():
            mapping = json.loads(mapping_path.read_text())
            if mapping.get('feedback_sha256') != digest(feedback) or mapping.get('input_sha256') != digest(store.fingerprint):
                raise ValueError('独立审查反馈或原输入已改变，不能套用旧页码映射')
            reviewer_groups = {int(gi): deepcopy(rows) for gi, rows in mapping['groups'].items()}
            reviewer_unmapped = deepcopy(mapping['unmapped'])
        else:
            # Preserve the original page identities before any continuation
            # pages shift global positions. Restarts restore this mapping.
            for entry in feedback:
                gi, slide_id, observed_revision = positions.get(entry['page'], (None, None, None))
                if type(gi) is not int or not 0 <= gi < len(groups):
                    reviewer_unmapped.append({'type': 'reviewer_feedback_unmapped', 'original_feedback': deepcopy(entry)})
                    continue
                reviewer_groups.setdefault(gi, []).append({**deepcopy(entry), 'slide_id': slide_id,
                    'observed_revision': observed_revision, 'original_page': entry['page'],
                    'observed_group_slide_ids': deepcopy(scopes[gi]),
                    'verdict': 'fix', 'origin': 'independent_reviewer'})
        # Upgrade old persisted page mappings in place without interpreting old
        # page numbers against the current, potentially repaginated deck.
        for gi, rows in reviewer_groups.items():
            if not 0 <= gi < len(groups):
                raise ValueError('独立审查映射引用无效页面组')
            for row_index, row in enumerate(rows):
                if not row.get('slide_id') or not row.get('observed_revision'):
                    raise ValueError('独立审查映射缺少原页面版本')
                if 'observed_group_slide_ids' not in row:
                    original = positions.get(row.get('original_page', row.get('page')))
                    row['observed_group_slide_ids'] = (deepcopy(scopes.get(gi)) if original ==
                        (gi, row['slide_id'], row['observed_revision']) else None)
                for issue_index, issue in enumerate(row['issues']):
                    raw = deepcopy(issue.get('raw_issue', issue))
                    issue_id = 'reviewer-' + digest({'feedback': feedback_sha, 'group': gi,
                        'row': row_index, 'slide_id': row['slide_id'], 'observed_revision': row['observed_revision'],
                        'issue': issue_index, 'raw': raw})[:32]
                    row['issues'][issue_index] = {**raw, 'raw_issue': raw, 'issue_id': issue_id,
                        'issue_key': issue_id, 'origin_issue_id': issue_id, 'slide_id': row['slide_id'],
                        'observed_revision': row['observed_revision'], 'origin': 'independent_reviewer'}
        atomic_json(mapping_path, {'schema_version': 2, 'feedback_sha256': feedback_sha,
            'input_sha256': digest(store.fingerprint), 'groups': reviewer_groups, 'unmapped': reviewer_unmapped})
        report['reviewer_feedback'] = deepcopy(feedback)

    # Live conversation requests have stable identities independent of the
    # original reviewer-feedback file. Appending to that file would invalidate
    # its saved page mapping and every issue ID derived from its whole hash.
    chat_mapping_path = folder / 'chat-feedback-mapping.json'
    chat_mapping = {'schema_version': 1, 'input_sha256': digest(store.fingerprint), 'requests': {}}
    if chat_mapping_path.exists():
        chat_mapping = json.loads(chat_mapping_path.read_text())
        if (chat_mapping.get('schema_version') != 1
            or chat_mapping.get('input_sha256') != digest(store.fingerprint)
            or not isinstance(chat_mapping.get('requests'), dict)):
            raise ValueError('对话意见映射与当前来源版本不符，拒绝套用旧意见')

    def chat_rows(request):
        rows = request.get('rows')
        if not isinstance(rows, list) or not rows:
            raise ValueError('对话修改请求缺少页面意见')
        ids = set()
        for row in rows:
            gi = row.get('generation_group')
            if (type(gi) is not int or not 0 <= gi < len(groups)
                or not isinstance(row.get('slide_id'), str)
                or not row['slide_id'].startswith(f'group-{gi:04d}-part-')
                or not row.get('observed_revision')
                or not isinstance(row.get('observed_group_slide_ids'), list)
                or row['slide_id'] not in row['observed_group_slide_ids']
                or not isinstance(row.get('issues'), list) or not row['issues']):
                raise ValueError('对话意见缺少稳定页面组、页面身份或观察版本')
            for issue in row['issues']:
                issue_id = issue.get('issue_id')
                if (not isinstance(issue_id, str) or not issue_id.startswith('reviewer-chat-')
                    or issue_id in ids or issue.get('severity') not in {'medium', 'high'}
                    or not isinstance(issue.get('detail'), str) or not issue['detail'].strip()
                    or issue.get('slide_id') != row['slide_id']):
                    raise ValueError('对话意见问题身份或内容无效')
                ids.add(issue_id)
        return rows

    def import_chat_request(request):
        for row in chat_rows(request):
            rows = reviewer_groups.setdefault(row['generation_group'], [])
            if row not in rows:
                rows.append(deepcopy(row))

    for request_id, request in chat_mapping['requests'].items():
        if (not isinstance(request_id, str) or not request_id or not isinstance(request, dict)
            or request.get('status') not in {'applying', 'completed', 'needs_attention'}):
            raise ValueError('对话意见映射请求状态无效')
        import_chat_request(request)

    def save_chat_mapping():
        atomic_json(chat_mapping_path, chat_mapping)
        report['chat_requests'] = [
            {'id': key, 'status': request['status'], 'groups': sorted({row['generation_group'] for row in request['rows']})}
            for key, request in chat_mapping['requests'].items()]

    def chat_checkpoint():
        if chat is not None:
            # Called only by generator advancement on the workflow thread. It
            # may plan messages, but never mutates in-flight review obligations.
            chat.checkpoint(job_id, plan, source, call)

    def page_binding(page):
        return {'slide_id': page['slide_id'], 'slide_version': page['slide_version'],
                'html_sha256': hashlib.sha256(page['html'].encode()).hexdigest()}

    def current_review_protocol(row):
        return row.get('rubric_version') == RUBRIC_VERSION and row.get('schema_version') == SCHEMA_VERSION

    def reviewer_scope(gi, pages):
        return digest({'generation_group': gi, 'feedback_sha256': feedback_sha,
            'issues': [i['issue_id'] for row in reviewer_groups.get(gi, []) for i in row['issues']],
            'pages': [page_binding(page) for page in pages]})

    def review_targets(gi):
        return [*reviewer_groups.get(gi, []), *seed_groups.get(gi, [])]

    def reviewer_priors(gi, pages, page, scope=None, targets=None):
        """Project immutable source observations onto one current screenshot.

        Every current page receives every tracked issue in the group. Page count
        alone cannot detect source content moving between continuation pages.
        ``slide_id`` addresses the current single-image review; the separately
        retained original identity is never rewritten in the persisted mapping.
        """
        scope = scope or reviewer_scope(gi, pages)
        return [{**deepcopy(issue), 'original_slide_id': original['slide_id'],
            'original_page': original.get('original_page'), 'slide_id': page['slide_id'],
            'review_scope_sha256': scope, 'review_target': page_binding(page),
            'review_scope_instruction': '这是同一来源组的逐页复验。仅依据当前截图逐项判断本页是否仍有原问题，'
                '内容可能已迁到其他续页；不要用原页码定位，不推断其他页已解决。'
                'resolved只表示当前页对应内容已修好或本页明确不存在该问题，并说明可见依据；'
                '无法确认则uncertain。宿主收齐当前组每页的逐项证据后才判断整组问题解决。'}
            for original in (review_targets(gi) if targets is None else targets) for issue in original['issues']]

    def issue_status(gi, pages, reviews, targets):
        """Close an issue only with its complete current issue-by-page matrix."""
        current = {p['slide_id']: p for p in pages}
        scope = reviewer_scope(gi, pages)
        projected = {page['slide_id']: {i['issue_id']: i for i in reviewer_priors(gi, pages, page, scope, targets)} for page in pages}
        result = []
        for original in targets:
            for issue in original['issues']:
                matrix = []
                for page in pages:
                    matching = [r for r in reviews if r.get('slide_id') == page['slide_id']]
                    row = matching[0] if len(matching) == 1 else {}
                    bound = (current_review_protocol(row) and row.get('phase') == 'candidate'
                             and all(row.get(k) == v for k, v in page_binding(page).items()))
                    checks = [c for c in row.get('rechecks', []) if c.get('issue_id') == issue['issue_id']]
                    check = checks[0] if len(checks) == 1 else {}
                    evidence = check.get('evidence')
                    verified = (bound and isinstance(evidence, str) and evidence.strip()
                        and check.get('status') in {'resolved', 'persists', 'uncertain'}
                        and check.get('original_issue') == projected[page['slide_id']][issue['issue_id']])
                    matrix.append({**page_binding(page), 'screenshot_sha': row.get('screenshot_sha') if bound else None,
                        'status': check['status'] if verified else 'unverified', 'evidence': evidence if verified else ''})
                statuses = {item['status'] for item in matrix}
                status = next((value for value in ('persists', 'uncertain', 'unverified') if value in statuses),
                              'resolved' if matrix else 'unverified')
                original_page = current.get(original['slide_id'], {})
                result.append({'issue_id': issue['issue_id'], 'slide_id': original['slide_id'],
                    'original_page': original.get('original_page'), 'generation_group': gi,
                    'slide_version': original_page.get('slide_version'), 'status': status, 'detail': issue['detail'],
                    'review_scope_sha256': scope, 'page_rechecks': matrix})
        return result

    def reviewer_status(gi, pages, reviews):
        return issue_status(gi, pages, reviews, reviewer_groups.get(gi, []))

    def seed_status(gi, pages, reviews):
        return issue_status(gi, pages, reviews, seed_groups.get(gi, []))

    def feedback_application(gi, revision):
        return {'feedback_sha256': feedback_sha, 'revision': revision,
                'issue_ids': [i['issue_id'] for row in reviewer_groups.get(gi, []) for i in row['issues']]}

    def update_report():
        atomic_json(folder / 'visual-review.json', report)

    def materialize():
        current = store.materialize()
        plan.clear()
        plan.update(current)
        jobs.update(job_id, page_count=len(plan['pages']))

    def check_assets(pages, gi):
        if not asset_manifest:
            return []
        from .assets import assets_for_group, evaluate_required_usage, validate_asset_usage
        # This includes reused HTML: a new required accepted asset cannot be
        # bypassed just because the source/template document was already valid.
        assets_for_group(folder, asset_manifest, gi)
        for page in pages:
            validate_asset_usage(page['html'], asset_manifest, gi, page.get('template_contract'))
        usage = evaluate_required_usage([page['html'] for page in pages], asset_manifest, gi,
                                        pages[0].get('template_contract'))
        return [{'type': 'required_asset_unavailable', 'generation_group': gi, 'asset_id': name}
                for name in usage.get('unavailable_required_asset_ids', [])]

    def compare_candidate(gi, revision, candidate_plan, reviews, findings):
        before = committed_evidence(gi)
        if not before or before['status'] != 'accepted' or before['revision'] == revision:
            return None

        def observations(pages, rows):
            by_id = {row['slide_id']: row for row in rows}
            return [_page_observation(page, by_id.get(page['slide_id'], {})) for page in pages]

        answer = call('enterprise_candidate_compare',
            '你是企业演示稿版本比较者。只根据前后Seed截图观察、美观量表与实际待修问题决定是否用新稿替换已接受稿，'
            '没有图片，不能假装亲眼看过。返回JSON {"accept":true或false,"reason":"具体证据与权衡"}。'
            '存在明确可读性、主次、留白或内容强调退步时拒绝覆盖；没有明确退步且实际问题得到解决可接受。'
            '允许有理由的版式与页数变化，不要求每个维度的分数都增加，不以微小分差机械否决。'
            '企业品牌和来源已由工具检查，本步骤只做文字决策，不生成HTML。' + LAYOUT_REVIEW_POLICY,
            {'group': gi, 'accepted_revision': before['revision'], 'candidate_revision': revision,
             'repair_targets': _model_feedback(findings), 'before': observations(before['pages'], before['reviews']),
             'after': observations(candidate_plan['pages'], reviews)})
        if not isinstance(answer, dict) or type(answer.get('accept')) is not bool or not isinstance(answer.get('reason'), str) or not answer['reason'].strip():
            raise ValueError('GLM版本比较缺少明确accept和证据理由，保留已接受稿')
        return {'accepted_revision': before['revision'], 'candidate_revision': revision,
                'accept': answer['accept'], 'reason': answer['reason'], 'decision_owner': 'GLM'}

    def diagnose(candidate, candidate_plan, probe, phase, previous=(), context_id=None, reusable=()):
        rows, errors = [], []
        if not enabled:
            return rows, [{'type': 'vision_disabled', 'detail': '视觉未启用，不能认定视觉通过'}]
        measured = {p['page']: p for p in probe.get('pages', [])}
        scope_pages = {}
        if phase == 'candidate':
            for page in candidate_plan['pages']:
                scope_pages.setdefault(page['generation_group'], []).append(page)
        scopes = {gi: reviewer_scope(gi, pages) for gi, pages in scope_pages.items() if review_targets(gi)}
        def page_context(page):
            return (f'final:{page["slide_id"]}:{hashlib.sha256(page["html"].encode()).hexdigest()}'
                    if phase == 'final' and context_id is None else context_id)

        def input_sha(page, prior, index):
            return review_input_sha256(page, measured[index], phase=phase,
                                       prior_issues=prior, context_id=page_context(page))

        prepared = []
        for index, page in enumerate(candidate_plan['pages'], 1):
            prior = _prior_issues(previous, page['slide_id']) if phase == 'candidate' else None
            cached = None
            gi = page['generation_group']
            if phase == 'candidate' and gi in scopes:
                by_id = {i['issue_id']: i for i in prior}
                by_id.update({i['issue_id']: i for i in reviewer_priors(gi, scope_pages[gi], page, scopes[gi])})
                prior = list(by_id.values())
                # Resume can retain already verified cells of a partial group
                # without reserving another model call. Re-rendered screenshots,
                # all page versions, and exact projected issue scope must match.
                matching = [r for r in reusable if r.get('slide_id') == page['slide_id']]
                row = matching[0] if len(matching) == 1 else {}
                checks = row.get('rechecks', [])
                checks_by_id = {c.get('issue_id'): c for c in checks}
                if (current_review_protocol(row) and row.get('page') == index
                    and row.get('phase') == phase and row.get('verdict') == 'pass'
                    and all(row.get(k) == v for k, v in page_binding(page).items())
                    and row.get('screenshot_sha') and row['screenshot_sha'] == measured[index].get('screenshot_sha')
                    and row.get('review_input_sha256') == review_input_sha256(page, measured[index],
                        phase=phase, prior_issues=prior, context_id=row.get('context_id'))
                    and not actionable([row]) and len(checks_by_id) == len(checks)
                    and all(checks_by_id.get(i['issue_id'], {}).get('status') == 'resolved'
                        and isinstance(checks_by_id[i['issue_id']].get('evidence'), str)
                        and checks_by_id[i['issue_id']]['evidence'].strip()
                        and checks_by_id[i['issue_id']].get('original_issue') == i for i in prior)):
                    cached = deepcopy(row)
            elif phase == 'final' and reusable:
                # Delivery verifies evidence, not a second opinion on unchanged
                # pixels. Keep the actual observation phase/request and preserve
                # unresolved findings; reusing a fix never means accepting it.
                evidence_sha = review_evidence_sha256(page, measured[index])
                for row in reusable:
                    if (current_review_protocol(row) and row.get('phase') in {'initial', 'candidate', 'final'}
                        and row.get('review_scope') == 'full_page'
                        and row.get('review_evidence_sha256') == evidence_sha
                        and row.get('verdict') in {'pass', 'fix'} and isinstance(row.get('issues'), list)
                        and all(row.get(k) == v for k, v in page_binding(page).items())
                        and row.get('screenshot_sha') and row['screenshot_sha'] == measured[index].get('screenshot_sha')):
                        origin = row.get('review_reuse') or {'phase': row['phase'], 'page': row['page'],
                            'request_sha256': row.get('request_sha256'), 'source': 'previous_audit'}
                        cached = {**deepcopy(row), 'page': index, 'reused_final_review': True,
                                  'reused_visual_review': True, 'review_reuse': deepcopy(origin)}
                        break
            prepared.append((index, page, prior, cached))

        def review(index, page, prior):
            """One single-image request in a worker thread; evidence never crosses pages."""
            # Final contexts bind to page content, not the deck or the round:
            # an unchanged page resolves to the same request and reuses its one
            # independent observation across rounds and restarts, while a
            # repaired page naturally receives a fresh context.
            context = page_context(page)
            if budget_exhausted.is_set():
                return skipped(index)
            try:
                row = agent.call('final_enterprise_visual_review' if phase == 'final' else 'diagnose_enterprise_page',
                    diagnose_page, candidate, candidate_plan, index, probe=measured[index], phase=phase,
                    prior_issues=prior,
                    context_id=context, cache_root=cache)
                if (row.get('page') != index or row.get('slide_id') != page['slide_id']
                    or row.get('slide_version') != page['slide_version'] or row.get('phase') != phase
                    or row.get('screenshot_sha') != measured[index].get('screenshot_sha')
                    or row.get('html_sha256') != hashlib.sha256(page['html'].encode()).hexdigest()
                    or row.get('verdict') not in {'pass', 'fix'} or not isinstance(row.get('issues'), list)
                    or (context is not None and row.get('context_id') != context)):
                    raise ValueError('视觉诊断未绑定当前页面、版本、截图或独立审查上下文')
                if prior:
                    checks = row.get('rechecks', [])
                    if (not isinstance(checks, list) or len(checks) != len(prior)
                        or {c.get('issue_id') for c in checks} != {i['issue_id'] for i in prior}
                        or any(c.get('status') not in {'resolved', 'persists', 'uncertain'}
                               or not isinstance(c.get('evidence'), str) or not c['evidence'].strip() for c in checks)):
                        raise ValueError('视觉诊断未逐项复验全部原问题，普通pass不能关闭人工反馈')
                row['review_input_sha256'] = input_sha(page, prior, index)
                return 'row', row
            except ERRORS as exc:
                reason = str(exc)[:2000]
                if _budget_error(reason):
                    budget_exhausted.set()
                return 'error', {'type': 'visual_unverified', 'slide_id': page['slide_id'], 'detail': reason}

        # Suspend this group while single-image tasks share one bounded pool
        # with other groups. State changes and progress stay on the main thread.
        total, collected = len(prepared), {}
        for index, page, prior, cached in prepared:
            if cached is not None:
                collected[index] = ('row', cached)
        pending = [(index, page, prior) for index, page, prior, cached in prepared if cached is None]
        budget_gap = {'type': 'visual_budget_exhausted', 'detail': '视觉预算不足，本页尚未验证'}
        finished = len(collected)

        def progress(index, value):
            nonlocal finished
            finished += 1
            jobs.update(job_id, stage='visual_review', visual_progress={
                'phase': phase, 'current': finished, 'total': total, 'page': index,
                'group': candidate_plan['pages'][index - 1]['generation_group'] + 1,
                'workers': workers})

        def skipped(index):
            return 'error', {**budget_gap, 'slide_id': candidate_plan['pages'][index - 1]['slide_id']}

        if pending:
            completed = yield ReviewBatch(
                tasks={index: partial(review, index, page, prior) for index, page, prior in pending},
                on_result=progress, skipped_result=skipped)
            collected.update(completed)
        rows.extend(value for _, (kind, value) in sorted(collected.items()) if kind == 'row')
        errors.extend(value for _, (kind, value) in sorted(collected.items()) if kind == 'error')
        return rows, errors

    def process_group(gi, proposal, existing=None, extra=None, deck_round=0,
                      force_repair=False, chat_request_ids=()):
        proposal = deepcopy(proposal)
        current, findings = deepcopy(existing), deepcopy(extra or [])
        human = deepcopy(reviewer_groups.get(gi, []))
        committed_seed = deepcopy(seed_groups.get(gi, []))
        before = committed_evidence(gi)
        retain_seed_issues(gi, extra or [])
        if before and seed_groups.get(gi, []) != committed_seed:
            # Independent final findings describe this committed version. Keep
            # their obligations even if every proposed replacement is rejected.
            # Add a new immutable check; never alter old reviews or their hashes.
            current_pages = {p['slide_id']: p for p in before['pages']}
            seed_rows = [r for r in extra or [] if r.get('phase') in {'initial', 'candidate', 'final'}]
            if not all(r.get('slide_id') in current_pages and all(r.get(k) == value
                for k, value in page_binding(current_pages[r['slide_id']]).items()) for r in seed_rows):
                raise ValueError('新增视觉问题不属于当前提交版本，拒绝覆盖现稿问题状态')
            notes = {**deepcopy(before['notes']), 'seed_issue_registry': deepcopy(seed_groups[gi]),
                     'seed_issues': seed_status(gi, before['pages'], before['reviews'])}
            store.record(gi, before['revision'], before['probe'], before['reviews'], before['status'],
                         json.dumps(notes, ensure_ascii=False))
            if not store.commit(gi, before['revision'], before['status']):
                raise ValueError('当前版本的新增视觉问题未能保存，保留原稿待复核')
            committed_seed = deepcopy(seed_groups[gi])
        original_findings = [*deepcopy(findings), *[row for row in review_targets(gi) if row not in findings]]
        attempts = []
        application = (before or {}).get('notes', {}).get('reviewer_feedback_application')
        if not before or application != feedback_application(gi, before['revision']):
            application = None
        # A committed draft is already hard-checked. Resume with explicit Seed
        # rechecks, including legacy drafts whose application history is unknown.
        # Fresh original input has no committed version and still gets GLM repair.
        review_current = bool(before and review_targets(gi) and not deck_round and not force_repair)
        last_visual = deepcopy(extra or group_reviews.get(gi, []))
        observed = diagnostic_history.setdefault(gi, [])
        generated_revision = None
        for attempt in range(MAX_GROUP_ATTEMPTS):
            revision, probe, rows, generated_now = None, {}, [], False
            repair_policy = build_repair_policy(proposal, findings, history=attempts,
                                                attempt=attempt, current=current)
            jobs.update(job_id, stage='layout_repair' if existing or extra or attempt else 'page_generating',
                page_progress={'current': gi + 1, 'total': len(groups), 'page': gi + 1},
                repair_progress={'group': gi + 1, 'attempt': attempt, 'max_self_repairs': 3, 'deck_round': deck_round})
            try:
                if current and ((attempt == 0 and not force_repair and (not extra or review_current))
                    or repair_policy['category'] == 'review_evidence'):
                    replacement = current
                else:
                    strategies = [item for row in last_visual if row.get('request_sha256')
                                  for item in classify_persistence(observed, row)]
                    references = []
                    for page in store.pages():
                        group = page['generation_group']
                        if page['role'] != proposal['role'] or store.state['groups'][str(group)]['status'] != 'accepted':
                            continue
                        review = next((r for r in group_reviews.get(group, []) if r['slide_id'] == page['slide_id']), {})
                        evidence = _page_observation(page, review)
                        references.append({key: evidence[key] for key in ('slide_id', 'role', 'template_page', 'layout_contract')} |
                                          {'titles': evidence['layout_measurements'].get('titles', [])})
                    references.sort(key=lambda r: r['template_page'] != proposal.get('template_page'))
                    feedback = {'findings': findings, 'original_findings': original_findings,
                        'attempt_history': deepcopy(attempts), 'strategy_assessment': strategies,
                        'repair_policy': repair_policy,
                        'allow_repagination': repair_policy['allow_repagination'],
                        'keep_selected_template': not repair_policy['allow_template_switch'],
                        'previous_pages': len(current or []),
                        'repair_goal': LAYOUT_REPAIR_GOAL,
                        'accepted_typography_references': references[:8],
                        'accepted_design_observations': [r.get('observed', '') for value in list(group_reviews.values())[-3:] for r in value][:6]}
                    if current and repair_policy['preserve_page_count']:
                        feedback['required_page_count'] = len(current)
                    replacement = generate(proposal, _model_feedback(feedback) if findings or attempt or extra else None, current)
                    generated_now = True
                if not isinstance(replacement, list) or not replacement:
                    raise ValueError('生成组没有返回页面')
                check_assets(replacement, gi)
                candidate, candidate_plan, revision = store.candidate(gi, replacement)
                if generated_now:
                    generated_revision = revision
                probe = agent.call('render_enterprise_candidate', renderer, candidate, True)
                hard_rows = browser_rows(probe)
                if len(probe.get('pages', [])) != len(replacement):
                    raise ValueError('浏览器probe遗漏候选页面')
                if hard_rows:
                    rows, errors = hard_rows, []
                else:
                    rows, errors = yield from diagnose(candidate, candidate_plan, probe,
                        'candidate' if attempt or extra or review_targets(gi) else 'initial', previous=[*last_visual, *human],
                        context_id=(f'enterprise-candidate-review-retry:{revision}:attempt-{attempt}'
                                    if repair_policy['category'] == 'review_evidence' and attempt else None),
                        reusable=group_reviews.get(gi, []) if review_targets(gi) else ())
                retain_seed_issues(gi, rows)
                tracked_ids = {i.get('issue_id') for r in original_findings for i in r.get('issues', [])}
                original_findings.extend(deepcopy(r) for r in seed_groups.get(gi, [])
                    if any(i['issue_id'] not in tracked_ids for i in r['issues']))
                human_checks = reviewer_status(gi, candidate_plan['pages'], rows)
                human_pending = [item for item in human_checks if item['status'] != 'resolved']
                seed_checks = seed_status(gi, candidate_plan['pages'], rows)
                seed_pending = [item for item in seed_checks if item['status'] != 'resolved']
                passed = not hard_rows and not errors and not human_pending and not seed_pending and enabled and len(rows) == len(replacement) and not actionable(rows)
                comparison = compare_candidate(gi, revision, candidate_plan, rows,
                    [*original_findings, *findings]) if passed else None
                if comparison and not comparison['accept']:
                    passed = False
                    errors.append({'type': 'candidate_regression', 'detail': comparison['reason']})
                state = 'accepted' if passed else 'draft_needs_review'
                if human and not hard_rows and generated_now:
                    application = feedback_application(gi, revision)
                notes = {'errors': errors, 'reviewer_feedback': human_checks,
                         'seed_issue_registry': deepcopy(seed_groups.get(gi, [])), 'seed_issues': seed_checks}
                if chat_request_ids and generated_revision == revision:
                    # This committed check is the durable proof that the chat
                    # instruction reached generation, including review-only
                    # retries of the generated candidate and crash recovery.
                    notes['chat_application'] = {'request_ids': list(chat_request_ids),
                        'generated_revision': revision, 'base_revision': (before or {}).get('revision')}
                if application and application == feedback_application(gi, revision):
                    notes['reviewer_feedback_application'] = application
                elif review_current and attempt == 0:
                    notes['reviewer_feedback_application_status'] = 'legacy_unverified'
                store.record(gi, revision, probe, rows, state, json.dumps(notes, ensure_ascii=False))
                committed = not hard_rows and store.commit(gi, revision, state)
                if committed:
                    materialize()
                    group_reviews[gi] = deepcopy(rows)
                    committed_seed = deepcopy(seed_groups.get(gi, []))
                # Failed candidates inform repairs, never the committed review.
                entry = {'group': gi, 'attempt': attempt, 'deck_round': deck_round, 'revision': revision,
                         'committed': bool(committed), 'status': 'verified' if passed and committed else 'needs_repair',
                         'findings': deepcopy(rows), 'errors': errors, 'comparison': comparison,
                         'reviewer_feedback': human_checks, 'seed_issues': seed_checks, 'generated': generated_now,
                         'repair_policy': deepcopy(repair_policy)}
                report['repairs'].append(entry)
                attempts.append(deepcopy(entry))
                if not hard_rows:
                    observed.extend(deepcopy(rows))
                    if rows:
                        by_id = {row.get('slide_id'): row for row in last_visual}
                        by_id.update({row.get('slide_id'): row for row in rows})
                        last_visual = list(by_id.values())
                if passed and committed:
                    seed_groups[gi] = committed_seed
                    report['committed_reviews'] = deepcopy(group_reviews)
                    update_report()
                    return True
                current = deepcopy(candidate_plan['pages'])
                findings = [*deepcopy(rows), *[{'verdict': 'fix', 'origin': 'tool',
                    'issues': [{'severity': 'high', 'type': error['type'], 'detail': error['detail']}],
                    **({'slide_id': error['slide_id']} if error.get('slide_id') else {})} for error in errors]]
                # Missing/uncertain evidence is a review task, not permission to
                # rewrite the same draft again. A real persists/new finding can
                # still send the candidate to GLM on the following attempt.
                confirmed_fix = any(any(issue.get('severity') in {'medium', 'high'} for issue in row.get('issues', [])) or
                    any(c.get('status') == 'persists' for c in row.get('rechecks', [])) for row in rows)
                evidence_retry = build_repair_policy(proposal, findings, history=attempts,
                    attempt=attempt+1, current=current)['category'] == 'review_evidence'
                if not hard_rows and (not enabled or budget_exhausted.is_set() or
                    ((human_pending or (review_current and attempt == 0)) and not confirmed_fix and not evidence_retry)):
                    seed_groups[gi] = committed_seed
                    report['history'].append({'group': gi, 'type': 'visual_unverified', 'attempts': deepcopy(attempts)})
                    report['committed_reviews'] = deepcopy(group_reviews)
                    update_report()
                    return False
                proposal = {k: v for k, v in replacement[0].items() if k in proposal or k == 'body_template_revision'}
            except ERRORS as exc:
                reason = str(exc)[:2000]
                if revision:
                    store.record(gi, revision, probe, rows, 'failed', reason)
                attempts.append({'attempt': attempt, 'error': reason, 'revision': revision,
                                 'repair_policy': deepcopy(repair_policy)})
                findings = [{'verdict': 'fix', 'origin': 'tool', 'issues': [{'severity': 'high',
                    'type': 'tool_error', 'detail': reason,
                    'fix_hint': '根据原始工具错误修复，不得删除来源或解除人工品牌保护'}]}]
                if _budget_error(reason):
                    budget_exhausted.set()
                    break
            corrections.append({'tool': 'enterprise_v5_repair', 'group': gi + 1, 'attempt': attempt,
                                'deck_round': deck_round, 'findings': deepcopy(findings)})
            log()
            update_report()
        report['history'].append({'group': gi, 'type': 'repair_exhausted', 'deck_round': deck_round,
                                  'detail': '本组初次尝试及3轮纠错后仍需复核', 'attempts': attempts})
        seed_groups[gi] = committed_seed
        if existing and not store.committed_group(gi):
            # Failed new candidates must not erase the original optimization
            # pages. Preserve only source-complete, browser-valid originals as
            # drafts; this is not visual acceptance or applied-feedback proof.
            try:
                from .template_html import validate_sources, table_headers
                blocks = [b for b in source.get('blocks', []) if b['id'] in groups[gi].get('block_ids', [])]
                agenda = [a for a in source.get('agenda', []) if a['id'] in groups[gi].get('agenda_ids', [])]
                validate_sources([p['html'] for p in existing], blocks, agenda, table_headers(source.get('blocks', [])))
                candidate, candidate_plan, revision = store.candidate(gi, existing)
                probe = agent.call('render_enterprise_original_fallback', renderer, candidate, True)
                if len(probe.get('pages', [])) != len(existing) or browser_rows(probe):
                    raise ValueError('原始页面未通过完整浏览器硬检查，不能作为保留稿提交')
                notes = {'original_fallback': True, 'attempts': deepcopy(attempts),
                         'reviewer_feedback': reviewer_status(gi, candidate_plan['pages'], []),
                         'seed_issue_registry': deepcopy(committed_seed)}
                store.record(gi, revision, probe, [], 'draft_needs_review', json.dumps(notes, ensure_ascii=False))
                if store.commit(gi, revision, 'draft_needs_review'):
                    materialize()
                    group_reviews[gi] = []
                    report['history'].append({'group': gi, 'type': 'original_fallback', 'revision': revision,
                                              'detail': '修复失败，保留来源和浏览器硬检通过的原页，仍待复核'})
            except ERRORS as exc:
                report['history'].append({'group': gi, 'type': 'original_fallback_failed', 'detail': str(exc)[:2000]})
        report['committed_reviews'] = deepcopy(group_reviews)
        update_report()
        return False

    def group_workflow(*args, **kwargs):
        result = yield from process_group(*args, **kwargs)
        chat_checkpoint()
        return result

    def chat_request_checks(request_id, request):
        results = []
        for gi in sorted({row['generation_group'] for row in request['rows']}):
            current = committed_evidence(gi)
            targets = [row for row in request['rows'] if row['generation_group'] == gi]
            checks = issue_status(gi, (current or {}).get('pages', []),
                                  (current or {}).get('reviews', []), targets)
            application = (current or {}).get('notes', {}).get('chat_application', {})
            generated = (current is not None and current['status'] == 'accepted'
                and request_id in application.get('request_ids', [])
                and application.get('generated_revision') == current['revision'])
            results.append({'generation_group': gi, 'revision': (current or {}).get('revision'),
                'generated_and_committed': bool(generated), 'checks': checks,
                'resolved': bool(generated and checks and all(item['status'] == 'resolved' for item in checks))})
        return results

    def finish_chat_request(request_id, request, details):
        status = 'completed' if details and all(item['resolved'] for item in details) else 'needs_attention'
        text = ('修改已提交，相关页面已逐项复验。' if status == 'completed'
                else '本轮未能完成并验证全部修改；现稿与未完成意见已保留。')
        request.update(status=status, result_text=text, details=details)
        # Persist first: if acknowledgement fails, recovery acknowledges this
        # result again without paying for the same generation a second time.
        save_chat_mapping()
        chat.finish_request(job_id, request_id, status, text, details=details)

    chat_batches = 0

    def drain_chat():
        nonlocal chat_batches
        if chat is None:
            return
        # Only call after the scheduler has drained every in-flight group.
        # Freeze all rows in a batch before starting any of its reviews.
        while chat_batches < MAX_CHAT_BATCHES:
            chat_checkpoint()
            claimed = chat.claim_ready(job_id, plan)
            if not claimed:
                break
            chat_batches += 1
            requests = {}
            for claimed_request in claimed:
                request_id = claimed_request['id']
                if not isinstance(request_id, str) or not request_id:
                    raise ValueError('对话修改请求ID无效')
                request = chat_mapping['requests'].get(request_id)
                if request is None:
                    chat_rows(claimed_request)
                    request = {'rows': deepcopy(claimed_request['rows']), 'status': 'applying'}
                    chat_mapping['requests'][request_id] = request
                    save_chat_mapping()
                    import_chat_request(request)
                elif request['rows'] != claimed_request['rows']:
                    raise ValueError('已认领的对话意见发生变化，拒绝覆盖原始请求')
                if request['status'] == 'needs_attention' and claimed_request.get('retry_requested') is True:
                    request.update(status='applying')
                    request.pop('result_text', None)
                    request.pop('details', None)
                    save_chat_mapping()
                if request['status'] in {'completed', 'needs_attention'}:
                    chat.finish_request(job_id, request_id, request['status'], request['result_text'],
                                        details=request.get('details'))
                    continue
                requests[request_id] = request
            affected = {}
            for request_id, request in requests.items():
                details = chat_request_checks(request_id, request)
                # A crash after commit but before acknowledging the queue does
                # not require another generation or another visual request.
                if details and all(item['resolved'] for item in details):
                    finish_chat_request(request_id, request, details)
                    continue
                for row in request['rows']:
                    affected.setdefault(row['generation_group'], set()).add(request_id)
            if affected and not budget_exhausted.is_set():
                run_review_workflows((group_workflow(gi, groups[gi], store.committed_group(gi),
                    reviewer_groups.get(gi), force_repair=True, chat_request_ids=sorted(request_ids))
                    for gi, request_ids in sorted(affected.items())), workers=workers,
                    stopped=budget_exhausted.is_set)
            for request_id, request in requests.items():
                if request['status'] == 'applying':
                    finish_chat_request(request_id, request, chat_request_checks(request_id, request))
            update_report()

    # Only immutable single-image requests run in worker threads. GLM generation,
    # rendering, revision commits and all shared workflow state stay serialized.
    def group_workflows():
        for gi, proposal in enumerate(groups):
            before = committed_evidence(gi)
            chat_group = any(row['generation_group'] == gi
                             for request in chat_mapping['requests'].values() for row in request['rows'])
            if before and (chat_resume or chat_group):
                # Chat requests are retried only when the persistent queue asks
                # for it. Completed/failed obligations remain visible to audit.
                continue
            if before and before['status'] == 'accepted' and review_targets(gi) and all(
                item['status'] == 'resolved' for item in issue_status(gi, before['pages'], before['reviews'], review_targets(gi))):
                continue
            existing = store.committed_group(gi) or (generated[gi] if gi < len(generated) else None)
            yield group_workflow(gi, proposal, existing, reviewer_groups.get(gi))

    # A manual conversation resume must spend its next available review slots
    # on the pages the user named, before unrelated draft groups use the budget.
    if chat_resume:
        drain_chat()
    run_review_workflows(group_workflows(), workers=workers, stopped=budget_exhausted.is_set)
    drain_chat()

    def audit(round_number, prior_final=()):
        drain_chat()
        materialize()
        missing = sorted(set(range(len(groups))) - {p['generation_group'] for p in plan['pages']})
        validation_error = ''
        try:
            check_deck()
        except ERRORS as exc:
            validation_error = str(exc)[:2000]
        if not plan['pages']:
            raise ValueError('没有通过硬检查的页面；候选和完整诊断已保留：' +
                             (validation_error or json.dumps(report['history'], ensure_ascii=False)[:1200]))
        plan.pop('html_only', None)
        atomic_json(folder / 'plan.json', plan)
        probe, errors = {'pages': []}, []
        try:
            probe = agent.call('render_frozen_enterprise', renderer, folder, True)
            if len(probe.get('pages', [])) != len(plan['pages']):
                raise ValueError('浏览器probe遗漏最终页面')
        except ERRORS as exc:
            errors.append({'type': 'final_render_unverified', 'detail': str(exc)[:2000]})
        browser_passed = not errors and not browser_rows(probe)
        hard = not missing and not validation_error and browser_passed
        if not hard:
            plan['html_only'] = True
        frozen = digest(plan)
        atomic_json(folder / 'plan.json', plan)
        atomic_json(folder / 'frozen-plan.json', plan)
        atomic_json(folder / f'frozen-plan-{round_number}.json', plan)
        jobs.update(job_id, stage='rendering', quality_status='checking', deck_revision=frozen)
        final = []
        if hard:
            # Committed checks are hash-verified and keep the original stage and
            # source location. A prior unresolved observation takes precedence
            # unless the current candidate explicitly resolves its issue IDs.
            reusable = []
            for gi in sorted({p['generation_group'] for p in plan['pages']}):
                committed = committed_evidence(gi)
                if not committed:
                    continue
                for row in committed['reviews']:
                    previous = [r for r in prior_final if r.get('slide_id') == row.get('slide_id')]
                    resolved = {c.get('issue_id') for c in row.get('rechecks', [])
                                if c.get('status') == 'resolved' and c.get('evidence')}
                    for old in previous:
                        obligations = {i['issue_id'] for i in _prior_issues([old], old['slide_id'])
                                       if i.get('severity') in {'medium', 'high'}}
                        if actionable([old]) and (not obligations or not obligations.issubset(resolved)):
                            reusable.append(old)
                    reusable.append({**deepcopy(row), 'review_reuse': {'phase': row.get('phase'),
                        'page': row.get('page'), 'request_sha256': row.get('request_sha256'),
                        'source': 'committed_candidate', 'folder': f'candidates/{committed["revision"]}',
                        'check': committed['check'], 'check_sha256': committed['check_sha256']}})
            reusable.extend(prior_final)
            final, visual_errors = run_review_workflows(
                [diagnose(folder, plan, probe, 'final', reusable=reusable)],
                workers=workers, stopped=budget_exhausted.is_set)[0]
            errors.extend(visual_errors)
        deck_review = {'status': 'unverified', 'issues': []}
        asset_errors = []
        for gi in sorted({page['generation_group'] for page in plan['pages']}):
            try:
                asset_errors.extend(check_assets([p for p in plan['pages'] if p['generation_group'] == gi], gi))
            except ERRORS as exc:
                asset_errors.append({'type': 'asset_usage_failed', 'generation_group': gi, 'detail': str(exc)[:2000]})
        if final and len(final) == len(plan['pages']):
            by_id = {row['slide_id']: row for row in final}
            try:
                answer = call('enterprise_deck_critic',
                    '你是企业演示稿整册审查者。仅依据给定逐页观察、来源标题和实测摘要判断叙事重复、样式漂移、关键内容未突出。不要假装看过图片。'
                    '返回JSON {"issues":[{"slide_id":"给定ID","severity":"medium或high","detail":"具体证据","fix_hint":"建议"}]}。'
                    '保留企业固定样式，不把正常章节留白当缺陷。逐项审视aesthetics量表与证据：低分且有明确可修复缺陷时须反馈对应页面，'
                    '不能把“勉强可用”认定为完成高质量审查，也不能仅依据分数机械判坏或捏造截图事实；没有明确问题则空数组。'
                    + LAYOUT_REVIEW_POLICY,
                    {'theme': plan.get('theme', {}), 'pages': [_page_observation(p, by_id[p['slide_id']]) for p in plan['pages']]})
                if not isinstance(answer, dict) or not isinstance(answer.get('issues'), list):
                    raise ValueError('整册审查结构无效')
                if any(not isinstance(issue, dict) or issue.get('slide_id') not in by_id
                       or issue.get('severity') not in {'medium', 'high'}
                       or not isinstance(issue.get('detail'), str) or not issue['detail'].strip()
                       for issue in answer['issues']):
                    raise ValueError('整册审查引用无效页或缺少依据')
                deck_review = {'status': 'passed' if not answer['issues'] else 'needs_review',
                               'issues': deepcopy(answer['issues'])}
            except ERRORS as exc:
                deck_review['reason'] = str(exc)[:1000]
        result = {'round': round_number, 'deck_revision': frozen, 'probe': probe, 'pages': final,
                  'deck_review': deck_review, 'missing_groups': missing, 'source_validation_error': validation_error,
                  'browser_passed': browser_passed, 'hard_pass': hard, 'errors': errors, 'asset_errors': asset_errors,
                  'visual_review_mode': 'changed_pages_only',
                  'reused_final_reviews': sum(1 for row in final if row.get('reused_final_review')),
                  'reused_visual_reviews': sum(1 for row in final if row.get('reused_visual_review'))}
        atomic_json(folder / f'final-audit-{round_number}.json', result)
        report['audits'].append({'round': round_number, 'deck_revision': frozen, 'report': f'final-audit-{round_number}.json'})
        report.update(pages=final, deck_revision=frozen, deck_review=deck_review)
        update_report()
        return result

    final_audit = audit(0)
    # One deck-wide repair round, with independently bounded groups.
    for deck_round in range(1, MAX_DECK_REPAIR_ROUNDS + 1):
        affected = {}
        group_by_id = {p['slide_id']: p['generation_group'] for p in plan['pages']}
        for row in final_audit['pages']:
            diagnostic_history.setdefault(group_by_id[row['slide_id']], []).append(deepcopy(row))
            # Initial/candidate findings already exhausted that group's bounded
            # repair loop. Reusing the same evidence must not start four more
            # attempts; only new frozen-render observations enter this loop.
            if actionable([row]) and (not row.get('reused_visual_review') or row.get('phase') == 'final'):
                affected.setdefault(group_by_id[row['slide_id']], []).append(deepcopy(row))
        for issue in final_audit['deck_review']['issues']:
            affected.setdefault(group_by_id[issue['slide_id']], []).append({
                'slide_id': issue['slide_id'], 'verdict': 'fix', 'origin': 'deck_critic',
                'issues': [{**deepcopy(issue), 'recheck_scope': 'deck'}]})
        for row in browser_rows(final_audit['probe']):
            if row.get('slide_id') in group_by_id:
                affected.setdefault(group_by_id[row['slide_id']], []).append(row)
        if not affected or budget_exhausted.is_set():
            break
        run_review_workflows((group_workflow(gi, groups[gi], store.committed_group(gi), findings,
                                           deck_round=deck_round) for gi, findings in sorted(affected.items())),
                             workers=workers, stopped=budget_exhausted.is_set)
        # The next audit reuses the latest full-page check of each exact version,
        # including a repaired page already checked during candidate acceptance.
        final_audit = audit(deck_round,
                            prior_final=final_audit['pages'] if final_audit['hard_pass'] else ())

    final, probe = final_audit['pages'], final_audit['probe']
    frozen, hard = final_audit['deck_revision'], final_audit['hard_pass']
    deck_review = final_audit['deck_review']
    limitations = [*deepcopy(final_audit['errors']), *deepcopy(reviewer_unmapped)]
    human_checks, seed_checks, original_fallbacks = [], [], []
    for gi in range(len(groups)):
        committed = committed_evidence(gi)
        human_checks.extend(reviewer_status(gi, (committed or {}).get('pages', []),
                                            (committed or {}).get('reviews', [])))
        seed_checks.extend(seed_status(gi, (committed or {}).get('pages', []),
                                      (committed or {}).get('reviews', [])))
        if committed and committed['notes'].get('original_fallback'):
            original_fallbacks.append(gi)
    human_pending = [item for item in human_checks if item['status'] != 'resolved']
    if human_pending:
        limitations.append({'type': 'reviewer_feedback_unverified', 'issues': deepcopy(human_pending)})
    seed_pending = [item for item in seed_checks if item['status'] != 'resolved']
    if seed_pending:
        limitations.append({'type': 'seed_issues_unverified', 'issues': deepcopy(seed_pending)})
    if original_fallbacks:
        limitations.append({'type': 'original_fallback', 'groups': original_fallbacks,
                            'detail': '修复失败后仅保留了通过硬检的原页，尚未完成修复验收'})
    if final_audit['missing_groups']:
        limitations.append({'type': 'missing_groups', 'groups': final_audit['missing_groups']})
    if final_audit['source_validation_error']:
        limitations.append({'type': 'source_validation_failed', 'detail': final_audit['source_validation_error']})
    approval = jobs.get(job_id).get('outline_approval') if hasattr(jobs, 'get') else None
    approved_page_count = approval.get('page_count') if approval else None
    outline_page_count_matches = approved_page_count is None or approved_page_count == len(plan['pages'])
    if not outline_page_count_matches:
        limitations.append({'type': 'outline_page_count_mismatch', 'approved': approved_page_count,
                            'actual': len(plan['pages']),
                            'detail': '成品页数与已确认大纲不一致；保留完整草稿，请检查缺页或续页后重新确认'})
    limitations.extend({'type': 'browser_issue', **row} for row in browser_rows(probe))
    limitations.extend({'type': 'visual_issue', **row} for row in final if actionable([row]))
    if len(final) != len(plan['pages']) and not final_audit['errors']:
        limitations.append({'type': 'visual_incomplete', 'detail': '最终截图尚未逐页绑定有效的完整视觉审查证据'})
    if deck_review['status'] != 'passed':
        limitations.append({'type': 'deck_review_' + deck_review['status'], **deepcopy(deck_review)})
    asset_issues = [*deepcopy((asset_manifest or {}).get('quality_issues', [])), *deepcopy(final_audit['asset_errors'])]
    if asset_issues:
        limitations.append({'type': 'asset_quality', 'issues': asset_issues})
    accepted = (hard and enabled and outline_page_count_matches and len(final) == len(plan['pages']) and not actionable(final)
        and deck_review['status'] == 'passed' and not asset_issues and not reviewer_unmapped
        and not human_pending and not seed_pending and not original_fallbacks)

    # Archive earlier outputs; HTML-only and failed exports cannot expose an old PPTX.
    for name in ('presentation.html', 'presentation.pptx'):
        path = folder / name
        if path.is_file():
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            archive = folder / 'artifact-history' / f'{path.stem}-{sha}{path.suffix}'
            archive.parent.mkdir(exist_ok=True)
            path.replace(archive)
    export_ok, export_matches = False, False
    try:
        agent.call('export_frozen_enterprise', renderer, folder)
        exported = json.loads((folder / 'enterprise-probe.json').read_text())
        expected = [(p.get('slide_id'), p.get('screenshot_sha')) for p in probe['pages']]
        actual = [(p.get('slide_id'), p.get('screenshot_sha')) for p in exported['pages']]
        export_matches = len(expected) == len(plan['pages']) and all(sha for _, sha in expected) and expected == actual
        export_ok = True
        if not export_matches:
            limitations.append({'type': 'export_version_mismatch', 'detail': '导出截图与终审版本不同，需要重新复核'})
    except ERRORS as exc:
        limitations.append({'type': 'export_failed', 'detail': str(exc)[:2000]})
    artifact_names = ['presentation.html']
    if export_ok and not plan.get('html_only'):
        artifact_names.append('presentation.pptx')
    artifacts = {name: hashlib.sha256((folder / name).read_bytes()).hexdigest()
                 for name in artifact_names if (folder / name).is_file()}
    if export_ok and not plan.get('html_only') and 'presentation.pptx' not in artifacts:
        limitations.append({'type': 'pptx_missing', 'detail': '导出未产生当前版本的PPTX'})
    accepted = bool(accepted and export_ok and export_matches and 'presentation.pptx' in artifacts)
    status = 'accepted' if accepted else 'needs_review'
    final_by_id = {row['slide_id']: row for row in final}
    probe_by_id = {row.get('slide_id'): row for row in probe.get('pages', [])}
    quality = {'workflow_version': 5, 'visual_review_mode': 'changed_pages_only',
        'quality_status': status, 'ready_for_delivery': accepted, 'deck_revision': frozen,
        'outline_page_count': {'approved': approved_page_count, 'actual': len(plan['pages']),
                               'matches': outline_page_count_matches},
        'artifact_sha256': artifacts, 'artifact_formats': ['pptx' if name.endswith('.pptx') else 'html' for name in artifacts],
        'pages': [{'slide_id': p['slide_id'], 'slide_version': p['slide_version'],
            'screenshot_sha': probe_by_id.get(p['slide_id'], {}).get('screenshot_sha'),
            'visual_verified': p['slide_id'] in final_by_id and not actionable([final_by_id[p['slide_id']]])}
            for p in plan['pages']],
        'checks': {'source_complete': not final_audit['missing_groups'] and not final_audit['source_validation_error'],
                   'outline_page_count_matches': outline_page_count_matches,
                   'browser_passed': final_audit['browser_passed'], 'visual_complete': len(final) == len(plan['pages']),
                   'deck_review': deck_review['status'], 'assets_complete': not asset_issues,
                   'reviewer_feedback_complete': not human_pending and not reviewer_unmapped,
                   'seed_issues_complete': not seed_pending,
                   'export_matches_review': bool(export_matches), 'pptx_exported': 'presentation.pptx' in artifacts},
        'reviewer_feedback': human_checks, 'seed_issues': seed_checks, 'limitations': limitations}
    report.update(status='passed' if accepted else 'needs_review', pages=final, issues=limitations,
        missing_groups=final_audit['missing_groups'], source_validation_error=final_audit['source_validation_error'],
        asset_quality_issues=asset_issues,
        reviewer_feedback_checks=human_checks,
        seed_issue_checks=seed_checks,
        notice='最终版本通过来源、品牌、浏览器、单图视觉及整册审查' if accepted else '草稿已保留；仍有未解决或未验证项')
    atomic_json(folder / 'quality-report.json', quality)
    store.record_final_quality()
    update_report()
    log()
    base_path = folder / 'report.json'
    try:
        base_report = json.loads(base_path.read_text()) if base_path.exists() else {}
    except (ValueError, OSError):
        base_report = {}
    base_report.update(passed=accepted, quality_status=status, ready_for_delivery=accepted,
        visual_review={'status': report['status'], 'report': 'visual-review.json'}, quality_report='quality-report.json',
        artifacts_available=list(artifacts), artifact_formats=quality['artifact_formats'])
    base_report.setdefault('checks', {}).update(quality['checks'])
    if 'presentation.pptx' not in artifacts:
        base_report['checks'].update(editable_text=False, pptx_application_render='not_exported')
    atomic_json(base_path, base_report)
    agent.finish('completed', quality=status, page_count=len(plan['pages']))
    jobs.update(job_id, status='completed', stage='completed' if accepted else 'needs_review', quality_status=status,
        ready_for_delivery=accepted, artifacts_available=list(artifacts), deck_revision=frozen, page_count=len(plan['pages']),
        checks=quality['checks'], visual_status=report['status'], visual_notice=report['notice'], repairs=len(report['repairs']))
    return quality
