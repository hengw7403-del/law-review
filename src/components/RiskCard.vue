<script setup>
import StatusBadge from "./StatusBadge.vue";
defineProps({ item: { type: Object, required: true } });
const names = {
  liability: "违约责任",
  payment: "付款条件",
  dispute_resolution: "争议解决",
  service_scope: "服务范围与交付物",
  milestones: "进度",
  acceptance: "验收",
  change_control: "需求变更",
  intellectual_property: "知识产权",
  confidentiality_data: "保密与数据",
  termination: "解除与终止",
  payment_default: "付款违约责任缺失",
  delivery_default: "交付违约责任缺失",
  dispute_resolution_gap: "争议方式缺失",
  acceptance_gap: "验收缺失",
  confidentiality_gap: "保密缺失",
};
</script>
<template>
  <article class="panel risk-card" :class="item.risk_level">
    <header class="page-head">
      <div class="risk-title">
        <h2>{{ item.scope === "contract" ? "合同级缺失" : item.clause_no }}</h2>
        <span class="risk-rule">{{ names[item.rule_id] || item.rule_id }}</span>
      </div>
      <StatusBadge
        :status="
          item.check_status === 'supported' ? item.risk_level : 'unknown'
        "
      />
    </header>
    <p>{{ item.description }}</p>
    <p class="muted"><span aria-hidden="true">💡</span> 建议：{{ item.suggestion || "（无）" }}</p>
    <details v-if="item.scope === 'clause'">
      <summary>查看条款原文</summary>
      <p class="pre-wrap">{{ item.clause_content }}</p>
    </details>
    <details class="risk-evidence">
      <summary><span class="evidence-arrow" aria-hidden="true">▸</span> 依据（{{ item.evidence.length }} 条）</summary>
      <section
        v-for="(source, index) in item.evidence"
        :key="source.chunk_id || index"
        class="clause"
      >
        <h3>
          {{ source.source_name || source.source_file }} ·
          {{ source.article_no }}
        </h3>
        <p class="pre-wrap">{{ source.content }}</p>
      </section>
      <p v-if="!item.evidence.length" class="muted">
        无通过校验的引用，需人工核查。
      </p>
    </details>
  </article>
</template>
