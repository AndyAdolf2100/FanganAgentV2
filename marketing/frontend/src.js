import { createApp, ref, computed, onMounted, onUnmounted } from 'vue/dist/vue.esm-bundler.js'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import './style.css'
import './presentation.css'
import EnterpriseManager from './enterprise/Manager.vue'
import PresentationDraft from './enterprise/PresentationDraft.vue'
import PresentationChat from './enterprise/PresentationChat.vue'
import PresentationOutline from './enterprise/PresentationOutline.vue'
import {useEnterpriseTemplateStore} from './enterprise/store'
import {presentationQuality, presentationRepairProgress, presentationVisualProgress} from './enterprise/presentation-status'


createApp({
  components: {EnterpriseManager, PresentationDraft, PresentationChat, PresentationOutline},
  setup() {
    const health = ref({ stages: [], tags: {}, gates: {} }), runs = ref([]), run = ref(null)
    const brief = ref(''), mode = ref('auto'), tag = ref('auto'), knowledgeMode = ref('both'), knowledge = ref('')
    const busy = ref(false), error = ref(''), feedback = ref(''), tab = ref(''), events = ref([]), live = ref('')
    const presentation = ref(null), presentationOutline = ref(null), presentationPollError = ref(''), slideNumber = ref(1), showSlides = ref(false)
    const entryMode = ref('brief'), importStep = ref(1), manuscript = ref(''), manuscriptTitle = ref(''), manuscriptFile = ref(''), importNotice = ref('')
    const styles = ref([]), styleId = ref('auto')
    const templateStore = useEnterpriseTemplateStore(), showTemplateManager = ref(false), templateId = ref('')
    const publishedTemplates = computed(()=>templateStore.templates.filter(t=>t.published))
    async function refreshTemplates(){ await templateStore.init() }
    async function templatePublished(id){ await action(async()=>{ await refreshTemplates(); if(publishedTemplates.value.some(t=>t.id===id))templateId.value=id }) }
    function presentationOptions(){
      if(!templateId.value)return {style_id:styleId.value}
      const t=publishedTemplates.value.find(t=>t.id===templateId.value)
      if(!t)throw new Error('请先发布并选择企业模板')
      return {style_id:'auto',template_id:t.id,template_revision:t.published.revision}
    }
    const pptStages = {queued:'等待排版',pagination:'正在分页',outline_review:'请确认生成大纲',reference_analysis:'Agent读取风格参考',online_style_research:'检索在线设计案例',template_analyzing:'理解企业模板与设计边界',images:'准备配图',image_generating:'规划、生成与检查正文配图',designing:'策划结论、版式与视觉主题',editing:'精简页面文案、安排场景图',page_generating:'逐页设计与定点纠错',layout_check:'浏览器复查排版',layout_repair:'Agent修复排版问题',rendering:'排版、检查与导出',visual_review:'Agent看图复核与修版',needs_review:'草稿待复核',completed:'PPT 已完成',failed:'生成失败',interrupted:'生成中断'}
    let pptTimer, pptPollRequest = 0, pptPollFailures = 0
    const statusNames = {ready:'等待执行', running:'正在执行', waiting:'等待确认', completed:'已完成', failed:'执行失败'}
    let source, generation = 0, lastMessage = ''
    async function api(path, options = {}) {
      const response = await fetch('/api' + path, {...options, headers: {'Content-Type':'application/json', ...options.headers}})
      if (!response.ok) {
        let body = await response.json().catch(() => ({}))
        const failure = new Error(typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail || response.statusText))
        failure.status = response.status
        throw failure
      }
      return response.json()
    }
    async function refreshList() { runs.value = await api('/runs') }
    async function select(id) {
      showTemplateManager.value=false; templateId.value=''
      source?.close()
      resetPresentationPolling(); presentation.value = null; presentationOutline.value = null; slideNumber.value = 1; showSlides.value = false
      const current = ++generation
      events.value = []; live.value = ''; lastMessage = ''; feedback.value = ''; error.value = ''
      const value = await api('/runs/' + id)
      if (current !== generation) return
      run.value = value
      history.replaceState(null,'', '?project='+encodeURIComponent(id))
      styleId.value = value.presentation_style || 'auto'
      tab.value = Object.keys(value.outputs).at(-1) || ''
      watch(id, current)
      await pollPresentation(id, current)
      if (current === generation && presentation.value) { styleId.value = presentation.value.style_id || 'auto'; templateId.value=presentation.value.options?.template_id || '' }
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
    function newRun() { history.replaceState(null,'',location.pathname); showTemplateManager.value=false; templateId.value=''; source?.close(); resetPresentationPolling(); generation++; run.value = null; presentation.value = null; presentationOutline.value = null; showSlides.value = false; tab.value = ''; events.value = []; live.value = ''; error.value = ''; entryMode.value = 'brief'; importStep.value = 1; manuscript.value = ''; manuscriptTitle.value = ''; manuscriptFile.value = ''; importNotice.value = ''; styleId.value = 'auto' }
    function resetPresentationPolling() {
      clearTimeout(pptTimer); pptTimer = undefined; pptPollRequest++; pptPollFailures = 0; presentationPollError.value = ''
    }
    async function pollPresentation(id, current = generation) {
      // A stale callback must not clear a newer project's timer or errors.
      if (current !== generation || run.value?.id !== id) return
      clearTimeout(pptTimer); pptTimer = undefined
      const request = ++pptPollRequest
      const active = () => current === generation && request === pptPollRequest && run.value?.id === id
      const schedule = delay => { pptTimer = setTimeout(() => { if(active())void pollPresentation(id,current) }, delay) }
      try {
        const value = await api('/runs/' + id + '/presentation')
        if (!active()) return
        pptPollFailures = 0; presentationPollError.value = ''
        if (!presentation.value && value) { styleId.value = value.style_id || 'auto'; templateId.value = value.options?.template_id || '' }
        presentation.value = value
        if (value?.status === 'awaiting_outline_confirmation' && presentationOutline.value?.job_id !== value.id) {
          const outline = await api('/presentations/' + value.id + '/outline')
          if (!active()) return
          presentationOutline.value = outline
        }
        if (value && ['queued','running'].includes(value.status)) schedule(1500)
      } catch (failure) {
        if (!active()) return
        const retryable = !failure.status || failure.status === 408 || failure.status === 429 || failure.status >= 500
        pptPollFailures++
        if (retryable && pptPollFailures < 6) {
          const delay = Math.min(30000, 1500 * 2 ** (pptPollFailures - 1))
          presentationPollError.value = `暂时无法更新演示文稿进度，${delay / 1000} 秒后自动重试（连续失败 ${pptPollFailures}/6）。`
          schedule(delay)
        } else {
          presentationPollError.value = retryable ? '演示文稿进度连续 6 次获取失败，自动重试已暂停。请刷新页面或重新选择项目；这不会重新生成任务。' : `无法获取演示文稿进度：${failure.message}。请刷新页面或重新选择项目。`
        }
      }
    }
    async function generatePresentation() {
      const id = run.value.id, current = generation
      await action(async () => {
        const value = await api('/runs/' + id + '/presentation', {method:'POST', body:JSON.stringify(presentationOptions())})
        if(current !== generation) return
        presentation.value = value; presentationOutline.value = null; slideNumber.value = 1; showSlides.value = true
        await pollPresentation(id,current)
      })
    }
    async function confirmPresentationOutline() {
      const id=run.value.id,current=generation,jobId=presentation.value.id
      await action(async()=>{
        const job=await api('/presentations/'+jobId+'/confirm-outline',{method:'POST'})
        if(current!==generation || run.value?.id!==id)return
        presentation.value=job
        await pollPresentation(id,current)
      })
    }
    async function optimizePresentation() {
      const id=run.value.id,current=generation,jobId=presentation.value.id
      await action(async()=>{
        const job=await api('/presentations/'+jobId+'/optimize',{method:'POST'})
        if(current!==generation)return
        presentation.value=job;showSlides.value=true;slideNumber.value=1
        await pollPresentation(id,current)
      })
    }
    async function resumePresentation() {
      const id=run.value.id,current=generation,jobId=presentation.value.id
      await action(async()=>{
        const job=await api('/presentations/'+jobId+'/resume-optimization',{method:'POST'})
        if(current!==generation)return
        presentation.value=job;showSlides.value=true
        await pollPresentation(id,current)
      })
    }
    async function refreshPresentationChat() {
      if (!run.value) return
      await pollPresentation(run.value.id, generation)
    }
    const pptWorking = computed(()=>['queued','running'].includes(presentation.value?.status))
    const pptBase = computed(()=>presentation.value ? '/api/presentations/'+presentation.value.id : '')
    const pptQuality = computed(()=>presentationQuality(presentation.value))
    const pptStatusText = computed(()=>pptQuality.value.needsReview && presentation.value?.status==='completed' ? '草稿已生成，待复核' : pptQuality.value.accepted ? 'PPT 已通过验收' : pptStages[presentation.value?.stage] || '正在准备演示文稿')
    onUnmounted(()=>{source?.close();resetPresentationPolling();generation++})
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
    async function uploadManuscript(event) {
      const file = event.target.files?.[0]
      if (!file) return
      const current = generation
      await action(async () => {
        if (file.size > 5 * 1024 * 1024 || !/\.(txt|md|docx)$/i.test(file.name)) throw new Error('请上传 5 MB 以内的 Word（.docx）、Markdown 或 TXT 文件')
        const value = await api('/manuscripts/parse?filename='+encodeURIComponent(file.name), {method:'POST', headers:{'Content-Type':'application/octet-stream'}, body:file})
        if (current !== generation) return
        manuscript.value = value.text; manuscriptTitle.value = value.title; manuscriptFile.value = value.filename; importNotice.value = value.notice; importStep.value = 1
      })
      event.target.value = ''
    }
    function chooseStyle() {
      error.value = ''
      if (!manuscript.value.trim()) { error.value = '请先上传或粘贴完整文稿'; return }
      if (manuscript.value.length > 100000) { error.value = '文稿最多 10 万字'; return }
      if (!manuscriptTitle.value.trim()) manuscriptTitle.value = manuscript.value.split('\n').find(l=>l.trim()).replace(/^#+\s*/, '').slice(0,120)
      importStep.value = 2
    }
    async function submitManuscript() {
      const current = generation
      const selectedStyle = styleId.value
      const selectedOptions = presentationOptions()
      await action(async () => {
        const value = await api('/runs/import', {method:'POST', body:JSON.stringify({title:manuscriptTitle.value, manuscript:manuscript.value, filename:manuscriptFile.value, style_id:selectedStyle})})
        await refreshList()
        if (current !== generation) return
        await select(value.id)
        if (run.value?.id !== value.id) return
        const selectedGeneration = generation
        const job = await api('/runs/'+value.id+'/presentation', {method:'POST', body:JSON.stringify(selectedOptions)})
        if (selectedGeneration !== generation) return
        presentation.value = job; templateId.value=selectedOptions.template_id || ''; showSlides.value = true
        await pollPresentation(value.id, selectedGeneration)
      })
    }
    const manuscriptPreview = computed(()=>DOMPurify.sanitize(marked.parse(manuscript.value)))
    onMounted(() => action(async () => { health.value = await api('/health'); styles.value = await api('/presentation-styles'); await refreshList(); await refreshTemplates(); const project=new URLSearchParams(location.search).get('project'); if(project && runs.value.some(r=>r.id===project))await select(project) }))
    const rendered = computed(() => DOMPurify.sanitize(marked.parse(run.value?.outputs[tab.value] || '')))
    const currentTitle = computed(() => health.value.stages[run.value?.index]?.title || '方案完成')
    const canRespond = computed(() => run.value && run.value.source_type !== 'manuscript' && ['waiting','completed','failed'].includes(run.value.status))
    const eventText = e => e.kind === 'tools' ? '调用工具 · ' + e.data.calls.map(c=>c.name).join('、') : e.kind === 'stage_started' ? '开始 · ' + e.data.title : ({created:'任务已创建',manuscript_imported:'完整文稿已导入',started:'工作流启动',stage_completed:'阶段成果已保存',feedback:'反馈已保存',failed:'执行失败',interrupted:'服务重启，可续跑',runtime:e.data.message,tool_result:'收到检索结果'})[e.kind] || ''
    return {presentationRepairProgress,presentationVisualProgress,templateStore,showTemplateManager,templateId,publishedTemplates,refreshTemplates,templatePublished,health,runs,run,brief,mode,tag,knowledgeMode,knowledge,busy,error,feedback,tab,events,live,statusNames,rendered,currentTitle,canRespond,eventText,select,submit, newRun,sendFeedback,retry,upload,action,presentation,presentationOutline,presentationPollError,slideNumber,showSlides,pptStages,pptWorking,pptBase,pptQuality,pptStatusText,generatePresentation,confirmPresentationOutline,optimizePresentation,resumePresentation,refreshPresentationChat,entryMode,importStep,manuscript,manuscriptTitle,manuscriptFile,importNotice,styles,styleId,uploadManuscript,chooseStyle,submitManuscript,manuscriptPreview}
  },
  template: `
  <div class="workspace">
    <aside class="sidebar">
      <div class="brand"><div class="brand-icon">M<span>↗</span></div><div>MARKETING<span class="brand-sub">V2 AGENT WORKSPACE</span></div></div>
      <button class="new-button" @click="newRun">＋ 新建营销项目</button><button class="secondary" @click="showTemplateManager=true">企业模板管理</button>
      <div class="section-label">项目记录 <span>{{runs.length}}</span></div>
      <nav class="history"><button v-for="item in runs" :key="item.id" :class="{active:run?.id===item.id}" @click="action(()=>select(item.id))"><span>{{item.brief.slice(0,32)}}</span><small><i :class="item.status"></i>{{statusNames[item.status]}} · {{new Date(item.created*1000).toLocaleDateString('zh-CN')}}</small></button><p v-if="!runs.length" class="muted">你的第一个提案，从这里开始。</p></nav>
      <div class="sidebar-footer"><span class="connection"></span> 本地工作空间 <small>POWERED BY DEERFLOW 2.0</small></div>
    </aside>
    <main>
      <header><div><span class="breadcrumb">工作空间 / </span>{{run ? '项目详情' : '新建项目'}}</div><span :class="['runtime',health.runtime]">{{health.runtime==='demo'?'演示模式 · 未调用模型':'DeerFlow · 真实执行'}}</span></header>
      <div v-if="error" role="alert" class="error">{{error}}</div>
      <div v-if="presentationPollError && (showTemplateManager || !run?.outputs?.assembly)" role="alert" class="error">{{presentationPollError}}</div>
      <EnterpriseManager v-if="showTemplateManager" @close="showTemplateManager=false; action(refreshTemplates)" @published="templatePublished" />
      <section v-else-if="!run" class="new-project">
        <div class="eyebrow">FROM BRIEF TO BIG IDEA</div>
        <h1>{{entryMode==='manuscript'?'让完整文稿，成为好提案':'好方案，从一个想法开始'}}<span>。</span></h1>
        <p class="intro">{{entryMode==='manuscript'?'上传已有方案，选择合适的视觉风格，让内容成为清晰、有表现力的演示文稿。':'把需求交给营销 Agent。从市场洞察到创意执行，让每一步都有据可循。'}}</p>
        <div class="entry-switch" aria-label="项目创建方式"><button type="button" :class="{selected:entryMode==='brief'}" @click="entryMode='brief'" :disabled="busy">从需求开始策划<span>调研、策略、创意与完整提案</span></button><button type="button" :class="{selected:entryMode==='manuscript'}" @click="entryMode='manuscript'" :disabled="busy">上传已有文稿<span>确认文稿、选择风格、生成 PPT</span></button></div>
        <form v-if="entryMode==='brief'" @submit.prevent="submit" class="brief-card">
          <div class="card-heading"><h2>这次，我们要解决什么？</h2><span>01 / PROJECT BRIEF</span></div>
          <textarea v-model="brief" required minlength="5" maxlength="40000" rows="7" aria-label="营销需求" placeholder="介绍你的品牌、产品、目标人群与营销目标…&#10;&#10;例如：为一款低糖茶饮策划夏季抖音推广，面向 18–30 岁年轻人，预算 300 万元，周期两个月。希望获得市场洞察、传播主题、创意内容与达人策略。"></textarea>
          <div class="form-grid"><label>执行方式<select v-model="mode"><option value="auto">自主执行 · 连续完成提案</option><option value="guided">逐步确认 · 关键阶段由我把关</option></select></label><label>项目类型<select v-model="tag"><option value="auto">由 Agent 判断</option><option v-for="(label,key) in health.tags" :value="key">{{label}}</option></select></label><label>资料来源<select v-model="knowledgeMode"><option value="both">联网调研 + 本地材料</option><option value="web">仅联网调研</option><option value="local">仅本地材料</option></select></label></div>
          <details><summary>补充品牌资料与已有方案 <span>可选 · TXT / Markdown</span></summary><input type="file" accept=".txt,.md" @change="upload"><textarea v-model="knowledge" rows="5" aria-label="知识材料" placeholder="粘贴品牌资料、已有目录、调研结果，或上传文本文件。"></textarea></details>
          <div class="form-bottom"><p>{{health.runtime==='demo'?'当前为演示模式，用于体验完整流程。':'任务会保存进度，关闭页面后仍会继续执行。'}}</p><button type="submit" :disabled="busy">{{busy?'正在创建…':'开始策划 →'}}</button></div>
        </form>
        <form v-else class="brief-card manuscript-card" @submit.prevent="importStep===1 ? chooseStyle() : submitManuscript()">
          <div class="import-steps"><span :class="{active:importStep===1}">01 上传并确认文稿</span><span>→</span><span :class="{active:importStep===2}">02 选择风格</span><span>→</span><span>03 生成 PPT</span></div>
          <template v-if="importStep===1">
            <div class="card-heading"><h2>已有完整文稿？直接从这里开始</h2></div>
            <label class="manuscript-upload"><strong>{{busy?'正在读取文件…':manuscriptFile || '选择文稿文件'}}</strong><span>Word（.docx） / Markdown / TXT · 最大 5 MB</span><input type="file" accept=".docx,.md,.txt" @change="uploadManuscript" :disabled="busy" aria-label="上传完整文稿"></label>
            <p v-if="importNotice" class="import-notice">{{importNotice}}</p>
            <label class="manuscript-label">项目名称<input v-model="manuscriptTitle" maxlength="120" placeholder="例如：夏季新品传播方案" aria-label="项目名称"></label>
            <label class="manuscript-label">文稿正文 <small>{{manuscript.length.toLocaleString()}} / 100,000 字</small><textarea v-model="manuscript" rows="10" maxlength="100000" required aria-label="完整文稿" placeholder="上传后可在这里核对、修改正文，也可以直接粘贴已有文稿。"></textarea></label>
            <div class="form-bottom"><p>直接使用这份文稿进行排版，无需重新调研和写稿。</p><button type="submit" :disabled="busy || !manuscript.trim()">下一步：选择风格 →</button></div>
          </template>
          <template v-else>
            <div class="card-heading"><h2>为这份提案选择视觉风格</h2><span>{{manuscriptTitle}}</span></div>
            <div class="enterprise-choice"><label>企业模板<select v-model="templateId" :disabled="busy || pptWorking" aria-label="企业模板"><option value="">使用通用风格</option><option v-for="t in publishedTemplates" :key="t.id" :value="t.id">{{t.published.name}} · v{{t.published.revision}}</option></select></label><button type="button" class="secondary" @click="showTemplateManager=true">上传 / 管理模板</button><p v-if="templateId" class="muted">16:9 · 保留企业页眉、页脚与标题样式；正文由项目 Agent 重新排版，表格和统计图按文稿生成。</p></div>
            <div v-if="!templateId" class="style-grid" role="radiogroup" aria-label="PPT 风格">
              <label v-for="style in styles" :key="style.id" :class="['style-option',{selected:styleId===style.id}]">
                <input type="radio" name="ppt-style" :value="style.id" v-model="styleId" :disabled="busy">
                <div :class="['style-mini',style.id]" :style="{'--preview-bg':'#'+style.swatches[0],'--preview-ink':'#'+style.swatches[1],'--preview-accent':'#'+style.swatches[2]}"><span class="mini-section">BRAND / STRATEGY</span><strong>让好想法<br>清晰呈现</strong><i></i><div class="mini-rule"></div></div>
                <div class="style-meta"><strong>{{style.name}}</strong><span>{{style.description}}</span></div>
              </label>
            </div>
            <details class="import-preview"><summary>核对文稿 · {{manuscript.length.toLocaleString()}} 字</summary><article class="markdown" v-html="manuscriptPreview"></article></details>
            <div class="form-bottom"><button type="button" class="secondary" @click="importStep=1" :disabled="busy">← 返回修改文稿</button><button type="submit" :disabled="busy || !styles.length">{{busy?'正在创建…':'创建项目并生成 PPT →'}}</button></div>
          </template>
        </form>
        <div v-if="entryMode==='brief'" class="pipeline-preview"><div v-for="(s,i) in health.stages" :key="s.key"><span>0{{i+1}}</span>{{s.title}}</div></div>
      </section>
      <section v-else class="project-detail">
        <div class="project-top"><div><div class="eyebrow">PROJECT / {{run.id.slice(0,8).toUpperCase()}}</div><h1>{{run.brief.slice(0,44)}}</h1><p v-if="run.source_type==='manuscript'" class="muted">文稿导入 · {{run.source_filename || '粘贴文稿'}} · 可直接生成 PPT</p><p v-else class="muted">{{run.mode==='auto'?'自主执行':'逐步确认'}} · {{health.tags[run.plan?.c_tag] || '正在分析需求'}} · {{statusNames[run.status]}}</p></div><a v-if="run.outputs.assembly" class="download" :href="'/api/runs/'+run.id+'/export'">↓ 下载方案</a></div>
        <div v-if="run.runtime==='demo'" class="demo-banner">演示任务：以下内容仅用于验证流程，未调用模型或检索服务。</div>
        <section v-if="run.outputs.assembly" class="ppt-panel" aria-label="演示文稿">
          <div class="enterprise-choice"><label>企业模板<select v-model="templateId" :disabled="busy || pptWorking" aria-label="企业模板"><option value="">使用通用风格</option><option v-for="t in publishedTemplates" :key="t.id" :value="t.id">{{t.published.name}} · v{{t.published.revision}}</option></select></label><button type="button" class="secondary" @click="showTemplateManager=true">上传 / 管理模板</button><p v-if="templateId" class="muted">16:9 · 保留企业页眉、页脚与标题样式；正文由项目 Agent 重新排版，表格和统计图按文稿生成。</p></div>
          <div class="ppt-toolbar"><div><h2>{{pptQuality.needsReview ? '演示文稿草稿' : '演示文稿'}}</h2><p class="muted">{{presentation ? pptStatusText : '将完整方案排版为可编辑 PPT' }}<template v-if="presentation?.status==='completed'"> · {{presentation.page_count}} 页</template></p></div>
            <div class="ppt-actions"><label v-if="!templateId" class="ppt-style-select">生成风格<select v-model="styleId" :disabled="busy || pptWorking" aria-label="生成风格"><option v-for="style in styles" :value="style.id">{{style.name}}</option></select></label><button @click="generatePresentation" :disabled="busy || pptWorking || presentation?.status==='awaiting_outline_confirmation' || run.status!=='completed'">{{pptWorking?'正在生成…':presentation?.status==='awaiting_outline_confirmation'?'大纲待确认':presentation?.status==='failed'?'重新生成 PPT':'生成 PPT'}}</button><button v-if="presentation?.enterprise_workflow_version===5 && (presentation?.status==='failed' || pptQuality.needsReview)" class="secondary" @click="resumePresentation" :disabled="busy || pptWorking">{{presentation?.status==='failed'?'继续当前任务':'继续修复草稿'}}</button><template v-if="presentation?.status==='completed'"><button v-if="presentation?.options?.template_id && !pptQuality.needsReview" class="secondary" @click="optimizePresentation" :disabled="busy || pptWorking">Agent 再优化</button><button class="secondary" @click="showSlides=!showSlides">{{showSlides?'收起预览':pptQuality.needsReview?'预览草稿':'预览 PPT'}}</button><a v-if="pptQuality.pptx" class="download" :href="pptBase+'/files/presentation.pptx'">{{pptQuality.needsReview ? '下载草稿 PPTX' : '下载 PPTX'}}</a><a v-if="pptQuality.html" class="download" :href="pptBase+'/files/presentation.html'">{{pptQuality.needsReview ? '下载草稿 HTML' : '下载 HTML'}}</a></template></div>
          </div>
          <p v-if="presentationPollError" role="alert" class="error">{{presentationPollError}}</p>
          <PresentationOutline v-if="presentation?.status==='awaiting_outline_confirmation'" :outline="presentationOutline" :busy="busy" @confirm="confirmPresentationOutline" />
          <p v-if="presentation?.checks?.outline_page_count_matches===false" role="status" class="error">成品 {{presentation.page_count}} 页，与已确认大纲 {{presentation.outline_approval?.page_count}} 页不一致；已保留草稿供复核。</p>
          <p v-if="pptQuality.needsReview" role="status" class="error">当前为待复核草稿，尚未通过交付验收。可以预览已生成页面，并通过“继续修复草稿”处理未完成意见。</p>
          <p v-if="presentation?.optimization_of" class="muted">项目 Agent 正文与版式优化版本 · 保留文稿及企业主题 · <a :href="'/api/presentations/'+presentation.optimization_of+'/files/presentation.html'" target="_blank">查看优化前版本</a></p>
          <p v-if="presentation?.vision_model" class="muted">截图审查：{{presentation.vision_model}} · 每次一张成品图</p>
          <p v-if="presentation?.style_name" class="muted">当前 PPT 风格：{{presentation.style_name}}<template v-if="!pptWorking && styleId!==(presentation.style_id || 'auto')"> · 已选择新风格，点击“生成 PPT”后生效</template></p>
          <p v-if="presentation?.stage==='online_style_research'" class="muted">正在搜索演示文稿设计案例并分析视觉参考…</p>
          <p v-if="presentation?.style_research_status" class="muted">在线设计参考：{{presentation.style_reference_count || 0}} 个案例<template v-if="presentation.style_research_status==='analyzed'">，已提炼版式特点</template><template v-else-if="presentation.style_research_status==='search_only'">，已记录案例信息</template><template v-else>，检索暂不可用，已使用现有风格</template> · <a :href="pptBase+'/files/online-style-research.json'" target="_blank">查看来源</a></p>
          <p v-if="presentation?.stage==='template_analyzing' && presentation?.template_progress" class="muted">正在分析模板第 {{presentation.template_progress.template_page}} 页 · {{presentation.template_progress.current}} / {{presentation.template_progress.total}}</p>
          <p v-if="presentation?.stage==='page_generating' && presentation?.page_progress" class="muted">设计进度 {{presentation.page_progress.current}} / {{presentation.page_progress.total}}</p>
          <p v-if="presentation?.stage==='layout_repair' && presentation?.repair_progress" class="muted">{{presentationRepairProgress(presentation)}}</p>
          <p v-if="presentation?.stage==='layout_check' && presentation?.repair_progress" class="muted">正在复查 {{presentation.repair_progress.total_pages}} 页，确认排版问题是否消除</p>
          <p v-if="presentation?.stage==='visual_review' && presentation?.visual_progress" class="muted"><template v-if="presentationVisualProgress(presentation)">{{presentationVisualProgress(presentation)}}</template><template v-else-if="presentation.visual_progress.phase==='repair'">{{presentationRepairProgress(presentation)}}</template><template v-else-if="presentation.visual_progress.phase==='review'">逐页截图审查 · 已完成 {{presentation.visual_progress.current}} / {{presentation.visual_progress.total}} 页<template v-if="presentation.visual_progress.current"> · 最近完成第 {{presentation.visual_progress.page}} 页</template></template><template v-else-if="['recheck','candidate'].includes(presentation.visual_progress.phase)">修复后逐页复查 · 正在看第 {{presentation.visual_progress.page}} 页 · {{presentation.visual_progress.current}} / {{presentation.visual_progress.total}}</template><template v-else-if="presentation.visual_progress.phase==='final'">冻结版本视觉验收 · {{presentation.visual_progress.current}} / {{presentation.visual_progress.total}}</template><template v-else>逐页截图审查 · {{presentation.visual_progress.current}} / {{presentation.visual_progress.total}}</template></p>
          <p v-if="pptWorking && presentation?.progress" class="muted">已检查 {{presentation.progress.completed_pages}} / {{presentation.progress.total_pages}} 页</p>
          <p v-if="presentation?.stale" class="muted">文稿已更新，当前 PPT 对应上一版文稿，请重新生成。</p>
          <p v-if="presentation?.corrections_available" class="muted">兼容处理 {{presentation.correction_count || 0}} 项<template v-if="presentation.warning_count">，其中 {{presentation.warning_count}} 项待核验</template> · <a :href="pptBase+'/files/corrections.md'">下载改动位置日志</a></p>
          <p v-if="presentation?.visual_notice" class="muted">{{presentation.visual_notice}} <a :href="'/api/presentations/'+presentation.id+'/files/visual-review.json'" target="_blank">视觉复核报告</a></p>
          <p v-if="presentation?.agent_owner" class="muted">项目 Agent 自动执行 · <a :href="pptBase+'/files/agent-run.json'" target="_blank">执行记录</a></p>
          <p v-if="presentation?.image_notice" class="muted">{{presentation.image_notice}}</p>
          <p v-else-if="presentation?.image_count" class="muted">已使用 {{presentation.image_count}} 张项目配图 · 概念图片不作为产品参数或市场数据依据</p>
          <div v-if="presentation?.error" class="error">{{presentation.error}}</div>
          <p v-if="presentation?.status==='failed' && presentation?.enterprise_workflow_version===5" class="muted">任务暂未完成。可以先在下方与项目 Agent 对话；需要补齐页面并验收时，点击“继续当前任务”。</p>
          <PresentationDraft v-if="presentation?.options?.template_id && !['completed','awaiting_outline_confirmation'].includes(presentation?.status)" :key="presentation.id" :job="presentation" />
          <PresentationChat v-if="presentation?.options?.template_id && presentation?.enterprise_workflow_version===5 && presentation?.status!=='awaiting_outline_confirmation'" :key="run.id" :run-id="run.id" :job="presentation" @updated="refreshPresentationChat" />
          <div v-if="showSlides && presentation?.status==='completed'" class="ppt-viewer"><img :src="pptBase+'/previews/'+slideNumber" :alt="'第'+slideNumber+'页幻灯片'"><div class="ppt-navigation"><button class="secondary" @click="slideNumber--" :disabled="slideNumber<=1">上一页</button><label>第 <input type="number" v-model.number="slideNumber" min="1" :max="presentation.page_count" @change="slideNumber=Math.min(presentation.page_count,Math.max(1,Number(slideNumber)||1))"> / {{presentation.page_count}} 页</label><button class="secondary" @click="slideNumber++" :disabled="slideNumber>=presentation.page_count">下一页</button></div></div>
        </section>
        <div v-if="run.source_type!=='manuscript'" class="stage-bar"><button v-for="(s,i) in health.stages" :key="s.key" :disabled="!run.outputs[s.key]" :class="{selected:tab===s.key,done:!!run.outputs[s.key],current:run.index===i}" @click="tab=s.key"><span>{{run.outputs[s.key]?'✓':i+1}}</span>{{s.title}}</button></div>
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
