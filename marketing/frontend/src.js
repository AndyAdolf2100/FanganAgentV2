import { createApp, ref, computed, onMounted } from 'vue/dist/vue.esm-bundler.js'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import './style.css'

createApp({
  setup() {
    const health = ref({ stages: [], tags: {}, gates: {} }), runs = ref([]), run = ref(null)
    const brief = ref(''), mode = ref('auto'), tag = ref('auto'), knowledgeMode = ref('both'), knowledge = ref('')
    const busy = ref(false), error = ref(''), feedback = ref(''), tab = ref(''), events = ref([]), live = ref('')
    const statusNames = {ready:'等待执行', running:'正在执行', waiting:'等待确认', completed:'已完成', failed:'执行失败'}
    let source, generation = 0, lastMessage = ''
    async function api(path, options = {}) {
      const response = await fetch('/api' + path, {...options, headers: {'Content-Type':'application/json'}})
      if (!response.ok) {
        let body = await response.json().catch(() => ({}))
        throw new Error(typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail || response.statusText))
      }
      return response.json()
    }
    async function refreshList() { runs.value = await api('/runs') }
    async function select(id) {
      source?.close()
      const current = ++generation
      events.value = []; live.value = ''; lastMessage = ''; feedback.value = ''; error.value = ''
      const value = await api('/runs/' + id)
      if (current !== generation) return
      run.value = value
      tab.value = Object.keys(value.outputs).at(-1) || ''
      watch(id, current)
    }
    function watch(id, current = generation) {
      source?.close()
      const stream = new EventSource('/api/runs/' + id + '/events')
      source = stream
      stream.addEventListener('update', async e => {
        if (current !== generation) return
        const event = JSON.parse(e.data)
        events.value = [...events.value.slice(-79), event]
        if (event.kind === 'stage_started') { live.value = ''; lastMessage = '' }
        if (event.kind === 'delta') {
          if (lastMessage !== event.data.message_id) { lastMessage = event.data.message_id; live.value = '' }
          live.value += event.data.text
        }
        if (['stage_completed','failed','feedback','started','interrupted'].includes(event.kind)) {
          try {
            const state = await api('/runs/' + id)
            if (current !== generation) return
            run.value = state
            if (!tab.value || event.kind === 'stage_completed') tab.value = Object.keys(state.outputs).at(-1) || ''
            await refreshList()
          } catch (e) { error.value = e.message }
        }
      })
      stream.addEventListener('idle', () => stream.close())
      stream.onerror = () => { /* EventSource reconnects with Last-Event-ID. */ }
    }
    async function action(fn) {
      busy.value = true; error.value = ''
      try { await fn() } catch (e) { error.value = e.message } finally { busy.value = false }
    }
    function newRun() { source?.close(); generation++; run.value = null; tab.value = ''; events.value = []; live.value = ''; error.value = '' }
    async function submit() {
      await action(async () => {
        const value = await api('/runs', {method:'POST', body:JSON.stringify({brief:brief.value, mode:mode.value, c_tag:tag.value, knowledge_mode:knowledgeMode.value, knowledge:knowledge.value})})
        await refreshList(); await select(value.id)
      })
    }
    async function sendFeedback(type) {
      await action(async () => {
        run.value = await api('/runs/' + run.value.id + '/feedback', {method:'POST', body:JSON.stringify({action:type, text:feedback.value, target:type === 'revise' ? tab.value : null})})
        feedback.value = ''; watch(run.value.id); await refreshList()
      })
    }
    async function retry() {
      await action(async () => { run.value = await api('/runs/' + run.value.id + '/retry', {method:'POST'}); watch(run.value.id) })
    }
    async function upload(event) {
      await action(async () => {
        const file = event.target.files[0]
        if (!file) return
        if (file.size > 300000 || !/\.(txt|md)$/i.test(file.name)) throw new Error('请提供 300 KB 以内的 TXT 或 Markdown 文件')
        knowledge.value = await file.text()
      })
    }
    onMounted(() => action(async () => { health.value = await api('/health'); await refreshList() }))
    const rendered = computed(() => DOMPurify.sanitize(marked.parse(run.value?.outputs[tab.value] || '')))
    const currentTitle = computed(() => health.value.stages[run.value?.index]?.title || '方案完成')
    const canRespond = computed(() => run.value && ['waiting','completed','failed'].includes(run.value.status))
    const eventText = e => e.kind === 'tools' ? '调用工具 · ' + e.data.calls.map(c=>c.name).join('、') : e.kind === 'stage_started' ? '开始 · ' + e.data.title : ({created:'任务已创建',started:'工作流启动',stage_completed:'阶段成果已保存',feedback:'反馈已保存',failed:'执行失败',interrupted:'服务重启，可续跑',runtime:e.data.message,tool_result:'收到检索结果'})[e.kind] || ''
    return {health,runs,run,brief,mode,tag,knowledgeMode,knowledge,busy,error,feedback,tab,events,live,statusNames,rendered,currentTitle,canRespond,eventText,select,submit,newRun,sendFeedback,retry,upload,action}
  },
  template: `
  <div class="workspace">
    <aside class="sidebar">
      <div class="brand"><div class="brand-icon">M<span>↗</span></div><div>MARKETING<span class="brand-sub">V2 AGENT WORKSPACE</span></div></div>
      <button class="new-button" @click="newRun">＋ 新建营销项目</button>
      <div class="section-label">项目记录 <span>{{runs.length}}</span></div>
      <nav class="history"><button v-for="item in runs" :key="item.id" :class="{active:run?.id===item.id}" @click="action(()=>select(item.id))"><span>{{item.brief.slice(0,32)}}</span><small><i :class="item.status"></i>{{statusNames[item.status]}} · {{new Date(item.created*1000).toLocaleDateString('zh-CN')}}</small></button><p v-if="!runs.length" class="muted">你的第一个提案，从这里开始。</p></nav>
      <div class="sidebar-footer"><span class="connection"></span> 本地工作空间 <small>POWERED BY DEERFLOW 2.0</small></div>
    </aside>
    <main>
      <header><div><span class="breadcrumb">工作空间 / </span>{{run ? '项目详情' : '新建项目'}}</div><span :class="['runtime',health.runtime]">{{health.runtime==='demo'?'演示模式 · 未调用模型':'DeerFlow · 真实执行'}}</span></header>
      <div v-if="error" role="alert" class="error">{{error}}</div>
      <section v-if="!run" class="new-project">
        <div class="eyebrow">FROM BRIEF TO BIG IDEA</div>
        <h1>好方案，从一个想法开始<span>。</span></h1>
        <p class="intro">把需求交给营销 Agent。从市场洞察到创意执行，让每一步都有据可循。</p>
        <form @submit.prevent="submit" class="brief-card">
          <div class="card-heading"><h2>这次，我们要解决什么？</h2><span>01 / PROJECT BRIEF</span></div>
          <textarea v-model="brief" required minlength="5" maxlength="40000" rows="7" aria-label="营销需求" placeholder="介绍你的品牌、产品、目标人群与营销目标…&#10;&#10;例如：为一款低糖茶饮策划夏季抖音推广，面向 18–30 岁年轻人，预算 300 万元，周期两个月。希望获得市场洞察、传播主题、创意内容与达人策略。"></textarea>
          <div class="form-grid"><label>执行方式<select v-model="mode"><option value="auto">自主执行 · 连续完成提案</option><option value="guided">逐步确认 · 关键阶段由我把关</option></select></label><label>项目类型<select v-model="tag"><option value="auto">由 Agent 判断</option><option v-for="(label,key) in health.tags" :value="key">{{label}}</option></select></label><label>资料来源<select v-model="knowledgeMode"><option value="both">联网调研 + 本地材料</option><option value="web">仅联网调研</option><option value="local">仅本地材料</option></select></label></div>
          <details><summary>补充品牌资料与已有方案 <span>可选 · TXT / Markdown</span></summary><input type="file" accept=".txt,.md" @change="upload"><textarea v-model="knowledge" rows="5" aria-label="知识材料" placeholder="粘贴品牌资料、已有目录、调研结果，或上传文本文件。"></textarea></details>
          <div class="form-bottom"><p>{{health.runtime==='demo'?'当前为演示模式，用于体验完整流程。':'任务会保存进度，关闭页面后仍会继续执行。'}}</p><button type="submit" :disabled="busy">{{busy?'正在创建…':'开始策划 →'}}</button></div>
        </form>
        <div class="pipeline-preview"><div v-for="(s,i) in health.stages" :key="s.key"><span>0{{i+1}}</span>{{s.title}}</div></div>
      </section>
      <section v-else class="project-detail">
        <div class="project-top"><div><div class="eyebrow">PROJECT / {{run.id.slice(0,8).toUpperCase()}}</div><h1>{{run.brief.slice(0,44)}}</h1><p class="muted">{{run.mode==='auto'?'自主执行':'逐步确认'}} · {{health.tags[run.plan?.c_tag] || '正在分析需求'}} · {{statusNames[run.status]}}</p></div><a v-if="run.outputs.assembly" class="download" :href="'/api/runs/'+run.id+'/export'">↓ 下载方案</a></div>
        <div v-if="run.runtime==='demo'" class="demo-banner">演示任务：以下内容仅用于验证流程，未调用模型或检索服务。</div>
        <div class="stage-bar"><button v-for="(s,i) in health.stages" :key="s.key" :disabled="!run.outputs[s.key]" :class="{selected:tab===s.key,done:!!run.outputs[s.key],current:run.index===i}" @click="tab=s.key"><span>{{run.outputs[s.key]?'✓':i+1}}</span>{{s.title}}</button></div>
        <div class="detail-grid"><section class="output-card"><div class="card-heading"><h2>{{health.stages.find(s=>s.key===tab)?.title || currentTitle}}</h2><span>{{statusNames[run.status]}}</span></div><article v-if="run.outputs[tab]" class="markdown" v-html="rendered"></article><div v-else class="empty-state"><div class="orb">✦</div><h2>{{currentTitle}}</h2><p>Agent 正在处理，请在右侧查看执行进度。</p><pre v-if="live" class="live-text">{{live.slice(-5000)}}</pre></div></section>
        <aside class="execution-panel"><div class="panel-title">执行动态 <span :class="['status-dot',run.status]"></span></div><ol><li v-for="event in events.filter(e=>eventText(e)).slice(-16)" :key="event.id"><small>{{new Date(event.created*1000).toLocaleTimeString('zh-CN')}}</small>{{eventText(event)}}</li></ol><div v-if="run.status==='running'" class="working">正在执行 · {{currentTitle}}</div>
          <button v-if="run.status==='running' && live" class="secondary" @click="tab=''">查看实时生成内容</button>
          <div v-if="run.error" class="error">{{run.error}}</div><button v-if="run.status==='failed'" @click="retry" :disabled="busy">重试当前阶段</button>
          <div v-if="canRespond" class="feedback"><h3>{{health.gates[run.gate] || '修改已有成果'}}</h3><p v-if="run.gate==='theme_confirm'">填写主题编号，或描述希望采用的主题。</p><p v-else>修改将应用于当前选中的阶段，并重新生成后续内容。</p><textarea v-model="feedback" rows="4" aria-label="确认或修改意见" placeholder="主题选择、创意方向或修改要求…"></textarea><button v-if="run.status==='waiting'" @click="sendFeedback('accept')" :disabled="busy || (run.gate==='theme_confirm'&&!feedback.trim())">{{run.gate==='report_edit'?'确认最终方案':'确认并继续 →'}}</button><button class="secondary" :disabled="busy || !feedback.trim() || !tab" @click="sendFeedback('revise')">按意见修改当前阶段</button></div>
        </aside></div>
      </section>
    </main>
  </div>`
}).mount('#app')
