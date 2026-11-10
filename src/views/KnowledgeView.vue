<script setup>
import { ref, onMounted } from 'vue';
import { api } from '../api/index.js';
import EmptyState from '../components/EmptyState.vue';
const sources = ref([]), loading = ref(true), error = ref('');
async function load() { loading.value = true; error.value = ''; try { sources.value = (await api.knowledge()).sources; } catch (e) { error.value = e.message; } finally { loading.value = false; } }
onMounted(load);
</script>
<template>
  <header class="page-head"><div><h1>知识库</h1><p class="muted">查看法规来源；导入与核对启用沿用管理员命令行流程。</p></div><router-link class="button primary" to="/workspace/knowledge-help">导入法规说明</router-link></header>
  <p v-if="loading" class="loading">正在读取 · · ·</p><p v-if="error" class="notice error">{{ error }} <button @click="load">重试</button></p>
  <EmptyState v-if="!loading && !error && !sources.length" text="尚未导入法规" />
  <article v-for="source in sources" :key="source.source_id + source.source_file" class="panel"><h2>{{ source.source_id }}</h2><p class="muted">{{ source.source_file }}</p><p>{{ source.chunks }} 块 · 已核对 {{ source.verified }} · 已启用 {{ source.enabled }}</p></article>
</template>
