<script setup>
defineProps({ show: Boolean });
</script>
<template>
  <aside v-if="show" class="knowledge-warning" role="alert">
    知识库为空或未命中，本次审查未能给出有效依据。<br />请先导入法规后重新审查。
    <router-link to="/workspace/knowledge-help">去导入</router-link>
  </aside>
</template>
