<script setup>
import { watch, ref, onUnmounted } from "vue";
const props = defineProps({ message: { type: String, default: "" } });
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
  <p v-if="visible" class="notice error toast" role="alert">{{ message }}</p>
</template>
