import { useUserStore } from "../stores/user.js";
import router from "../router/index.js";

export async function request(
  path,
  { json, text = false, timeout = 30000, ...options } = {},
) {
  const url = new URL(path, location.origin);
  if (url.origin !== location.origin) throw new Error("仅允许同源请求");
  const user = useUserStore();
  const headers = new Headers(options.headers);
  if (user.token) headers.set("Authorization", `Bearer ${user.token}`);
  if (json !== undefined) {
    headers.set("Content-Type", "application/json");
    options.body = JSON.stringify(json);
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(path, {
      ...options,
      headers,
      signal: controller.signal,
      cache: "no-store",
    });
    if (response.status === 401) {
      user.logout();
      if (router.currentRoute.value.name !== "login")
        await router.replace({
          name: "login",
          query: { redirect: router.currentRoute.value.fullPath },
        });
    }
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      const error = new Error(
        payload.error?.message ||
          (typeof payload.detail === "string"
            ? payload.detail
            : "请求失败，请稍后重试"),
      );
      error.status = response.status;
      throw error;
    }
    return text ? response.text() : response.json();
  } catch (error) {
    if (error.name === "AbortError") throw new Error("请求超时，请重试");
    if (error instanceof TypeError)
      throw new Error("无法连接服务器，请检查网络");
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
const id = encodeURIComponent;
export const api = {
  dashboard: () => request('/dashboard/summary'),
  users: () => request('/admin/users'),
  knowledge: () => request('/admin/knowledge'),
  confirmTicket: (key, note = "") =>
    request(`/tickets/${id(key)}/confirm`, { method: "POST", json: { note } }),
  abandonConversation: (key) =>
    request(`/conversations/${id(key)}/abandon`, { method: "POST", json: {} }),
  startConversation: () =>
    request("/conversations", { method: "POST", json: {} }),
  conversation: (key) => request(`/conversations/${id(key)}`),
  message: (key, content) =>
    request(`/conversations/${id(key)}/messages`, {
      method: "POST",
      json: { content },
      timeout: 90000,
    }),
  tickets: (status) =>
    request(
      "/tickets" + (status ? "?status=" + encodeURIComponent(status) : ""),
    ),
  ticket: (key) => request(`/tickets/${id(key)}`),
  reviewTicket: (key) =>
    request(`/tickets/${id(key)}/review`, { method: "POST", json: {} }),
  closeTicket: (key) =>
    request(`/tickets/${id(key)}/close`, { method: "POST", json: {} }),
  login: (credentials) =>
    request("/auth/login", { method: "POST", json: credentials }),
  me: () => request("/auth/me"),
  updateOrganization: (org_name) => request("/auth/me", { method: "PATCH", json: { org_name } }),
  preview: (body) => request("/parse/preview", { method: "POST", body, timeout: 90000 }),
  contracts: () => request("/contracts"),
  upload: (body) =>
    request("/contracts", { method: "POST", body, timeout: 90000 }),
  contract: (key) => request(`/contracts/${id(key)}`),
  clauses: (key, page) =>
    request(`/contracts/${id(key)}/clauses?page=${page}&page_size=10`),
  review: (key) =>
    request(`/contracts/${id(key)}/review`, { method: "POST", json: {} }),
  task: (key) => request(`/tasks/${id(key)}`),
  results: (key) => request(`/reviews/${id(key)}/items`),
  report: (key) => request(`/reviews/${id(key)}/report`, { text: true }),
};
