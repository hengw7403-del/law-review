<script setup>
defineProps({
  label: { type: String, default: '正在加载' },
  count: { type: Number, default: 2 },
});
</script>

<template>
  <div class="loading-skeleton" role="status" :aria-label="label">
    <div v-for="index in count" :key="index" class="skeleton-card" aria-hidden="true">
      <span class="skeleton-line"></span>
      <span class="skeleton-line"></span>
    </div>
  </div>
</template>
