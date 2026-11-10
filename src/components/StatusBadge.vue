<script setup>
defineProps({ status: String });
const labels = {
  pending: "待启动",
  needs_human: "待人工确认",
  reviewing: "审查中",
  done: "已完成",
  closed: "已关闭",
  parsed: "解析成功",
  parse_failed: "解析失败",
  uploaded: "等待解析",
  parsing: "正在解析",
  queued: "排队中",
  running: "正在审查",
  completed: "审查完成",
  partial_failed: "部分完成",
  failed: "失败",
  high: "高风险",
  medium: "中风险",
  low: "低风险",
  unknown: "依据不足",
};
</script>
<template>
  <span class="badge" :class="status">{{ labels[status] || status }}</span>
</template>
