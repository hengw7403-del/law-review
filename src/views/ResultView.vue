<script setup>
import { ref, computed, onMounted } from "vue";
import { useRoute } from "vue-router";
import { api } from "../api/index.js";
import RiskCard from "../components/RiskCard.vue";
import StatBar from "../components/StatBar.vue";
import StatusBadge from "../components/StatusBadge.vue";
import Toast from "../components/Toast.vue";
import KnowledgeWarning from "../components/KnowledgeWarning.vue";
import { formatTime } from "../utils/format.js";
const contractLabels = {
  payment_default: "逾期付款责任缺失",
  delivery_default: "交付违约责任缺失",
  dispute_resolution_gap: "争议解决方式缺失",
  acceptance_gap: "验收标准与期限缺失",
  confidentiality_gap: "保密义务缺失",
};
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
const clauseFindings = computed(() =>
  findings.value.filter((i) => i.scope !== "contract"),
);
const contractItems = computed(
  () => data.value?.items.filter((i) => i.scope === "contract") || [],
);
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
  <KnowledgeWarning :show="!!data?.all_clauses_insufficient" />
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
        · {{ formatTime(data.reviewed_at) }}
      </p>
      <p>
        {{ data.stats.high + data.stats.medium + data.stats.low }}
        风险（已获依据支持）
      </p>
      <StatBar :stats="data.stats" />
    </section>
    <RiskCard
      v-for="item in clauseFindings"
      :key="item.item_key"
      :item="item"
    />
    <section v-if="contractItems.length" class="contract-results">
      <h2>合同级缺失（{{ contractItems.length }}项）</h2>
      <template v-for="item in contractItems" :key="item.item_key"
        ><RiskCard
          v-if="
            ['supported', 'insufficient', 'failed', 'pending'].includes(
              item.check_status,
            )
          "
          :item="item"
        />
        <article v-else class="panel">
          <h3>
            {{ contractLabels[item.rule_id] || item.rule_id }} ·
            {{
              item.check_status === "no_issue_detected"
                ? "未发现缺失"
                : "不适用"
            }}
          </h3>
          <p>{{ item.description }}</p>
          <p class="muted">{{ item.reason }}</p>
        </article></template
      >
    </section>
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
          data.items.filter(
            (i) => i.scope !== "contract" && i.check_status === status,
          ).length
        }}）
      </summary>
      <p
        v-for="item in data.items.filter(
          (i) => i.scope !== 'contract' && i.check_status === status,
        )"
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
