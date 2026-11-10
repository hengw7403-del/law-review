<script setup>
import { ref, onMounted } from "vue";
import { api } from "../api/index.js";
import StatusBadge from "../components/StatusBadge.vue";
import EmptyState from "../components/EmptyState.vue";
import Toast from "../components/Toast.vue";
import { useUserStore } from "../stores/user.js";
const user = useUserStore();
import { formatTime, ticketNumber, focusLabel } from "../utils/format.js";
const tickets = ref([]),
  status = ref(""),
  loading = ref(true),
  error = ref("");
let sequence = 0;
async function load() {
  const current = ++sequence;
  loading.value = true;
  error.value = "";
  try {
    const data = await api.tickets(status.value);
    if (current === sequence) tickets.value = data.tickets;
  } catch (e) {
    if (current === sequence) error.value = e.message;
  } finally {
    if (current === sequence) loading.value = false;
  }
}
onMounted(load);
</script>
<template>
  <header class="page-head">
    <div>
      <h1>{{ user.role === 'requester' ? '我的提交' : '工单' }}</h1>
      <p class="muted">自动审查进度与待人工确认的高风险工单。</p>
    </div>
    <router-link class="button primary" to="/workspace/new-ticket"
      >发起工单</router-link
    >
  </header>
  <div class="actions ticket-filters" role="group" aria-label="工单筛选">
    <button
      v-for="[value, label] in [
        ['', '全部'],
        ['reviewing', '审查中'],
        ['needs_human', '待人工确认'],
        ['done', '已完成'],
      ]"
      :key="value"
      :class="{ primary: status === value }"
      :aria-pressed="status === value"
      @click="
        status = value;
        load();
      "
    >
      {{ label }}
    </button>
  </div>
  <p v-if="loading" class="loading">正在读取工单 · · ·</p>
  <p v-if="error" class="notice error">
    {{ error }} <button @click="load">重试</button>
  </p>
  <EmptyState
    v-if="!loading && !tickets.length && !error"
    text="当前筛选下还没有工单"
  /><router-link
    v-for="ticket in tickets"
    :key="ticket.id"
    class="panel ticket-card"
    :class="{ 'needs-human': ticket.status === 'needs_human' }"
    :to="{ name: 'ticket', params: { id: ticket.id } }"
    ><div class="page-head">
      <h2>{{ ticket.title }}</h2>
      <StatusBadge :status="ticket.status" />
    </div>
    <p class="muted">
      {{ ticketNumber(ticket.id) }} · {{ formatTime(ticket.created_at) }} ·
      发起人 {{ ticket.requester_name }} · 优先级
      {{ { low: "低", normal: "普通", urgent: "紧急" }[ticket.priority] }}
    </p>
    <p>关注点：{{ focusLabel(ticket.focus_points) }}</p></router-link
  ><Toast :message="error" />
</template>
