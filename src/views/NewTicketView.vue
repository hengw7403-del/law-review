<script setup>
import LoadingSkeleton from "../components/LoadingSkeleton.vue";
import { ref, onMounted, nextTick } from "vue";
import { useRouter } from "vue-router";
import { api } from "../api/index.js";
import Toast from "../components/Toast.vue";
const conversation = ref(null),
  messages = ref([]),
  input = ref(""),
  busy = ref(false),
  loading = ref(true),
  error = ref(""),
  candidates = ref([]),
  ticket = ref(null),
  cost = ref(null),
  feed = ref(null);
const router = useRouter();
async function openTicket(key) {
  await router.replace({ name: "ticket", params: { id: key } });
  sessionStorage.removeItem("conversation_id");
}
async function restore() {
  loading.value = true;
  error.value = "";
  try {
    let key = sessionStorage.getItem("conversation_id");
    if (key) {
      try {
        const previous = await api.conversation(key);
        if (previous.status === "active" || previous.status === "completed") {
          conversation.value = previous;
          messages.value = previous.messages;
          cost.value = previous.cost;
          candidates.value = previous.candidates || [];
          if (previous.ticket_id) await openTicket(previous.ticket_id);
          return;
        }
      } catch (e) {
        if (e.status !== 404) throw e;
      }
    }
    const started = await api.startConversation();
    key = started.conversation_id;
    sessionStorage.setItem("conversation_id", key);
    conversation.value = await api.conversation(key);
    messages.value = conversation.value.messages;
    cost.value = conversation.value.cost;
    candidates.value = conversation.value.candidates || [];
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
    await scroll();
  }
}
async function scroll() {
  await nextTick();
  feed.value?.scrollTo({ top: feed.value.scrollHeight });
}
async function send() {
  if (busy.value || !input.value.trim() || ticket.value) return;
  busy.value = true;
  error.value = "";
  const text = input.value.trim();
  try {
    const result = await api.message(conversation.value.conversation_id, text);
    messages.value.push(
      { role: "user", content: text },
      { role: "assistant", content: result.reply },
    );
    input.value = "";
    candidates.value = result.candidates;
    cost.value = result.cost;
    if (result.ticket) {
      ticket.value = result.ticket;
      await openTicket(result.ticket.id);
    }
    await scroll();
  } catch (e) {
    error.value = e.message;
    try {
      const saved = await api.conversation(conversation.value.conversation_id);
      messages.value = saved.messages;
      if (saved.ticket_id) await openTicket(saved.ticket_id);
    } catch {
      /* Keep the visible error and draft. */
    }
  } finally {
    busy.value = false;
  }
}
function choose(c) {
  input.value = `选择 ${c.title}，合同ID ${c.id}`;
}
async function newConversation() {
  if (conversation.value?.status === "active") {
    if (!window.confirm("结束当前未完成会话并重新开始？历史记录会保留。"))
      return;
    try {
      await api.abandonConversation(conversation.value.conversation_id);
    } catch (e) {
      error.value = e.message;
      return;
    }
  }
  sessionStorage.removeItem("conversation_id");
  ticket.value = null;
  conversation.value = null;
  messages.value = [];
  candidates.value = [];
  restore();
}
function keydown(event) {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    send();
  }
}
onMounted(restore);
</script>
<template>
  <header class="page-head">
    <div>
      <h1>自然语言发起工单</h1>
      <p class="muted">
        选择合同、说明关注点，确认建单后自动审查；高风险需人工确认。
      </p>
    </div>
    <router-link class="button" to="/workspace/tickets">我的工单</router-link>
    <button
      v-if="conversation?.status === 'active' && !ticket"
      :disabled="busy || loading"
      @click="newConversation"
    >
      重新开始
    </button>
  </header>
  <LoadingSkeleton v-if="loading" label="正在恢复会话" />
  <p v-if="error" class="notice error">
    {{ error }} <button @click="restore">重新加载</button>
  </p>
  <section class="panel conversation">
    <div ref="feed" class="chat-feed" aria-live="polite">
      <article
        v-for="(message, index) in messages"
        :key="index"
        class="chat-message"
        :class="message.role"
      >
        <small>{{ message.role === "user" ? "你" : "工单助手" }}</small>
        <p>{{ message.content }}</p>
      </article>
      <p v-if="busy" class="muted">助手正在整理信息 · · ·</p>
    </div>
    <div v-if="candidates.length && !ticket" class="candidate-list">
      <span class="muted">可选合同</span
      ><button
        v-for="c in candidates"
        :key="c.id"
        :disabled="busy"
        @click="choose(c)"
      >
        {{ c.title }} · {{ c.id.slice(0, 8) }}
      </button>
    </div>
    <article v-if="ticket" class="panel ticket-success">
      <h2>{{ ticket.title }}</h2>
      <p class="muted">工单号 {{ ticket.id }}</p>
      <router-link
        class="button primary"
        :to="{ name: 'ticket', params: { id: ticket.id } }"
        >查看工单</router-link
      ><button @click="newConversation">再建一个工单</button>
    </article>
    <form v-else @submit.prevent="send">
      <label for="ticket-input">你的需求</label>
      <div class="chat-compose">
        <textarea
          id="ticket-input"
          v-model="input"
          maxlength="500"
          rows="3"
          placeholder="例如：审一下技术服务合同，关注付款和解除条款"
          :disabled="busy || loading"
          @keydown="keydown"
        ></textarea
        ><button class="primary" :disabled="busy || loading || !input.trim()">
          {{ busy ? "发送中…" : "发送" }}
        </button>
      </div>
      <small>Enter 发送 · Shift+Enter 换行 · 仅支持已解析且有权限的合同</small>
    </form>
    <p v-if="cost" class="muted chat-cost">
      本会话调用 {{ cost.model_calls }} 次 ·
      {{
        cost.total_tokens === null ? "用量未知" : cost.total_tokens + " token"
      }}；上限5次 / 8000 token
    </p>
  </section>
  <Toast :message="error" />
</template>
