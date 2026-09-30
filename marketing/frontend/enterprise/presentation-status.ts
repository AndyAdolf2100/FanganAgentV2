// Completed execution and accepted output are independent states.
export function presentationQuality(job: any) {
  const enterprise = !!job?.options?.template_id;
  const completed = job?.status === 'completed';
  const explicit = job?.quality_status !== undefined || job?.ready_for_delivery !== undefined;
  const needsReview = enterprise && completed && (job?.quality_status === 'needs_review' || job?.ready_for_delivery === false || job?.stage === 'needs_review' || ['needs_review', 'incomplete', 'disabled'].includes(job?.visual_status));
  const accepted = enterprise && completed && !needsReview && job?.quality_status === 'accepted' && job?.ready_for_delivery === true;
  const available = Array.isArray(job?.artifacts_available) ? job.artifacts_available : !explicit ? ['presentation.html', 'presentation.pptx'] : [];
  return { needsReview, accepted, html: completed && available.includes('presentation.html'), pptx: completed && available.includes('presentation.pptx') };
}

// v5 reports source-group attempts; older jobs report a list of page numbers.
// Progress can arrive partially during polling, so neither shape is required.
export function presentationRepairProgress(job: any) {
  const progress = job?.stage === 'visual_review' ? job?.visual_progress : job?.repair_progress;
  if (!progress) return '';
  const positive = (value: any) => Number.isInteger(value) && value > 0;
  if (positive(progress.group)) {
    const total = job?.page_progress?.total;
    const group = `正在优化第 ${progress.group}${positive(total) ? ` / ${total}` : ''} 个来源组`;
    return `${group} · ${positive(progress.attempt) ? `本组第 ${progress.attempt} 次纠错` : '本组首次处理'}`;
  }
  const pages = Array.isArray(progress.pages) ? progress.pages.filter(positive) : [];
  const label = pages.length ? `正在处理第 ${pages.join('、')} 页` : '正在处理页面';
  const round = positive(progress.round) ? `第 ${progress.round} 轮排版修复 · ` : '';
  const count = Number.isInteger(progress.current) && progress.current >= 0 && positive(progress.total)
    ? ` · ${progress.current} / ${progress.total} 个问题页面组` : '';
  return `${round}${label}${count}`;
}

// v5 initial/candidate screenshot counts are local to one source group.
export function presentationVisualProgress(job: any) {
  const progress = job?.visual_progress;
  if (!(Number(job?.enterprise_workflow_version) >= 5) || !['initial', 'candidate'].includes(progress?.phase)) return '';
  const count = (value: any) => Number.isInteger(value) && value >= 0 ? value : '—';
  return `页面组 ${count(job?.page_progress?.current)} / ${count(job?.page_progress?.total)} · 本组截图 ${count(progress.current)} / ${count(progress.total)}`;
}
