<script setup>
import { ref, onMounted, onUnmounted, computed } from "vue";
import { useRoute } from "vue-router";
import { api } from "../api/index.js";
import StatusBadge from "../components/StatusBadge.vue";
import Toast from "../components/Toast.vue";
import KnowledgeWarning from "../components/KnowledgeWarning.vue";
import { formatTime, ticketNumber, focusLabel } from "../utils/format.js";
const id = useRoute().params.id,
  ticket = ref(null),
  task = ref(null),
  me = ref(null),
  error = ref(""),
  busy = ref(false),
  loading = ref(true);
const confirmationNote = ref(""),
  success = ref("");
let timer,
  alive = true;
const canOperate = computed(
  () =>
    me.value &&
    ['admin', 'reviewer'].includes(me.value.role),
);
const canClose = computed(
  () =>
    me.value &&
    (['admin', 'reviewer'].includes(me.value.role) || ticket.value?.requester_id === me.value.user_id),
);
async function poll() {
  clearTimeout(timer);
  if (!alive || !ticket.value?.review_task_id) return;
  try {
    const value = await api.task(ticket.value.review_task_id);
    if (!alive) return;
    task.value = value;
    if (["queued", "running"].includes(value.status))
      timer = setTimeout(poll, 2000);
    else ticket.value = await api.ticket(id);
  } catch (e) {
    if (alive) error.value = e.message;
  }
}
async function load() {
  loading.value = true;
  error.value = "";
  try {
    const [record, user] = await Promise.all([api.ticket(id), api.me()]);
    if (!alive) return;
    ticket.value = record;
    me.value = user;
    await poll();
  } catch (e) {
    if (alive) error.value = e.message;
  } finally {
    if (alive) loading.value = false;
  }
}
async function action(kind) {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    await {
      confirm: (key) => api.confirmTicket(key, confirmationNote.value),
      review: api.reviewTicket,
      close: api.closeTicket,
    }[kind](id);
    await load();
    if (kind === "confirm") success.value = "已人工确认通过。";
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
onMounted(load);
onUnmounted(() => {
  alive = false;
  clearTimeout(timer);
});
</script>
<template>
  <router-link to="/workspace/tickets">← 返回工单列表</router-link>
  <p v-if="loading" class="loading">正在读取 · · ·</p>
  <p v-if="error" class="notice error">
    {{ error }} <button @click="load">重试</button>
  </p>
  <KnowledgeWarning :show="!!task?.all_clauses_insufficient" />
  <template v-if="ticket"
    ><section class="panel sticky">
      <header class="page-head">
        <h1>{{ ticket.title }}</h1>
        <StatusBadge :status="ticket.status" />
      </header>
      <p class="muted">
        {{ ticketNumber(ticket.id) }} · {{ formatTime(ticket.created_at) }}
      </p>
      <p>发起人：{{ ticket.requester_name }}</p>
      <p>
        关注点：{{ focusLabel(ticket.focus_points) }} · 优先级：{{
          { low: "低", normal: "普通", urgent: "紧急" }[ticket.priority]
        }}
      </p>
      <p v-if="ticket.cost" class="muted">
        成本：会话 {{ ticket.cost.conversation.model_calls }} 次 /
        {{ ticket.cost.conversation.total_tokens ?? "未知" }} token · 审查
        {{ ticket.cost.review.model_calls }} 次 /
        {{ ticket.cost.review.total_tokens ?? "未知" }} token · 合计
        {{ ticket.cost.total_tokens ?? "未知" }} token
      </p>
      <div class="actions">
        <button
          v-if="ticket.status === 'pending' && canOperate"
          :disabled="busy"
          @click="action('review')"
        >
          重试自动审查</button
        ><template
          v-if="task && ['completed', 'partial_failed'].includes(task.status)"
          ><router-link
            class="button"
            :to="{ name: 'result', params: { id: ticket.review_task_id } }"
            >查看审查结果</router-link
          ><router-link
            class="button"
            :to="{ name: 'report', params: { id: ticket.review_task_id } }"
            >查看报告</router-link
          ></template
        ><button
          v-if="
            canClose &&
            !['closed', 'reviewing', 'needs_human'].includes(ticket.status)
          "
          :disabled="busy"
          @click="action('close')"
        >
          关闭工单
        </button>
      </div>
      <p v-if="ticket.review_error" class="notice error">
        {{ ticket.review_error }}
      </p>
      <p
        v-if="ticket.status === 'reviewing' && !ticket.review_task_id"
        class="muted"
      >
        旧工单审查关联待恢复，请管理员核对。
      </p>
      <p v-if="success" class="notice">{{ success }}</p>
      <p v-if="ticket.confirmed_by" class="muted">
        已人工确认 · {{ formatTime(ticket.confirmed_at) }} ·
        {{ ticket.confirmation_note }}
      </p>
    </section>
    <section
      v-if="ticket.status === 'needs_human'"
      class="panel human-warning"
      role="status"
    >
      <h2>
        发现 {{ ticket.high_risk_count }} 处高风险，需要人工确认后才能关闭
      </h2>
      <p>{{ ticket.needs_human_reason }}</p>
      <template v-if="canOperate"><label for="confirm-note">确认备注（可选）</label
      ><textarea
        id="confirm-note"
        v-model="confirmationNote"
        maxlength="2000"
        rows="2"
      ></textarea></template>
      <div class="actions">
        <button
          v-if="canOperate && ['reviewer', 'admin'].includes(me?.role)"
          class="primary"
          :disabled="busy"
          @click="action('confirm')"
        >
          确认通过</button
        ><router-link
          class="button"
          :to="{ name: 'result', params: { id: ticket.review_task_id } }"
          >查看审查结果</router-link
        >
      </div>
    </section>
    <section v-if="task" class="panel">
      <h2>审查进度</h2>
      <StatusBadge :status="task.status" /><progress
        max="1"
        :value="task.progress"
      ></progress>
      <p class="muted">
        已处理 {{ task.processed_items }}/{{ task.total_items }} · 失败
        {{ task.failed_items }}
      </p>
      <p v-if="task.error_message" class="notice error">
        {{ task.error_message }}
      </p>
      <button v-if="['queued', 'running'].includes(task.status)" @click="poll">
        重新获取进度
      </button>
    </section>
    <section class="panel">
      <h2>原始需求</h2>
      <p class="pre-wrap">{{ ticket.raw_request }}</p>
    </section>
    <section class="panel">
      <h2>对话记录</h2>
      <article
        v-for="(message, index) in ticket.messages"
        :key="index"
        class="chat-message"
        :class="message.role"
      >
        <small>{{ message.role === "user" ? "发起人" : "工单助手" }}</small>
        <p>{{ message.content }}</p>
      </article>
      <p v-if="!ticket.messages.length" class="muted">尚无可查看的对话记录。</p>
    </section></template
  ><Toast :message="error" />
</template>
