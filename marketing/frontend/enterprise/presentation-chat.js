export const chatStatusLabels = {
  queued: '等待处理', planning: '正在理解要求', planned: '修改计划已准备好',
  applying: '正在修改和检查', completed: '已完成', failed: '本次未完成',
  needs_attention: '需要继续确认',
};

export function parseChatPages(input) {
  const text = String(input || '').trim().replace(/[，、；;]/g, ',').replace(/[—–－]/g, '-').replace(/\s*-\s*/g, '-');
  if (!text) return [];
  const pages = new Set();
  for (const token of text.split(/[,\s]+/)) {
    if (!/^\d+(?:-\d+)?$/.test(token)) throw new Error('页码请填写如 8,13-15，留空可由 Agent 判断。');
    const [start, end = start] = token.split('-').map(Number);
    if (start < 1 || end > 200 || end < start) throw new Error('页码须在 1–200 之间，范围请从小到大填写。');
    for (let page = start; page <= end; page++) pages.add(page);
  }
  return [...pages].sort((a, b) => a - b);
}

export function chatAuthor(message) {
  if (message.role === 'user') return '你';
  if (message.role === 'system' || message.author === 'system' || message.source === 'system') return '系统';
  return message.role === 'assistant' ? 'Agent' : '系统';
}

export function chatTime(value) {
  if (value === undefined || value === null || value === '') return '';
  const date = new Date(typeof value === 'number' && value < 1e12 ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit'});
}

export function newChatMessageId() {
  return globalThis.crypto?.randomUUID?.() || `chat-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export async function chatJson(url, options = {}) {
  const response = await fetch(url, {...options, headers: {'Content-Type': 'application/json', ...options.headers}});
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(typeof body?.detail === 'string' ? body.detail : '暂时无法处理这条消息，请稍后重试。');
  }
  if (!body || typeof body !== 'object') throw new Error('暂时未读到有效回复，请稍后重试。');
  return body;
}
