<script setup>
defineProps({ show: Boolean });
import { useUserStore } from "../stores/user.js";
const user = useUserStore();
</script>
<template>
  <aside v-if="show" class="knowledge-warning" role="alert">
    知识库为空或未命中，本次审查未能给出有效依据。<br />请先导入法规后重新审查。
    <router-link v-if="user.role === 'admin'" to="/workspace/knowledge">去导入</router-link>
    <span v-else>请联系管理员导入法规。</span>
  </aside>
</template>
