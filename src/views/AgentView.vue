<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue';
import { useRoute } from 'vue-router';
import { api } from '../api/index.js';
import Toast from '../components/Toast.vue';
const input = ref(''), run = ref(null), busy = ref(false), error = ref('');
const route = useRoute();
const running = computed(() => busy.value || run.value?.status === 'running');
const labels = { list_contracts: '列出合同', get_contract: '获取合同', review_contract: '审查合同', write_result: '写回 OA' };
let timer, alive = true;
async function poll(key) {
  clearTimeout(timer);
  try {
    const value = await api.agentRun(key);
    if (!alive) return;
    run.value = value; error.value = '';
    sessionStorage.setItem('agent_run_id', value.run_id);
    if (value.status === 'running') timer = setTimeout(() => poll(key), 2000);
  } catch (e) { if (alive) error.value = e.message; }
}
async function start() {
  if (running.value || !input.value.trim()) return;
  busy.value = true; error.value = '';
  try {
    const response = await api.startAgent(input.value.trim());
    if (!alive) return;
    sessionStorage.setItem('agent_run_id', response.run_id);
    run.value = { run_id: response.run_id, status: 'running', steps: [] };
    await poll(response.run_id);
  } catch (e) { if (alive) error.value = e.message; }
  finally { busy.value = false; }
}
onMounted(() => { const key = route.query.run || sessionStorage.getItem('agent_run_id'); if (key) poll(String(key)); });
onUnmounted(() => { alive = false; clearTimeout(timer); });
</script>
<template>
  <header class="page-head"><div><h1>AI 助手</h1><p class="muted">从外部 OA 获取合同，调用本系统审查，再将真实结果写回。</p></div></header>
  <section class="panel">
    <form class="agent-form" @submit.prevent="start"><label for="agent-request">你希望做什么？</label><textarea id="agent-request" v-model="input" rows="3" maxlength="2000" :disabled="running" required placeholder="例如：审一下 C001，我方是乙方"></textarea>
      <div class="actions agent-examples"><button v-for="example in ['把 OA 里的合同都审一遍', '看看 OA 里有哪些合同']" :key="example" type="button" :disabled="running" @click="input = example">{{ example }}</button></div>
      <p class="muted">未知合同数时限 10 步；列出 N 份后按 2 + 3 × N 计算，最多 40 步。规划限 50,000 tokens，规划与审查合计限 200 次模型调用。</p>
      <button class="primary" :disabled="running || !input.trim()">{{ running ? '正在执行 · · ·' : '开始执行' }}</button>
    </form>
  </section>
  <p v-if="error" class="notice error" role="alert">{{ error }} <button v-if="run?.run_id" @click="poll(run.run_id)">重新获取进度</button></p>
  <section v-if="run" class="panel" aria-live="polite">
    <header class="page-head"><h2>执行过程</h2><span class="badge">{{ { running: '执行中', completed: '已完成', failed: '未完成' }[run.status] }}</span></header>
    <p v-if="run.request" class="muted">{{ run.request }}</p>
    <p v-if="run.rounds_limit != null" class="muted">已用 {{ run.rounds_used }}/{{ run.rounds_limit }} 步 · 已完成 {{ run.contracts_processed }}/{{ run.contracts_total ?? '待确定' }} 份合同</p>
    <p v-if="!run.steps.length && run.status === 'running'" class="loading">正在连接 OA 并规划 · · ·</p>
    <ol class="agent-timeline"><li v-for="step in run.steps" :key="step.round" :class="step.status"><strong>第 {{ step.round }} 步：{{ labels[step.tool] || step.tool }} <span v-if="step.args?.contract_id">{{ step.args.contract_id }}</span></strong><p>{{ step.result_summary }}</p><details v-if="Object.keys(step.args || {}).length"><summary>参数</summary><pre>{{ JSON.stringify(step.args, null, 2) }}</pre></details></li></ol>
    <p v-if="run.status === 'running' && run.progress?.tool === 'review_contract'" class="muted">worker：{{ run.progress.contract_id }} · {{ run.progress.processed_items }}/{{ run.progress.total_items }}</p>
    <p v-if="run.summary" class="agent-summary">{{ run.summary }}</p>
    <div v-if="run.cost" class="agent-cost muted"><p>Agent 规划：{{ run.cost.planning.model_calls }} 次 · {{ run.cost.planning.total_tokens ?? '用量待核对' }} tokens</p><p>合同审查：{{ run.cost.reviews.model_calls }} 次 · {{ run.cost.reviews.total_tokens ?? '用量待核对' }} tokens</p><p>合计 {{ run.cost.total_model_calls }}/200 次模型调用</p></div>
    <div class="actions"><router-link v-for="item in run.details || []" :key="item.task_id" class="button" :to="item.result_path">查看 {{ item.oa_contract_id }} 详情</router-link></div>
  </section>
  <Toast :message="error" />
</template>
