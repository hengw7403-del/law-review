/* Same-origin requests only. Credentials live for the current browser tab. */
window.Api = (() => {
  const TOKEN = "access_token";
  function logout() {
    sessionStorage.removeItem(TOKEN);
    sessionStorage.removeItem("role");
    sessionStorage.removeItem("username");
    window.location.replace("/login.html");
  }
  function requireSession() {
    if (!sessionStorage.getItem(TOKEN)) { logout(); return false; }
    return true;
  }
  async function request(path, options = {}) {
    const url = new URL(path, window.location.origin);
    if (url.origin !== window.location.origin) throw new Error("仅允许同源请求");
    const { json, responseType = "json", timeout = 30000, ...init } = options;
    const headers = new Headers(init.headers);
    const token = sessionStorage.getItem(TOKEN);
    if (token) headers.set("Authorization", "Bearer " + token);
    if (json !== undefined) { headers.set("Content-Type", "application/json"); init.body = JSON.stringify(json); }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(url.pathname + url.search, {
        ...init, headers, signal: controller.signal, credentials: "same-origin", cache: "no-store"
      });
      if (response.status === 401) {
        sessionStorage.removeItem(TOKEN);
        sessionStorage.removeItem("role");
        sessionStorage.removeItem("username");
        if (window.location.pathname !== "/login.html") window.location.replace("/login.html");
      }
      if (!response.ok) {
        let payload;
        try { payload = await response.json(); } catch (_) { payload = {}; }
        const message = payload.error?.message || (typeof payload.detail === "string" ? payload.detail : "请求失败，请检查输入或稍后重试");
        const error = new Error(message);
        error.status = response.status;
        throw error;
      }
      return responseType === "text" ? await response.text() : await response.json();
    } catch (error) {
      if (error.name === "AbortError") throw new Error("请求超时，请稍后重试。");
      if (error instanceof TypeError) throw new Error("无法连接服务器，请检查网络后重试。");
      throw error;
    } finally { clearTimeout(timer); }
  }
  function message(element, text, kind = "error") {
    element.textContent = text;
    element.className = "notice " + kind;
    element.hidden = !text;
  }
  function date(value) {
    if (!value) return "—";
    // Historical SQLite CURRENT_TIMESTAMP values are UTC without a suffix.
    const normalized = value.includes("T") ? value : value.replace(" ", "T") + "Z";
    const parsed = new Date(normalized);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("zh-CN", { hour12: false });
  }
  const parseLabels = { uploaded: "等待解析", parsing: "正在解析", parsed: "解析成功", parse_failed: "解析失败" };
  const stanceLabels = { party_a: "甲方", party_b: "乙方" };
  function badge(status) {
    const span = document.createElement("span");
    span.className = "badge " + (status === "parsed" ? "good" : status === "parse_failed" ? "bad" : "neutral");
    span.textContent = parseLabels[status] || "状态待确认";
    return span;
  }
  async function viewReport(taskId) {
    const viewer = window.open("about:blank", "_blank");
    if (!viewer) throw new Error("浏览器阻止了新窗口，请允许弹出窗口后重试。");
    viewer.opener = null;
    viewer.document.title = "正在读取报告";
    viewer.document.body.textContent = "正在读取报告…";
    try {
      const text = await request("/reviews/" + encodeURIComponent(taskId) + "/report", { responseType: "text" });
      const blobUrl = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
      viewer.location.replace(blobUrl);
      setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
    } catch (error) { viewer.close(); throw error; }
  }
  return { request, logout, requireSession, message, date, badge, parseLabels, stanceLabels, viewReport };
})();

if (window.location.pathname === "/" || window.location.pathname === "/index.html") {
  window.location.replace(sessionStorage.getItem("access_token") ? "/contract-list.html" : "/login.html");
}

// Presentation only: no network requests or changes to authentication/workflows.
document.querySelectorAll('[id="loading"],#detail-loading').forEach(e=>e.classList.add('loading-dots'));
const showMessage=Api.message;
const toastTimers=new WeakMap();
Api.message=function(target,text,kind='error'){
  clearTimeout(toastTimers.get(target));
  showMessage(target,text,kind);
  if(!text)return;
  // Modal messages stay within the modal's top layer.
  if(!target.closest('dialog'))target.classList.add('toast');
  toastTimers.set(target,setTimeout(()=>{target.hidden=true;},3000));
};
