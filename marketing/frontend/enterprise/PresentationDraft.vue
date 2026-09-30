<script setup>
import {ref, computed, watch, onMounted, onUnmounted} from 'vue'

const props = defineProps({job: {type: Object, required: true}})
const pages = ref([]), expanded = ref(false), selected = ref(0)
const stale = ref(false), loaded = ref(false), failed = ref(false), retry = ref(0)
let timer, controller, disposed = false
const current = computed(() => pages.value[selected.value])
const source = computed(() => current.value ? `/api/presentation-drafts/${props.job.id}/previews/${current.value.number}?v=${current.value.revision}&retry=${retry.value}` : '')
watch(source, () => {loaded.value = false; failed.value = false})
async function refresh() {
  controller = new AbortController()
  try {
    const response = await fetch(`/api/presentation-drafts/${props.job.id}`, {signal: controller.signal, cache: 'no-store'})
    if (!response.ok) throw new Error('draft unavailable')
    const result = await response.json()
    if (disposed) return
    pages.value = result.pages
    selected.value = Math.max(0, Math.min(selected.value, pages.value.length - 1))
    stale.value = false
  } catch (error) {if (!disposed && error.name !== 'AbortError') stale.value = true}
  finally {
    if (!disposed && ['queued', 'running'].includes(props.job.status)) timer = setTimeout(refresh, 3000)
  }
}
onMounted(refresh)
onUnmounted(() => {disposed = true; clearTimeout(timer); controller?.abort()})
</script>

<template>
  <section class="draft-preview" aria-label="生成中的页面预览">
    <div class="draft-toolbar">
      <span v-if="pages.length">已设计 {{pages.length}} 页，可随时预览</span>
      <span v-else class="muted">首个页面设计完成后即可预览</span>
      <button v-if="pages.length" type="button" class="secondary" @click="expanded=!expanded">{{expanded ? '收起草稿预览' : `预览已设计页面（${pages.length}）`}}</button>
    </div>
    <p v-if="stale" class="muted">暂时未读到最新草稿，请稍后刷新页面。已读取的页面仍可查看。</p>
    <div v-if="expanded && current" class="draft-content">
      <p class="muted">生成中的草稿，尚未完成整套排版与视觉复核；后续可能调整或续页。</p>
      <div class="draft-image">
        <img v-show="loaded" :key="source" :src="source" :alt="`第 ${current.number} 页草稿：${current.title}`" @load="loaded=true" @error="failed=true">
        <div v-if="!loaded" class="draft-placeholder" role="status">
          <template v-if="failed">草稿预览暂时不可用 <button type="button" class="secondary" @click="retry++">重试预览</button></template>
          <template v-else>正在加载第 {{current.number}} 页草稿…</template>
        </div>
      </div>
      <div class="ppt-navigation">
        <button type="button" class="secondary" :disabled="selected===0" @click="selected--">上一页</button>
        <select v-model.number="selected" aria-label="选择已设计页面"><option v-for="(page,index) in pages" :key="page.number" :value="index">第 {{page.number}} 页 · {{page.title || '草稿'}}</option></select>
        <span class="muted">已设计 {{pages.length}} 页</span>
        <button type="button" class="secondary" :disabled="selected>=pages.length-1" @click="selected++">下一页</button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.draft-preview{margin-top:16px;padding-top:16px;border-top:1px solid #e5e7eb}
.draft-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.draft-image{position:relative;aspect-ratio:16/9;background:#eef1f5;border-radius:8px;overflow:hidden}
.draft-image img{width:100%;height:100%;object-fit:contain;display:block}
.draft-placeholder{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;gap:12px;color:#64748b}
.ppt-navigation{flex-wrap:wrap}
.ppt-navigation select{max-width:min(100%,420px);min-width:0}
</style>
