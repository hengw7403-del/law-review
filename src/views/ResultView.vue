<script setup>
import { ref, computed, onMounted } from "vue";
import { useRoute } from "vue-router";
import { api } from "../api/index.js";
import RiskCard from "../components/RiskCard.vue";
import StatBar from "../components/StatBar.vue";
import StatusBadge from "../components/StatusBadge.vue";
import Toast from "../components/Toast.vue";
const id = useRoute().params.id,
  data = ref(null),
  error = ref(""),
  loading = ref(true);
const findings = computed(
  () =>
    data.value?.items.filter((i) =>
      ["supported", "insufficient", "failed", "pending"].includes(
        i.check_status,
      ),
    ) || [],
);
async function load() {
  loading.value = true;
  error.value = "";
  try {
    data.value = await api.results(id);
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}
onMounted(load);
</script>
<template>
  <header class="page-head">
    <h1>审查结果</h1>
    <router-link class="button primary" :to="{ name: 'report', params: { id } }"
      >查看完整报告</router-link
    >
  </header>
  <p v-if="loading" class="loading">正在加载 · · ·</p>
  <p v-if="error" class="notice error">
    {{ error }} <button @click="load">重试</button>
  </p>
  <template v-if="data"
    ><section class="panel sticky">
      <header class="page-head">
        <h1>{{ data.contract.title }}</h1>
        <StatusBadge :status="data.status" />
      </header>
      <p class="muted">
        我方立场：{{
          data.contract.party_stance === "party_a" ? "甲方" : "乙方"
        }}
        · {{ data.reviewed_at }}
      </p>
      <p>
        {{
          data.stats.high + data.stats.medium + data.stats.low
        }}
        风险（已获依据支持）
      </p>
      <StatBar :stats="data.stats" />
    </section>
    <RiskCard v-for="item in findings" :key="item.item_key" :item="item" />
    <p v-if="!findings.length" class="panel">
      本次没有已支持风险或待核查问题。
    </p>
    <details
      v-for="[status, label] in [
        ['no_issue_detected', '未发现问题'],
        ['not_applicable', '不适用'],
        ['merged', '已合并'],
      ]"
      :key="status"
      class="panel"
    >
      <summary>
        {{ label }}（{{
          data.items.filter((i) => i.check_status === status).length
        }}）
      </summary>
      <p
        v-for="item in data.items.filter((i) => i.check_status === status)"
        :key="item.item_key"
        class="muted"
      >
        {{ item.clause_no || "合同级" }} · {{ item.rule_id }}：{{
          status === "merged" ? "合并至 " + item.merged_into : item.reason
        }}
      </p>
    </details></template
  ><Toast :message="error" />
</template>
