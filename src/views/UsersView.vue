<script setup>
import { ref, onMounted } from 'vue';
import { api } from '../api/index.js';
const users = ref([]), loading = ref(true), error = ref('');
const roles = { admin: '管理员', reviewer: '法务', requester: '提交人' };
async function load() { loading.value = true; error.value = ''; try { users.value = (await api.users()).users; } catch (e) { error.value = e.message; } finally { loading.value = false; } }
onMounted(load);
</script>
<template>
  <header class="page-head"><div><h1>用户管理</h1><p class="muted">查看账号、组织与角色。账号创建沿用 create-user 交互命令，不在页面展示密码。</p></div></header>
  <p v-if="loading" class="loading">正在读取 · · ·</p><p v-if="error" class="notice error">{{ error }} <button @click="load">重试</button></p>
  <section class="panel user-list"><article v-for="user in users" :key="user.user_id" class="activity-row"><div><h2>{{ user.username }}</h2><small>{{ user.org_name || '未设置组织' }}</small></div><span>{{ roles[user.role] }}</span><span class="badge">{{ user.active ? '启用' : '停用' }}</span></article></section>
</template>
