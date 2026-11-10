<script setup>
defineProps({ text: { type: String, default: "还没有合同，点右上角上传" } });
</script>
<template>
  <section class="panel empty">
    <div class="empty-icon" aria-hidden="true"></div>
    <p>{{ text }}</p>
  </section>
</template>
