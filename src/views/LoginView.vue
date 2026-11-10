<script setup>
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "../api/index.js";
import { useUserStore } from "../stores/user.js";
import { safeRedirect } from "../router/index.js";
import Toast from "../components/Toast.vue";
const username = ref(""),
  password = ref(""),
  busy = ref(false),
  error = ref("");
const user = useUserStore(),
  route = useRoute(),
  router = useRouter();
async function submit() {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    user.setSession(
      await api.login({ username: username.value, password: password.value }),
    );
    password.value = "";
    await router.replace(safeRedirect(route.query.redirect));
  } catch (e) {
    error.value = e.status === 401 ? "账号或密码错误" : e.message;
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <div class="login-layout">
    <aside class="login-brand">
      <small>LEGAL REVIEW WORKSPACE</small>
      <h1>法务合同辅助初审</h1>
      <p>让每一份合同的风险与依据清晰可见。</p>
    </aside>
    <section class="login-form">
      <h1>登录工作台</h1>
      <p class="muted">管理合同，查看初审进度。</p>
      <form @submit.prevent="submit">
        <label for="username">用户名</label
        ><input
          id="username"
          v-model="username"
          autocomplete="username"
          required
          maxlength="80"
        /><label for="password">密码</label
        ><input
          id="password"
          v-model="password"
          type="password"
          autocomplete="current-password"
          required
        /><button class="primary" :disabled="busy">
          {{ busy ? "正在登录…" : "登录" }}
        </button>
      </form>
      <p v-if="error" class="notice error">{{ error }}</p>
      <Toast :message="error" /><small
        >登录状态仅保存在当前浏览器会话中。</small
      >
    </section>
  </div>
</template>
