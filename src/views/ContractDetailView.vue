<script setup>
import { ref, onMounted, onUnmounted } from "vue";
import { useRoute } from "vue-router";
import { api } from "../api/index.js";
import StatusBadge from "../components/StatusBadge.vue";
import Toast from "../components/Toast.vue";
const id = useRoute().params.id,
  contract = ref(null),
  clauses = ref([]),
  task = ref(null),
  error = ref(""),
  busy = ref(false),
  page = ref(1),
  pages = ref(1),
  loading = ref(true);
let timer,
  alive = true;
const terminal = ["completed", "partial_failed", "failed"];
async function loadClauses() {
  try {
    const data = await api.clauses(id, page.value);
    if (!alive) return;
    clauses.value = data.clauses;
    pages.value = Math.max(1, Math.ceil(data.total / data.page_size));
  } catch (e) {
    if (alive) error.value = e.message;
  }
}
async function poll(taskId) {
  clearTimeout(timer);
  try {
    const data = await api.task(taskId);
    if (!alive) return;
    task.value = data;
    if (!terminal.includes(data.status))
      timer = setTimeout(() => poll(taskId), 2000);
  } catch (e) {
    if (alive) error.value = e.message;
  }
}
async function load() {
  loading.value = true;
  error.value = "";
  clearTimeout(timer);
  try {
    const data = await api.contract(id);
    if (!alive) return;
    contract.value = data;
    await loadClauses();
    if (data.latest_task) await poll(data.latest_task.task_id);
  } catch (e) {
    if (alive) error.value = e.message;
  } finally {
    if (alive) loading.value = false;
  }
}
async function review() {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    const data = await api.review(id);
    await poll(data.task_id);
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function changePage(delta) {
  page.value += delta;
  await loadClauses();
}
onMounted(load);
onUnmounted(() => {
  alive = false;
  clearTimeout(timer);
});
</script>
<template>
  <router-link to="/workspace/contracts">← 返回合同列表</router-link>
  <p v-if="loading" class="loading">正在加载 · · ·</p>
  <p v-if="error" class="notice error">
    {{ error }} <button @click="load">重试</button>
  </p>
  <section v-if="contract" class="panel sticky">
    <header class="page-head">
      <h1>{{ contract.title }}</h1>
      <StatusBadge :status="task?.status || contract.parse_status" />
    </header>
    <p class="muted">
      {{ contract.party_stance === "party_a" ? "甲方" : "乙方" }} ·
      {{ contract.clause_count }} 条 · v{{ contract.version }}
    </p>
    <StatusBadge :status="contract.parse_status" />
    <p v-if="contract.parse_status === 'parse_failed'" class="notice error">
      解析失败：{{ contract.parse_error }}
      <router-link to="/workspace/contracts">重新上传</router-link>
    </p>
    <p v-if="contract.parse_warnings?.length" class="notice">
      解析警告：{{ contract.parse_warnings.join("、") }}，请人工核对。
    </p>
    <div class="actions">
      <button
        v-if="contract.parse_status === 'parsed'"
        class="primary"
        :disabled="busy || (task && !terminal.includes(task.status))"
        @click="review"
      >
        {{ busy ? "正在提交…" : "发起审查" }}</button
      ><template
        v-if="task && ['completed', 'partial_failed'].includes(task.status)"
        ><router-link
          class="button"
          :to="{ name: 'result', params: { id: task.task_id } }"
          >查看结果</router-link
        ><router-link
          class="button"
          :to="{ name: 'report', params: { id: task.task_id } }"
          >查看报告</router-link
        ></template
      >
    </div>
  </section>
  <section v-if="task" class="panel">
    <h2>审查进度</h2>
    <p v-if="task.status === 'running'">
      正在审查（已完成 {{ task.executed_items }}/{{
        task.planned_model_items
      }}）
    </p>
    <p v-else-if="task.status === 'partial_failed'">
      部分完成（{{ task.failed_items }} 项失败）
    </p>
    <StatusBadge v-else :status="task.status" /><progress
      max="1"
      :value="task.progress"
    ></progress>
    <p class="muted">
      已处理 {{ task.processed_items }}/{{ task.total_items }} · 不适用
      {{ task.not_applicable_items }}
    </p>
    <p v-if="task.error_message" class="notice error">
      {{ task.error_message }}
    </p>
    <button v-if="!terminal.includes(task.status)" @click="poll(task.task_id)">
      重新获取进度
    </button>
  </section>
  <section v-if="contract" class="panel">
    <h2>合同条款</h2>
    <article
      v-for="clause in clauses"
      :key="clause.clause_id || clause.seq"
      class="clause"
    >
      <h3>{{ clause.original_no }}</h3>
      <p class="pre-wrap">{{ clause.content }}</p>
    </article>
    <div class="actions">
      <button :disabled="page <= 1" @click="changePage(-1)">上一页</button
      ><small>{{ page }} / {{ pages }}</small
      ><button :disabled="page >= pages" @click="changePage(1)">下一页</button>
    </div>
  </section>
  <Toast :message="error" />
</template>
