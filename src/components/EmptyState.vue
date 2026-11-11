<script setup>
defineProps({
  text: { type: String, default: "还没有合同" },
  description: { type: String, default: "" },
});
</script>
<template>
  <section class="panel empty">
    <svg class="empty-illustration" width="100" height="72" viewBox="0 0 100 72"
      fill="none" aria-hidden="true" focusable="false">
      <ellipse cx="50" cy="62" rx="34" ry="6" fill="#eef2f7" />
      <rect x="23" y="18" width="52" height="37" rx="7" fill="#edf2fa" stroke="#d3dce9" transform="rotate(-10 49 36)" />
      <rect x="29" y="11" width="47" height="47" rx="7" fill="#f7f9fc" stroke="#cbd6e5" />
      <path d="M40 26h25M40 34h19M40 42h13" stroke="#cbd6e5" stroke-width="2" stroke-linecap="round" />
      <circle cx="82" cy="45" r="4" fill="#dce6f4" />
      <circle cx="18" cy="32" r="3" fill="#e4ebf5" />
    </svg>
    <p class="empty-title">{{ text }}</p>
    <p v-if="description" class="empty-description">{{ description }}</p>
    <div v-if="$slots.action" class="empty-action"><slot name="action" /></div>
  </section>
</template>
