<script setup>
import LoadingSkeleton from "../components/LoadingSkeleton.vue";
import { ref, onMounted } from 'vue';
import { api } from '../api/index.js';
import { useUserStore } from '../stores/user.js';
import StatusBadge from '../components/StatusBadge.vue';
import EmptyState from '../components/EmptyState.vue';
import AppIcon from '../components/AppIcon.vue';
import Toast from '../components/Toast.vue';
import { formatTime, ticketNumber } from '../utils/format.js';
const user = useUserStore(), data = ref(null), error = ref(''), loading = ref(true);
const metrics = [
  { key: 'contracts', label: '合同', icon: 'document' },
  { key: 'tickets', label: '工单', icon: 'tickets' },
  { key: 'tickets_needs_human', label: '待人工确认', icon: 'pending' },
  { key: 'high_risk_total', label: '工单高风险', icon: 'risk' },
];
async function load() {
  loading.value = true; error.value = '';
  try { data.value = await api.dashboard(); }
  catch (e) { error.value = e.message; }
  finally { loading.value = false; }
}
onMounted(load);
</script>
<template>
  <header class="page-head"><div><h1>{{ user.role === 'requester' ? '我的提交' : '概览' }}</h1><p class="muted">{{ user.role === 'requester' ? '查看你的合同审查进度和结果。' : '合同审查与工单处理工作台。' }}</p></div><div class="actions"><button :disabled="loading" @click="load">刷新</button><router-link class="button primary" to="/workspace/new-ticket">发起工单</router-link></div></header>
  <LoadingSkeleton v-if="loading" label="正在加载" />
  <p v-if="error" class="notice error">{{ error }} <button @click="load">重试</button></p>
  <template v-if="data">
    <template v-if="user.role === 'admin'">
      <section class="stats dashboard-stats">
        <div v-for="metric in metrics" :key="metric.key" class="panel metric-card" :class="metric.icon">
          <span class="metric-icon"><AppIcon :name="metric.icon" /></span>
          <div><strong>{{ data[metric.key] }}</strong><small>{{ metric.label }}</small></div>
        </div>
      </section>
      <section class="panel"><h2>最近活动</h2><EmptyState v-if="!data.recent_activity.length" text="还没有活动" /><router-link v-for="activity in data.recent_activity" :key="activity.kind + activity.id" class="activity-row" :to="activity.kind === 'ticket' ? '/workspace/tickets/' + activity.id : '/workspace/contracts/' + activity.id"><span>{{ activity.kind === 'ticket' ? '工单' : '合同' }} · {{ activity.title }}</span><small>{{ formatTime(activity.created_at) }}</small></router-link></section>
    </template>
    <template v-else-if="user.role === 'reviewer'">
      <section class="panel attention-card"><small>待人工确认</small><h2>待你处理 <strong>{{ data.tickets_needs_human }}</strong> 件</h2><p class="muted">核对高风险结论与依据，确认后完成工单。</p></section>
      <EmptyState v-if="!data.needs_human.length" text="当前没有待人工确认的工单" />
      <router-link v-for="ticket in data.needs_human" :key="ticket.id" class="panel ticket-card needs-human" :to="'/workspace/tickets/' + ticket.id"><div class="page-head"><h2>{{ ticket.title }}</h2><StatusBadge :status="ticket.status" /></div><p class="muted">{{ ticketNumber(ticket.id) }} · {{ formatTime(ticket.created_at) }} · 高风险 {{ ticket.high_risk_count }} 处</p></router-link>
      <p class="muted">今天已完成 {{ data.today_done }} 件</p>
    </template>
    <template v-else>
      <EmptyState v-if="!data.submissions.length" text="还没有提交" description="发起第一份工单，在这里查看审查进度。">
        <template #action><router-link class="button primary" to="/workspace/new-ticket">发起工单</router-link></template>
      </EmptyState>
      <article v-for="ticket in data.submissions" :key="ticket.id" class="panel ticket-card">
        <div class="page-head"><router-link :to="'/workspace/tickets/' + ticket.id"><h2>{{ ticket.title }}</h2></router-link><StatusBadge :status="ticket.status" /></div>
        <p class="muted">{{ ticketNumber(ticket.id) }} · {{ formatTime(ticket.created_at) }}</p>
        <template v-if="ticket.total_items"><progress :value="ticket.done_items" :max="ticket.total_items"></progress><p class="muted">进度 {{ ticket.done_items }}/{{ ticket.total_items }}</p></template>
        <p v-else class="muted">等待启动审查</p>
        <router-link v-if="ticket.review_task_id && ['completed','partial_failed'].includes(ticket.task_status)" class="button" :to="'/workspace/results/' + ticket.review_task_id">查看结果</router-link>
      </article>
    </template>
  </template>
  <Toast :message="error" />
</template>
