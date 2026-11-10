<script setup>
import { watch, ref, onUnmounted } from "vue";
const props = defineProps({ message: { type: String, default: "" }, variant: { type: String, default: "error" } });
const visible = ref(false);
let timer;
watch(
  () => props.message,
  (value) => {
    clearTimeout(timer);
    visible.value = Boolean(value);
    timer = setTimeout(() => (visible.value = false), 3000);
  },
  { immediate: true },
);
onUnmounted(() => clearTimeout(timer));
</script>
<template>
  <p v-if="visible" class="notice toast" :class="{ error: variant === 'error' }" :role="variant === 'error' ? 'alert' : 'status'">{{ message }}</p>
</template>
