const params = new URLSearchParams(location.search);
const contractId = params.get("id");
const reviewButton = document.getElementById("start-review");
const terminal = new Set(["completed", "partial_failed", "failed"]);
let contract = null, taskId = null, timer = null, alive = true, page = 1, pageCount = 1;
function stopPolling() { clearTimeout(timer); timer = null; }
window.addEventListener("pagehide", () => { alive = false; stopPolling(); });
// Returning from the back/forward cache needs a fresh server check.
window.addEventListener("pageshow", (event) => { if (event.persisted) { alive = true; if (Api.requireSession()) loadContract(); } });

async function loadClauses() {
  const errorBox = document.getElementById("clause-error");
  document.getElementById("retry-clauses").hidden = true;
  try {
    const data = await Api.request("/contracts/" + encodeURIComponent(contractId) + "/clauses?page=" + page + "&page_size=10");
    pageCount = Math.max(1, Math.ceil(data.total / data.page_size));
    const host = document.getElementById("clauses"); host.replaceChildren();
    for (const clause of data.clauses) {
      const article = document.createElement("article"); article.className = "clause";
      const heading = document.createElement("h3"); heading.textContent = clause.original_no || "条款 " + clause.seq;
      const text = document.createElement("p"); text.className = "clause-content"; text.textContent = clause.content;
      article.append(heading, text); host.append(article);
    }
    if (!data.total) host.textContent = "暂无可展示的条款。";
    Api.message(errorBox, "");
    document.getElementById("page-label").textContent = "第 " + page + " / " + pageCount + " 页，共 " + data.total + " 条";
    document.getElementById("prev-page").disabled = page <= 1;
    document.getElementById("next-page").disabled = page >= pageCount;
  } catch (error) { Api.message(errorBox, error.message); document.getElementById("retry-clauses").hidden = false; }
}
function displayTask(task) {
  if (task.scope) {
    const selected = task.scope.selected_clauses, totalClauses = task.scope.total_parent_clauses;
    document.getElementById("review-scope").textContent = selected === totalClauses
      ? "本次任务覆盖全部 " + totalClauses + " 个父条款，并按规则适用条件筛选检查项。"
      : "本次任务仅覆盖前 " + selected + "/" + totalClauses + " 个父条款；再次发起审查将默认检查全部条款。";
  }
  const done = task.executed_items ?? (task.done_items + task.failed_items);
  const total = task.planned_model_items;
  const labels = {
    queued: "排队中", running: "正在审查（已完成 " + done + "/" + total + "）",
    completed: "审查完成", partial_failed: "部分完成（" + task.failed_items + " 项失败）", failed: "审查失败"
  };
  document.getElementById("task-status").textContent = labels[task.status] || "任务状态待确认";
  document.getElementById("task-progress").value = Math.min(1, Math.max(0, task.progress));
  document.getElementById("task-counts").textContent = "检查已处理 " + done + "/" + total + " · 不适用 " + task.not_applicable_items + " · 失败 " + task.failed_items;
  const ended = terminal.has(task.status);
  const hasReport = ["completed", "partial_failed"].includes(task.status);
  document.getElementById("view-report").hidden = !hasReport;
  document.getElementById("view-result").hidden = !hasReport;
  document.getElementById("view-result").href = "/result.html?id=" + encodeURIComponent(task.task_id);
  document.getElementById("report-hint").hidden = !hasReport;
  reviewButton.disabled = !ended || contract?.parse_status !== "parsed";
  reviewButton.textContent = ended ? "再次发起审查" : "审查进行中";
  if (task.status === "failed") Api.message(document.getElementById("task-error"), task.error_message || task.error_code || "任务执行失败，请联系管理员后重试。");
  else if (task.status === "partial_failed") Api.message(document.getElementById("task-error"), "部分检查执行失败，详见报告；未完成的检查不代表合同没有问题。");
  return ended;
}
async function pollTask() {
  stopPolling();
  document.getElementById("retry-task").hidden = true;
  Api.message(document.getElementById("task-error"), "");
  try {
    const task = await Api.request("/tasks/" + encodeURIComponent(taskId));
    if (!alive) return;
    const ended = displayTask(task);
    if (!ended) timer = setTimeout(pollTask, 2000);
  } catch (error) {
    if (!alive) return;
    Api.message(document.getElementById("task-error"), "获取进度失败：" + error.message);
    document.getElementById("retry-task").hidden = false;
  }
}
function watchTask(id) {
  stopPolling(); taskId = id;
  document.getElementById("task-panel").hidden = false;
  reviewButton.disabled = true;
  document.getElementById("task-status").textContent = "正在获取进度…";
  pollTask();
}
async function loadContract() {
  stopPolling();
  document.getElementById("detail-loading").hidden = false;
  document.getElementById("retry-detail").hidden = true;
  Api.message(document.getElementById("detail-error"), "");
  try {
    contract = await Api.request("/contracts/" + encodeURIComponent(contractId));
    document.getElementById("contract-panel").hidden = false;
    document.getElementById("clauses-panel").hidden = false;
    document.getElementById("contract-name").textContent = contract.title;
    document.title = contract.title + " · 合同辅助初审";
    document.getElementById("contract-created").textContent = "创建于 " + Api.date(contract.created_at);
    document.getElementById("info-stance").textContent = Api.stanceLabels[contract.party_stance] || "—";
    document.getElementById("info-count").textContent = contract.clause_count + " 条";
    document.getElementById("info-parse").replaceChildren(Api.badge(contract.parse_status));
    document.getElementById("info-version").textContent = "v" + contract.version;
    reviewButton.disabled = contract.parse_status !== "parsed";
    const failed = contract.parse_status === "parse_failed";
    document.getElementById("reupload").hidden = !failed;
    Api.message(document.getElementById("parse-error"), failed ? "解析失败：" + (contract.parse_error || "未能提取合同正文，请检查文件后重新上传。") : "");
    Api.message(document.getElementById("parse-warning"), contract.parse_warnings.length ? "解析警告：" + contract.parse_warnings.join("、") + "，发起审查前需人工核对。" : "", "info");
    if (failed) reviewButton.hidden = true;
    loadClauses();
    // Always resume the server's latest task; URL parameters cannot select another contract's task.
    if (contract.latest_task) watchTask(contract.latest_task.task_id);
  } catch (error) { Api.message(document.getElementById("detail-error"), error.message); document.getElementById("retry-detail").hidden = false; }
  finally { document.getElementById("detail-loading").hidden = true; }
}
reviewButton.addEventListener("click", async () => {
  if (reviewButton.disabled) return;
  reviewButton.disabled = true; reviewButton.textContent = "正在提交…";
  Api.message(document.getElementById("detail-error"), "");
  try {
    const result = await Api.request("/contracts/" + encodeURIComponent(contractId) + "/review", { method: "POST", json: {} });
    watchTask(result.task_id);
  } catch (error) { Api.message(document.getElementById("detail-error"), error.message); reviewButton.disabled = false; reviewButton.textContent = "发起审查"; }
});
document.getElementById("retry-detail").addEventListener("click", loadContract);
document.getElementById("retry-task").addEventListener("click", pollTask);
document.getElementById("retry-clauses").addEventListener("click", loadClauses);
document.getElementById("prev-page").addEventListener("click", () => { if (page > 1) { page -= 1; loadClauses(); } });
document.getElementById("next-page").addEventListener("click", () => { if (page < pageCount) { page += 1; loadClauses(); } });
document.getElementById("view-report").addEventListener("click", async (event) => {
  const button = event.currentTarget; button.disabled = true;
  try { window.location.assign("/report.html?id="+encodeURIComponent(taskId)); }
  catch (error) { Api.message(document.getElementById("task-error"), error.message); }
  finally { button.disabled = false; }
});
if (Api.requireSession()) {
  if (contractId) loadContract();
  else { document.getElementById("detail-loading").hidden = true; Api.message(document.getElementById("detail-error"), "缺少合同 ID，请返回合同列表重新选择。"); }
}
