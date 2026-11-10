<script setup>
import { useRouter } from "vue-router";
import { useUserStore } from "../stores/user.js";
import { ref } from "vue";
import { api } from "../api/index.js";
import Toast from "./Toast.vue";
const router = useRouter(),
  user = useUserStore();
const settingsOpen = ref(false), orgName = ref(""), saving = ref(false), settingsError = ref(""), notice = ref("");
async function openSettings() {
  settingsOpen.value = true;
  settingsError.value = "";
  saving.value = true;
  try {
    const profile = await api.me();
    user.setProfile(profile);
    orgName.value = profile.org_name || "";
  } catch (e) { settingsError.value = e.message; }
  finally { saving.value = false; }
}
async function saveSettings() {
  if (saving.value) return;
  saving.value = true;
  settingsError.value = "";
  try {
    user.setProfile(await api.updateOrganization(orgName.value.trim() || null));
    settingsOpen.value = false;
    notice.value = "组织设置已保存";
  } catch (e) { settingsError.value = e.message; }
  finally { saving.value = false; }
}
function logout() {
  user.logout();
  router.replace({ name: "login" });
}
</script>
<template>
  <nav>
    <router-link class="brand" to="/workspace/contracts"
      >合同辅助初审</router-link
    >
    <div class="actions">
      <router-link to="/workspace/new-ticket">发起工单</router-link>
      <router-link to="/workspace/tickets">我的工单</router-link>
      <router-link to="/workspace/contracts">合同列表</router-link
      >
      <details class="user-menu">
        <summary>{{ user.role }} · 用户菜单</summary>
        <div><button @click="openSettings">设置我的组织</button><button @click="logout">退出</button></div>
      </details>
    </div>
  </nav>
  <main><router-view :key="$route.fullPath" /></main>
  <div v-if="settingsOpen" class="modal" @click.self="!saving && (settingsOpen = false)">
    <section class="panel" role="dialog" aria-modal="true" aria-label="设置我的组织">
      <header class="page-head"><h2>设置我的组织</h2><button :disabled="saving" @click="settingsOpen = false">关闭</button></header>
      <p class="muted">企业填写公司名称；律所填写本次委托人的名称。仅用于推荐立场，每次上传仍需确认。</p>
      <form @submit.prevent="saveSettings">
        <label for="org-name">我的组织名称（可留空）</label>
        <input id="org-name" v-model="orgName" maxlength="200" :disabled="saving" placeholder="示例星桥科技有限公司" />
        <p v-if="settingsError" class="notice error" role="alert">{{ settingsError }}</p>
        <button class="primary" :disabled="saving">{{ saving ? "正在处理…" : "保存" }}</button>
      </form>
    </section>
  </div>
  <Toast :message="notice" variant="info" />
</template>
