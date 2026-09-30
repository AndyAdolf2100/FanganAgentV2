"""Immutable enterprise candidates, evidence-bound commits and safe recovery."""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
from pathlib import Path
import re
import shutil
import time
from uuid import uuid4

SCHEMA_VERSION = 2
MAX_PAGES = 200
COMMIT_STATUSES = {'accepted', 'draft_needs_review'}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _reference_compatible(saved, current, expected_sha):
    """Allow only added/revised per-page descriptive layout evidence.

    The candidate's original copy must still match its complete raw hash.
    A new renderer may augment layout_measurements without changing the
    template, geometry, styles, screenshots or hard validation evidence.
    """
    saved_raw = saved.read_bytes() if saved.is_file() else None
    current_raw = current.read_bytes() if current.is_file() else None
    raw_sha = lambda raw: hashlib.sha256(raw).hexdigest() if raw is not None else None
    if raw_sha(saved_raw) != expected_sha:
        return False
    if raw_sha(current_raw) == expected_sha:
        return True
    if saved_raw is None or current_raw is None:
        return False

    def projection(raw):
        value = json.loads(raw)
        if (not isinstance(value, dict) or not isinstance(value.get('pages'), list)
            or any(not isinstance(page, dict) for page in value['pages'])):
            raise ValueError('模板探针结构无效')
        return {**value, 'pages': [{key: child for key, child in page.items()
                                  if key != 'layout_measurements'} for page in value['pages']]}

    try:
        # Canonical JSON preserves scalar types, unlike Python equality where
        # True == 1. Only the one documented page-level field is omitted.
        return digest(projection(saved_raw)) == digest(projection(current_raw))
    except (ValueError, TypeError):
        return False


def _group(group):
    if type(group) is not int or not 0 <= group < MAX_PAGES:
        raise ValueError('页面组ID须为0至199的整数')
    return str(group)


def _revision(revision):
    if not isinstance(revision, str) or not re.fullmatch(r'[a-f0-9]{64}', revision):
        raise ValueError('候选版本ID无效')
    return revision


class RevisionStore:
    def __init__(self, folder, base):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.base = {key: deepcopy(value) for key, value in base.items() if key != 'pages'}
        self.path = self.folder / 'revision-state.json'
        self.fingerprint = self._fingerprint()
        with self._locked():
            self._pages(self.state)

    def _fingerprint(self):
        template = self.folder / 'template.json'
        source_plan = self.folder / 'source-plan.json'
        checkpoint = self.folder / 'workflow-checkpoint.json'
        return {'base_sha256': digest(self.base),
                'source_sha256': digest({'source_blocks': self.base.get('source_blocks'),
                    'source_sha256': self.base.get('source_sha256'),
                    'source_plan': json.loads(source_plan.read_text()) if source_plan.is_file() else None,
                    'manuscript_sha256': _sha(self.folder / 'manuscript.md')}),
                'template_sha256': digest({'template_id': self.base.get('template_id'),
                    'template_revision': self.base.get('template_revision'),
                    'snapshot': json.loads(template.read_text()) if template.is_file() else None}),
                'theme_sha256': digest(self.base.get('theme')),
                'workflow_input_sha256': json.loads(checkpoint.read_text()).get('input_sha256') if checkpoint.is_file() else None}

    @contextmanager
    def _locked(self):
        # Reload under a lock: a stale store cannot overwrite an accepted pointer.
        with (self.folder / 'revision-state.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                if self._fingerprint() != self.fingerprint:
                    raise ValueError('原稿或模板在任务期间改变，拒绝沿用旧候选')
                if self.path.exists():
                    state = json.loads(self.path.read_text())
                    if not isinstance(state, dict) or state.get('schema_version') != SCHEMA_VERSION or state.get('fingerprint') != self.fingerprint:
                        raise ValueError('版本仓与当前base/原稿/模板/主题指纹不符，保留旧版本并拒绝恢复')
                    if not isinstance(state.get('groups'), dict) or not isinstance(state.get('history'), list):
                        raise ValueError('版本仓状态格式无效')
                    self.state = state
                else:
                    self.state = {'schema_version': SCHEMA_VERSION, 'fingerprint': self.fingerprint,
                                  'groups': {}, 'history': [], 'issues': []}
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _reference_sha(self):
        return _sha(self.folder / 'template-reference' / 'enterprise-probe.json')

    def _load_candidate(self, group, revision):
        _group(group)
        _revision(revision)
        folder = self.folder / 'candidates' / revision
        try:
            plan = json.loads((folder / 'plan.json').read_text())
            manifest = json.loads((folder / 'candidate.json').read_text())
            if not isinstance(plan, dict) or not isinstance(manifest, dict) or manifest.get('schema_version') != SCHEMA_VERSION:
                raise ValueError('候选清单格式或版本无效')
            if digest(plan) != revision or manifest.get('revision') != revision or manifest.get('group') != group:
                raise ValueError('候选内容哈希或页面组不符')
            if manifest.get('fingerprint') != self.fingerprint or {k: v for k, v in plan.items() if k != 'pages'} != self.base:
                raise ValueError('候选输入指纹不符')
            pages = plan.get('pages')
            if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES:
                raise ValueError('候选页数须为1至200')
            for part, page in enumerate(pages, 1):
                if not isinstance(page, dict) or page.get('generation_group') != group or page.get('slide_id') != f'group-{group:04d}-part-{part:03d}':
                    raise ValueError('候选页面身份不符')
                if page.get('slide_version') != digest({k: v for k, v in page.items() if k not in {'slide_version', 'quality_state'}}):
                    raise ValueError('候选页面版本不符')
            expected_reference = manifest.get('reference_sha256')
            if not _reference_compatible(folder / 'template-reference' / 'enterprise-probe.json',
                                         self.folder / 'template-reference' / 'enterprise-probe.json', expected_reference):
                raise ValueError('候选模板探针依赖改变')
            return plan
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError('候选版本损坏或不完整，拒绝复用') from exc

    def candidate(self, group, pages):
        _group(group)
        if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES:
            raise ValueError('候选页数须为1至200')
        pages = deepcopy(pages)
        for part, page in enumerate(pages, 1):
            if not isinstance(page, dict) or not isinstance(page.get('html'), str) or not page['html'].strip():
                raise ValueError('候选须包含实际HTML页面')
            if 'generation_group' in page and (type(page['generation_group']) is not int or page['generation_group'] != group):
                raise ValueError('候选混入其他页面组')
            page.pop('quality_state', None)
            page['generation_group'] = group
            page['slide_id'] = f'group-{group:04d}-part-{part:03d}'
            page['slide_version'] = digest({k: v for k, v in page.items() if k not in {'slide_version', 'quality_state'}})
        plan = {**self.base, 'pages': pages}
        revision = digest(plan)
        folder = self.folder / 'candidates' / revision
        with self._locked():
            if not folder.exists():
                temporary = folder.with_name('.' + revision + '.' + uuid4().hex)
                temporary.mkdir(parents=True)
                try:
                    atomic_json(temporary / 'plan.json', plan)
                    src = self.folder / 'template-reference' / 'enterprise-probe.json'
                    reference_sha = self._reference_sha()
                    if reference_sha:
                        destination = temporary / 'template-reference' / 'enterprise-probe.json'
                        destination.parent.mkdir()
                        shutil.copy2(src, destination)
                        if _sha(destination) != reference_sha:
                            raise ValueError('复制模板探针时原始依赖改变')
                    atomic_json(temporary / 'candidate.json', {'schema_version': SCHEMA_VERSION,
                        'group': group, 'revision': revision, 'fingerprint': self.fingerprint, 'reference_sha256': reference_sha})
                    temporary.replace(folder)
                finally:
                    if temporary.exists():
                        shutil.rmtree(temporary)
            verified = self._load_candidate(group, revision)
        return folder, verified, revision

    def record(self, group, revision, probe, reviews, status, reason=''):
        if status not in COMMIT_STATUSES | {'failed'}:
            raise ValueError('候选检查状态无效')
        with self._locked():
            self._load_candidate(group, revision)
            record = {'group': group, 'revision': revision, 'status': status, 'reason': reason,
                      'recorded_at': time.time(), 'probe': deepcopy(probe), 'reviews': deepcopy(reviews)}
            record_path = self.folder / 'candidates' / revision / f'check-{time.time_ns()}-{uuid4().hex}.json'
            atomic_json(record_path, record)
            self.state['history'].append({'group': group, 'revision': revision, 'status': status, 'reason': reason,
                'check': str(record_path.relative_to(self.folder)), 'check_sha256': digest(record)})
            atomic_json(self.path, self.state)
        return record

    def _evidence(self, group, revision, status, evidence, plan):
        check = evidence.get('check', '')
        if not isinstance(check, str) or Path(check).parts[:2] != ('candidates', revision) or len(Path(check).parts) != 3:
            raise ValueError('提交检查记录路径无效')
        record = json.loads((self.folder / check).read_text())
        if digest(record) != evidence.get('check_sha256') or record.get('group') != group or record.get('revision') != revision or record.get('status') != status:
            raise ValueError('提交检查记录与候选不匹配')
        probes = record.get('probe', {}).get('pages')
        if not isinstance(probes, list) or len(probes) != len(plan['pages']):
            raise ValueError('提交候选缺少完整浏览器检查')
        for index, probe in enumerate(probes, 1):
            if probe.get('page') != index or probe.get('issues') != []:
                raise ValueError('候选浏览器检查未通过或页面不匹配')
        if status != 'accepted':
            return
        reviews = record.get('reviews')
        if not isinstance(reviews, list) or len(reviews) != len(plan['pages']):
            raise ValueError('候选缺少完整视觉验收')
        for index, (page, probe, review) in enumerate(zip(plan['pages'], probes, reviews), 1):
            screenshot_sha = probe.get('screenshot_sha')
            if not isinstance(screenshot_sha, str) or not re.fullmatch(r'[a-f0-9]{64}', screenshot_sha):
                raise ValueError('浏览器检查缺少截图版本')
            if (review.get('page') != index or review.get('verdict') != 'pass'
                or review.get('slide_id') != page['slide_id'] or review.get('slide_version') != page['slide_version']
                or review.get('html_sha256') != hashlib.sha256(page['html'].encode()).hexdigest()
                or review.get('screenshot_sha') != screenshot_sha):
                raise ValueError('视觉验收与候选页面或截图版本不匹配')
            if not isinstance(review.get('issues'), list) or any(issue.get('severity') in {'medium', 'high'} for issue in review['issues']):
                raise ValueError('视觉验收仍有未解决问题')
            if any(item.get('status') != 'resolved' for item in review.get('rechecks', [])):
                raise ValueError('视觉复查问题尚未关闭')

    def commit(self, group, revision, status):
        key = _group(group)
        if status not in COMMIT_STATUSES:
            raise ValueError('提交状态只能是accepted或draft_needs_review')
        with self._locked():
            plan = self._load_candidate(group, revision)
            old = self.state['groups'].get(key)
            if old and old['status'] == 'accepted' and status != 'accepted':
                return False
            evidence = next((row for row in reversed(self.state['history'])
                             if row['group'] == group and row['revision'] == revision), None)
            if not evidence or evidence['status'] != status:
                raise ValueError('候选尚无相应检查结果，拒绝提交')
            self._evidence(group, revision, status, evidence, plan)
            next_state = deepcopy(self.state)
            next_state['groups'][key] = {'revision': revision, 'status': status,
                'check': evidence['check'], 'check_sha256': evidence['check_sha256']}
            pages = self._pages(next_state)
            # This pointer is the commit boundary. A crash during materialization
            # is recovered by rebuilding plan.json from the checked pointer.
            atomic_json(self.path, next_state)
            self.state = next_state
            atomic_json(self.folder / 'plan.json', {**self.base, 'pages': pages})
        return True

    def _pages(self, state):
        pages = []
        for group, entry in sorted(state['groups'].items(), key=lambda item: int(item[0])):
            if not isinstance(group, str) or not group.isdigit() or str(int(group)) != group:
                raise ValueError('已提交页面组身份无效')
            index = int(group)
            if not isinstance(entry, dict) or entry.get('status') not in COMMIT_STATUSES:
                raise ValueError('已提交页面质量状态无效')
            plan = self._load_candidate(index, entry.get('revision'))
            self._evidence(index, entry['revision'], entry['status'], entry, plan)
            pages.extend({**page, 'quality_state': entry['status']} for page in plan['pages'])
            if len(pages) > MAX_PAGES:
                raise ValueError('提交后整册超过200页，保留原已提交版本')
        return pages

    def pages(self):
        with self._locked():
            return self._pages(self.state)

    def materialize(self):
        with self._locked():
            plan = {**self.base, 'pages': self._pages(self.state)}
            atomic_json(self.folder / 'plan.json', plan)
        return plan

    def committed_group(self, group):
        key = _group(group)
        with self._locked():
            entry = self.state['groups'].get(key)
            if not entry:
                return None
            plan = self._load_candidate(group, entry['revision'])
            self._evidence(group, entry['revision'], entry['status'], entry, plan)
            return deepcopy(plan['pages'])

    def record_final_quality(self):
        """Bind the actual quality report, frozen deck and exported artifacts.

        A failed or incomplete audit is still recorded. An accepted report must
        describe the current committed deck and actual HTML/PPTX bytes; this is
        deliberately not a free-form status setter.
        """
        with self._locked():
            report_path = self.folder / 'quality-report.json'
            raw = report_path.read_bytes()
            report = json.loads(raw)
            if not isinstance(report, dict) or report.get('quality_status') not in {'accepted', 'needs_review'}:
                raise ValueError('最终质量报告状态无效')
            status = report['quality_status']
            ready = report.get('ready_for_delivery')
            if type(ready) is not bool or ready != (status == 'accepted'):
                raise ValueError('最终质量状态与交付标记不一致')
            expected = report.get('artifact_sha256')
            if not isinstance(expected, dict):
                raise ValueError('最终质量报告缺少产物哈希清单')
            if any(name not in {'presentation.html', 'presentation.pptx'}
                   or not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{64}', sha)
                   for name, sha in expected.items()):
                raise ValueError('最终质量报告产物路径或哈希无效')
            actual = {name: _sha(self.folder / name) for name in expected}
            artifact_bound = all(actual[name] == sha for name, sha in expected.items())
            frozen_path = self.folder / 'frozen-plan.json'
            frozen, frozen_sha = None, None
            try:
                frozen = json.loads(frozen_path.read_text())
                if not isinstance(frozen, dict) or not isinstance(frozen.get('pages'), list):
                    raise ValueError('冻结计划格式无效')
                frozen_sha = digest(frozen)
            except (OSError, ValueError, TypeError):
                if ready:
                    raise ValueError('accepted报告缺少可验证冻结计划') from None
            deck_revision = report.get('deck_revision')
            revision_bound = isinstance(deck_revision, str) and frozen_sha is not None and deck_revision == frozen_sha
            if ready:
                if not revision_bound or digest({**self.base, 'pages': self._pages(self.state)}) != frozen_sha:
                    raise ValueError('accepted报告与冻结计划或当前提交版本不匹配')
                if set(expected) != {'presentation.html', 'presentation.pptx'} or not artifact_bound:
                    raise ValueError('accepted报告与实际HTML/PPTX产物哈希不匹配')
                reported_pages = report.get('pages')
                if not isinstance(reported_pages, list) or len(reported_pages) != len(frozen['pages']) or not reported_pages:
                    raise ValueError('accepted报告缺少完整页级验收')
                for page, reviewed in zip(frozen['pages'], reported_pages):
                    if (not isinstance(reviewed, dict) or reviewed.get('slide_id') != page.get('slide_id')
                        or reviewed.get('slide_version') != page.get('slide_version')
                        or reviewed.get('visual_verified') is not True
                        or not isinstance(reviewed.get('screenshot_sha'), str)
                        or not re.fullmatch(r'[a-f0-9]{64}', reviewed['screenshot_sha'])):
                        raise ValueError('accepted报告页身份或视觉验收不完整')
                checks = report.get('checks', {})
                required = ('source_complete', 'browser_passed', 'visual_complete', 'assets_complete',
                            'export_matches_review', 'pptx_exported')
                if not isinstance(checks, dict) or any(checks.get(key) is not True for key in required) or checks.get('deck_review') != 'passed':
                    raise ValueError('accepted报告仍有未完成的质量门禁')
            record = {'status': status, 'ready_for_delivery': ready, 'deck_revision': deck_revision,
                      'report': 'quality-report.json', 'report_sha256': hashlib.sha256(raw).hexdigest(),
                      'frozen_plan_sha256': frozen_sha, 'revision_bound': revision_bound,
                      'artifact_sha256': deepcopy(expected), 'observed_artifact_sha256': actual,
                      'artifact_binding': artifact_bound, 'recorded_at': time.time()}
            self.state['final_quality'] = record
            atomic_json(self.path, self.state)
            return deepcopy(record)

    def invalidate_acceptance(self, reason):
        with self._locked():
            self.state['final_quality'] = {'status': 'needs_review', 'reason': reason}
            atomic_json(self.path, self.state)
