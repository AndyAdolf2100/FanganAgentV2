<script setup>
import {computed, nextTick, onBeforeUnmount, ref, watch} from 'vue'
import {chatAuthor, chatJson, chatStatusLabels, chatTime, newChatMessageId, parseChatPages} from './presentation-chat.js'

const props = defineProps({runId: {type: String, required: true}, job: {type: Object, default: null}})
const emit = defineEmits(['updated'])
const messages = ref([]), requests = ref([]), loading = ref(false), sending = ref(false)
const pollError = ref(''), sendNotice = ref(''), history = ref(null)
const forms = new Map()
const form = ref({text: '', pages: '', error: '', pending: null})
const recentRequests = computed(() => requests.value.slice(-5))
const pendingCount = computed(() => requests.value.filter(item => ['queued', 'planning', 'planned', 'applying'].includes(item.status)).length)
const canSend = computed(() => !!props.runId && !!form.value.text.trim() && !sending.value)
let epoch = 0, loadSequence = 0, timer, getController, postController, disposed = false, lastStatus = null

function schedule() {
  clearTimeout(timer)
  if (!disposed && props.runId) timer = setTimeout(() => sending.value ? schedule() : refresh(), 3000)
}

function applySnapshot(value) {
  if (!Array.isArray(value.messages) || !Array.isArray(value.requests)) throw new Error('暂时未读到完整对话，请稍后重试。')
  const nearBottom = !history.value || history.value.scrollHeight - history.value.scrollTop - history.value.clientHeight < 80
  messages.value = value.messages.filter(item => item && typeof item.text === 'string')
  requests.value = value.requests.filter(item => item && typeof item.status === 'string')
  const status = JSON.stringify([value.active_job_id, requests.value.map(item => [item.id, item.status, item.job_id])])
  if (status !== lastStatus) {
    lastStatus = status
    emit('updated')
  }
  if (nearBottom) nextTick(() => {if (history.value) history.value.scrollTop = history.value.scrollHeight})
}

async function refresh() {
  if (disposed || !props.runId || sending.value) return
  clearTimeout(timer)
  getController?.abort()
  const controller = new AbortController(), current = epoch, sequence = ++loadSequence, runId = props.runId
  getController = controller
  loading.value = true
  const timeout = setTimeout(() => controller.abort(), 15000)
  try {
    const result = await chatJson(`/api/runs/${encodeURIComponent(runId)}/presentation-chat`, {signal: controller.signal, cache: 'no-store'})
    if (disposed || current !== epoch || sequence !== loadSequence) return
    applySnapshot(result)
    pollError.value = ''
  } catch (error) {
    if (disposed || current !== epoch || sequence !== loadSequence) return
    pollError.value = error.name === 'AbortError' ? '读取对话超时，正在等待下次刷新。' : '暂时未能刷新对话。已有消息仍可查看，你也可以继续发送。'
  } finally {
    clearTimeout(timeout)
    if (!disposed && current === epoch && sequence === loadSequence) {
      loading.value = false
      getController = null
      schedule()
    }
  }
}

async function send() {
  if (!canSend.value) return
  const draft = form.value, text = draft.text.trim()
  let pages
  try {pages = parseChatPages(draft.pages)} catch (error) {draft.error = error.message; return}
  draft.error = ''
  sendNotice.value = ''
  const signature = JSON.stringify([props.job?.id, text, pages])
  // Retain this identifier after ambiguous network errors; retrying the same
  // draft must not create a second modification request on the server.
  if (draft.pending?.signature !== signature) draft.pending = {signature, id: newChatMessageId()}
  const payload = {text, client_message_id: draft.pending.id, job_id: props.job?.id,
    ...(pages.length ? {page_numbers: pages} : {})}
  const current = epoch, runId = props.runId, controller = new AbortController()
  postController = controller
  sending.value = true
  clearTimeout(timer)
  ++loadSequence
  getController?.abort()
  getController = null
  loading.value = false
  const timeout = setTimeout(() => controller.abort(), 30000)
  try {
    const result = await chatJson(`/api/runs/${encodeURIComponent(runId)}/presentation-chat`, {
      method: 'POST', body: JSON.stringify(payload), signal: controller.signal,
    })
    if (disposed || current !== epoch) return
    draft.text = ''
    draft.pages = ''
    draft.pending = null
    sendNotice.value = '消息已发送。Agent 的修改计划和处理结果会显示在这里。'
    if (Array.isArray(result.messages) && Array.isArray(result.requests)) applySnapshot(result)
    emit('updated')
  } catch (error) {
    if (disposed || current !== epoch) return
    draft.error = error.name === 'AbortError' ? '暂未确认发送结果，请重试。输入内容已保留。' : `${error.message || '消息暂未发送成功，请重试。'} 输入内容已保留。`
  } finally {
    clearTimeout(timeout)
    if (!disposed && current === epoch) {
      sending.value = false
      postController = null
      refresh()
    }
  }
}

watch(() => props.runId, runId => {
  epoch++
  ++loadSequence
  clearTimeout(timer)
  getController?.abort()
  postController?.abort()
  getController = postController = null
  messages.value = []
  requests.value = []
  pollError.value = sendNotice.value = ''
  loading.value = sending.value = false
  lastStatus = null
  if (!forms.has(runId)) forms.set(runId, {text: '', pages: '', error: '', pending: null})
  form.value = forms.get(runId)
  refresh()
}, {immediate: true})

onBeforeUnmount(() => {
  disposed = true
  epoch++
  clearTimeout(timer)
  getController?.abort()
  postController?.abort()
})
</script>

<template>
  <section class="presentation-chat" aria-label="PPT 修改对话">
    <div class="chat-heading">
      <div><h3>和 Agent 一起优化 PPT</h3><p>生成或优化中也可以补充要求。Agent 会安排修改并回复进度，保留原文和企业品牌样式。</p></div>
      <span v-if="pendingCount" class="chat-pending">{{pendingCount}} 条要求处理中</span>
    </div>
    <p v-if="pollError" class="chat-warning" role="status">{{pollError}} <button type="button" class="secondary" :disabled="loading || sending" @click="refresh">刷新对话</button></p>
    <div ref="history" class="chat-history" role="log" aria-label="PPT 对话消息" aria-live="polite" aria-relevant="additions text">
      <p v-if="!messages.length" class="chat-empty">{{loading ? '正在读取对话…' : '例如：第 8 页的圆环放大一些，保持文字清晰；第 13 页的内容整体居中。'}}</p>
      <article v-for="(message,index) in messages" :key="message.id || index" class="chat-message" :class="{'chat-message-user': message.role === 'user', 'chat-message-system': chatAuthor(message) === '系统'}">
        <div class="chat-message-meta"><strong>{{chatAuthor(message)}}</strong><time>{{chatTime(message.created)}}</time><span v-if="message.display_label">{{message.display_label}}</span><span v-else-if="message.status && chatStatusLabels[message.status]">{{chatStatusLabels[message.status]}}</span></div>
        <p class="chat-text">{{message.text}}</p>
        <small v-if="Array.isArray(message.page_numbers) && message.page_numbers.length" class="chat-pages">涉及第 {{message.page_numbers.join('、')}} 页</small>
      </article>
    </div>
    <ul v-if="recentRequests.length" class="chat-requests" aria-label="修改要求进度">
      <li v-for="request in recentRequests" :key="request.id"><span>{{request.display_label || '修改要求'}}<span v-if="Array.isArray(request.page_numbers) && request.page_numbers.length"> · 第 {{request.page_numbers.join('、')}} 页</span></span><span :class="{'chat-attention': ['failed','needs_attention'].includes(request.status)}">{{chatStatusLabels[request.status] || '已收到'}}</span></li>
    </ul>
    <p v-if="sendNotice" class="chat-notice" role="status"><strong>系统：</strong>{{sendNotice}}</p>
    <form class="chat-form" @submit.prevent="send">
      <label>修改要求<textarea v-model="form.text" rows="3" maxlength="6000" :disabled="sending" placeholder="告诉 Agent 你希望如何调整 PPT…" /></label>
      <div class="chat-form-bottom"><label class="chat-page-input">指定页码（可选）<input v-model="form.pages" :disabled="sending" placeholder="如 8,13-15；留空由 Agent 判断" inputmode="text" /></label><button type="submit" :disabled="!canSend">{{sending ? '正在发送…' : '发送修改要求'}}</button></div>
      <p v-if="form.error" class="chat-error" role="alert">{{form.error}}</p>
    </form>
  </section>
</template>

<style scoped>
.presentation-chat{margin-top:22px;padding-top:22px;border-top:1px solid #dfe6da;color:#26392f}
.chat-heading{display:flex;gap:16px;justify-content:space-between;align-items:flex-start}.chat-heading h3{margin:0;font-size:16px}.chat-heading p{margin:7px 0 14px;font-size:12px;line-height:1.7;color:#6f7d69}.chat-pending{flex-shrink:0;padding:6px 9px;background:#edf2e6;border-radius:12px;font-size:11px;color:#466641}
.chat-history{max-height:400px;overflow:auto;border:1px solid #e3e8df;border-radius:9px;padding:16px;background:#fafbf8}.chat-empty{margin:12px 0;color:#75816f;line-height:1.8;font-size:12px}.chat-message{margin:0 0 14px;padding:12px 14px;border:1px solid #e4e9df;background:white;border-radius:8px}.chat-message:last-child{margin-bottom:0}.chat-message-user{margin-left:22px;background:#edf3e6;border-color:#dce8ce}.chat-message-system{background:transparent;border:0;padding:4px 0;color:#6c7766}.chat-message-meta{display:flex;flex-wrap:wrap;gap:10px;align-items:center;font-size:11px;color:#778271}.chat-message-meta strong{color:#31533d}.chat-message-meta time{font-variant-numeric:tabular-nums}.chat-text{margin:8px 0 0;white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;line-height:1.85}.chat-pages{display:block;margin-top:8px;color:#6f7d69;font-size:11px}
.chat-requests{list-style:none;padding:0;margin:12px 0}.chat-requests li{display:flex;gap:12px;justify-content:space-between;padding:5px 0;color:#73816c;font-size:11px}.chat-requests .chat-attention{color:#a96535}.chat-warning,.chat-notice{font-size:12px;line-height:1.8;color:#7b6d48}.chat-warning button{padding:4px 9px;margin-left:7px;font-size:11px}.chat-notice{color:#557448}.chat-form{margin-top:14px}.chat-form label{display:block;color:#687961;font-size:12px}.chat-form textarea{display:block;margin-top:8px;font-size:13px}.chat-form-bottom{display:flex;gap:14px;align-items:flex-end;margin-top:12px}.chat-page-input{flex:1;min-width:0}.chat-page-input input{display:block;width:100%;margin-top:7px;border:1px solid #dfe5dc;border-radius:6px;padding:11px 12px;background:white;color:#26392f;font-size:12px}.chat-page-input input:focus{outline:2px solid #739b5a44;border-color:#739b5a}.chat-form-bottom button{white-space:nowrap;font-size:12px}.chat-error{margin:12px 0 0;padding:10px 12px;border-radius:6px;background:#fff0e9;color:#9c4f36;font-size:12px;line-height:1.7;white-space:pre-wrap;overflow-wrap:anywhere}@media(max-width:600px){.chat-heading,.chat-form-bottom{flex-direction:column;align-items:stretch}.chat-pending{align-self:flex-start;margin-bottom:12px}.chat-message-user{margin-left:10px}.chat-history{padding:10px}.chat-form-bottom button{width:100%}}
</style>
