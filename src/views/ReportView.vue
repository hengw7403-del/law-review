<script setup>
import LoadingSkeleton from "../components/LoadingSkeleton.vue";
import { ref, onMounted, computed } from "vue";
import { useRoute } from "vue-router";
import { api } from "../api/index.js";
import Toast from "../components/Toast.vue";
import { parseMarkdown } from "../utils/markdown.js";
const id = useRoute().params.id,
  markdown = ref(""),
  loading = ref(true),
  error = ref("");
const blocks = computed(() => parseMarkdown(markdown.value));
async function load() {
  loading.value = true;
  error.value = "";
  try {
    markdown.value = await api.report(id);
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}
function download() {
  const url = URL.createObjectURL(
    new Blob([markdown.value], { type: "text/markdown;charset=utf-8" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `review-${id}.md`;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
onMounted(load);
</script>
<template>
  <header class="page-head">
    <h1>完整审查报告</h1>
    <div class="actions">
      <router-link class="button" :to="{ name: 'result', params: { id } }"
        >返回结果页</router-link
      ><button class="primary" :disabled="loading || !!error" @click="download">
        下载 .md
      </button>
    </div>
  </header>
  <LoadingSkeleton v-if="loading" label="正在加载" />
  <p v-if="error" class="notice error">
    {{ error }} <button @click="load">重试</button>
  </p>
  <article class="markdown">
    <template v-for="(block, index) in blocks" :key="index">
      <pre v-if="block.type === 'code'"><code>{{ block.text }}</code></pre>
      <ul v-else-if="block.type === 'list'">
        <li v-for="(line, i) in block.lines" :key="i">
          <template v-for="(span, j) in line" :key="j"
            ><strong v-if="span.bold">{{ span.text }}</strong
            ><template v-else>{{ span.text }}</template></template
          >
        </li>
      </ul>
      <component :is="block.type" v-else
        ><template v-for="(span, j) in block.spans" :key="j"
          ><strong v-if="span.bold">{{ span.text }}</strong
          ><template v-else>{{ span.text }}</template></template
        ></component
      ></template
    >
  </article>
  <Toast :message="error" />
</template>
