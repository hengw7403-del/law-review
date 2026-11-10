<script setup>
import { computed } from "vue";
const props = defineProps({ stats: { type: Object, required: true } });
const segments = computed(() => [
  ["high", props.stats.high],
  ["medium", props.stats.medium],
  ["low", props.stats.low],
  [
    "other",
    ["insufficient", "no_issue", "not_applicable", "failed", "merged"].reduce(
      (n, key) => n + (props.stats[key] || 0),
      0,
    ),
  ],
]);
</script>
<template>
  <div class="distribution" aria-label="结果分布">
    <span
      v-for="[key, count] in segments"
      :key="key"
      :class="key"
      :style="{ flexGrow: count }"
      :title="`${key}: ${count}`"
    ></span>
  </div>
  <div class="stats">
    <div
      v-for="[key, label] in [
        ['high', '高风险'],
        ['medium', '中风险'],
        ['low', '低风险'],
        ['insufficient', '依据不足'],
        ['no_issue', '未发现问题'],
        ['not_applicable', '不适用'],
        ['merged', '已合并'],
        ['failed', '失败'],
      ]"
      :key="key"
    >
      <strong>{{ stats[key] || 0 }}</strong
      ><small>{{ label }}</small>
    </div>
  </div>
</template>
