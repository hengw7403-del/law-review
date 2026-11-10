<script setup>
import { formatTime } from "../utils/format.js";
import { ref, computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import { api } from "../api/index.js";
import StatusBadge from "../components/StatusBadge.vue";
import EmptyState from "../components/EmptyState.vue";
import Toast from "../components/Toast.vue";
const contracts = ref([]),
  loading = ref(true),
  error = ref(""),
  uploadOpen = ref(false),
  file = ref(null),
  title = ref(""),
  stance = ref(""),
  preview = ref(null),
  uploading = ref(false),
  submitting = ref("");
const router = useRouter();
function selectFile(event) {
  file.value = event.target.files[0] || null;
  preview.value = null;
  stance.value = "";
  error.value = "";
}
function openUpload() {
  file.value = null;
  title.value = "";
  preview.value = null;
  stance.value = "";
  error.value = "";
  uploadOpen.value = true;
}
const reviewed = computed(
  () =>
    contracts.value.filter((c) =>
      ["completed", "partial_failed"].includes(c.latest_task?.status),
    ).length,
);
async function load() {
  loading.value = true;
  error.value = "";
  try {
    contracts.value = (await api.contracts()).contracts;
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}
async function upload() {
  if (uploading.value) return;
  error.value = "";
  if (!file.value) {
    error.value = "请先选择合同文件";
    return;
  }
  if (file.value.size > 20 * 1024 * 1024) {
    error.value = "单文件不得超过20MB";
    return;
  }
  uploading.value = true;
  try {
    const body = new FormData();
    body.append("file", file.value);
    if (!preview.value) {
      preview.value = await api.preview(body);
      stance.value = preview.value.suggested_stance || "";
      return;
    }
    if (!["party_a", "party_b"].includes(stance.value)) {
      error.value = "请选择我方立场";
      return;
    }
    body.append("party_stance", stance.value);
    if (title.value.trim()) body.append("title", title.value.trim());
    await api.upload(body);
    uploadOpen.value = false;
    file.value = null;
    title.value = "";
    await load();
  } catch (e) {
    error.value = e.message;
  } finally {
    uploading.value = false;
  }
}
async function review(contract) {
  if (submitting.value) return;
  submitting.value = contract.contract_id;
  error.value = "";
  try {
    await api.review(contract.contract_id);
    await router.push({
      name: "contract",
      params: { id: contract.contract_id },
    });
  } catch (e) {
    error.value = e.message;
  } finally {
    submitting.value = "";
  }
}
onMounted(load);
</script>
<template>
  <header class="page-head">
    <div>
      <h1>合同列表</h1>
      <p class="muted">上传合同，跟踪解析与初审进度。</p>
    </div>
    <button class="primary" @click="openUpload">上传合同</button>
  </header>
  <div class="stats">
    <div>
      <strong>{{ contracts.length }}</strong
      ><small>合同数</small>
    </div>
    <div>
      <strong>{{ reviewed }}</strong
      ><small>已审查</small>
    </div>
  </div>
  <p v-if="loading" class="loading">正在加载 · · ·</p>
  <p v-if="error" class="notice error">
    {{ error }} <button @click="load">重新加载</button>
  </p>
  <EmptyState v-if="!loading && !error && !contracts.length" />
  <article
    v-for="contract in contracts"
    :key="contract.contract_id"
    class="panel contract-row"
  >
    <div>
      <h2>{{ contract.title }}</h2>
      <span class="badge">{{
        contract.party_stance === "party_a" ? "甲方" : "乙方"
      }}</span>
    </div>
    <StatusBadge :status="contract.parse_status" /><small>{{
      formatTime(contract.created_at)
    }}</small>
    <div class="actions">
      <router-link
        class="button"
        :to="{ name: 'contract', params: { id: contract.contract_id } }"
        >查看</router-link
      ><button
        :disabled="
          !!submitting ||
          contract.parse_status !== 'parsed' ||
          ['queued', 'running'].includes(contract.latest_task?.status)
        "
        @click="review(contract)"
      >
        发起审查
      </button>
    </div>
  </article>
  <div
    v-if="uploadOpen"
    class="modal"
    @click.self="!uploading && (uploadOpen = false)"
  >
    <section
      class="panel"
      role="dialog"
      aria-modal="true"
      aria-label="上传合同"
    >
      <header class="page-head">
        <h2>上传合同</h2>
        <button :disabled="uploading" @click="uploadOpen = false">关闭</button>
      </header>
      <form @submit.prevent="upload">
        <label for="file">PDF / DOCX / UTF-8 TXT，最多20MB</label
        ><input
          id="file"
          type="file"
          accept=".pdf,.docx,.txt"
          required
          :disabled="uploading"
          @change="selectFile"
        />
        <p v-if="file" class="muted">
          {{ file.name }} · {{ (file.size / 1024 / 1024).toFixed(2) }} MB
        </p>
        <label for="title">合同名称（可选）</label
        ><input id="title" v-model="title" maxlength="200" :disabled="uploading" />
        <section v-if="preview" class="party-preview" aria-live="polite">
          <h3>检测到合同当事方</h3>
          <template v-if="preview.detected_parties.party_a_name || preview.detected_parties.party_b_name">
            <p>甲方　{{ preview.detected_parties.party_a_name || "未识别" }}</p>
            <p>乙方　{{ preview.detected_parties.party_b_name || "未识别" }}</p>
          </template>
          <p v-else class="muted">未能识别合同当事方，请手动选择</p>
          <fieldset :disabled="uploading">
            <legend>我方立场</legend>
            <label class="radio-option"><input v-model="stance" type="radio" name="stance" value="party_a" required />甲方<span v-if="preview.suggested_stance === 'party_a'" class="muted">（匹配到你的组织）</span></label>
            <label class="radio-option"><input v-model="stance" type="radio" name="stance" value="party_b" required />乙方<span v-if="preview.suggested_stance === 'party_b'" class="muted">（匹配到你的组织）</span></label>
          </fieldset>
          <p class="muted">{{ preview.suggested_stance ? "如果识别错了，可以手动改。请确认我方立场后入库。" : "请选择我方立场" }}</p>
        </section>
        <button class="primary" :disabled="uploading || (preview && !stance)">
          {{ uploading ? "正在处理…" : preview ? "确认立场并上传" : "解析并识别当事方" }}
        </button>
      </form>
      <p v-if="error" class="notice error">{{ error }}</p>
    </section>
  </div>
  <Toast :message="error" />
</template>
