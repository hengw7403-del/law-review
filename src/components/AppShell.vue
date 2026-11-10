<script setup>
import { useRouter } from "vue-router";
import { useUserStore } from "../stores/user.js";
const router = useRouter(),
  user = useUserStore();
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
      <router-link to="/workspace/contracts">合同列表</router-link
      ><span class="muted">{{ user.role }}</span
      ><button @click="logout">退出</button>
    </div>
  </nav>
  <main><router-view :key="$route.fullPath" /></main>
</template>
