const listMessage = document.getElementById("list-message");
const dialog = document.getElementById("upload-dialog");
const uploadButton = document.getElementById("upload-submit");
let uploading = false;
window.addEventListener("pageshow", (event) => {
  if (event.persisted && Api.requireSession()) {
    document.getElementById("contract-rows").replaceChildren();
    document.getElementById("table-wrap").hidden = true;
    loadContracts();
  }
});

async function loadContracts() {
  document.getElementById("loading").hidden = false;
  document.getElementById("retry-list").hidden = true;
  try {
    const data = await Api.request("/contracts");
    document.getElementById('contract-total').textContent=data.contracts.length;
    document.getElementById('reviewed-total').textContent=data.contracts.filter(c=>['completed','partial_failed'].includes(c.latest_task?.status)).length;
    const body = document.getElementById("contract-rows");
    body.replaceChildren();
    for (const contract of data.contracts) {
      const row = document.createElement("tr");
      for (const value of [contract.title, Api.stanceLabels[contract.party_stance] || "—"]) {
        const cell = document.createElement("td"); cell.textContent = value; row.append(cell);
      }
      const status = document.createElement("td"); status.append(Api.badge(contract.parse_status)); row.append(status);
      const date = document.createElement("td"); date.textContent = Api.date(contract.created_at); row.append(date);
      const cell = document.createElement("td");
      const actions = document.createElement("div"); actions.className = "actions";
      const detail = document.createElement("a"); detail.className = "button"; detail.textContent = "查看";
      detail.href = "/contract-detail.html?id=" + encodeURIComponent(contract.contract_id);
      const review = document.createElement("button"); review.textContent = "发起审查"; review.type = "button";
      const active = ["queued", "running"].includes(contract.latest_task?.status);
      review.disabled = contract.parse_status !== "parsed" || active;
      if (active) review.textContent = "审查进行中";
      review.addEventListener("click", async () => {
        if (review.disabled) return;
        review.disabled = true; review.textContent = "正在提交…";
        Api.message(listMessage, "");
        try {
          const result = await Api.request("/contracts/" + encodeURIComponent(contract.contract_id) + "/review", { method: "POST", json: {} });
          window.location.assign(detail.href + "&task=" + encodeURIComponent(result.task_id));
        } catch (error) { Api.message(listMessage, error.message); review.disabled = false; review.textContent = "发起审查"; }
      });
      actions.append(detail, review); cell.append(actions); row.append(cell); body.append(row);
    }
    document.getElementById("empty").hidden = data.contracts.length !== 0;
    document.getElementById("table-wrap").hidden = data.contracts.length === 0;
  } catch (error) {
    Api.message(listMessage, error.message);
    document.getElementById("retry-list").hidden = false;
  } finally { document.getElementById("loading").hidden = true; }
}
document.getElementById("retry-list").addEventListener("click", () => { Api.message(listMessage, ""); loadContracts(); });
document.getElementById("open-upload").addEventListener("click", () => dialog.showModal());
document.getElementById("close-upload").addEventListener("click", () => { if (!uploading) dialog.close(); });
dialog.addEventListener("cancel", (event) => { if (uploading) event.preventDefault(); });
document.getElementById("contract-file").addEventListener("change", (event) => {
  const file = event.target.files[0];
  document.getElementById("file-summary").textContent = file ? file.name + " · " + (file.size / 1024 / 1024).toFixed(2) + " MB（" + file.size.toLocaleString() + " 字节）" : "尚未选择文件";
  Api.message(document.getElementById("upload-error"), "");
});
document.getElementById("upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (uploading) return;
  const file = document.getElementById("contract-file").files[0];
  const errorBox = document.getElementById("upload-error");
  if (!file) { Api.message(errorBox, "请先选择合同文件。"); return; }
  if (file.size > 20 * 1024 * 1024) { Api.message(errorBox, "单文件不得超过 20 MB。"); return; }
  const data = new FormData();
  data.append("file", file);
  data.append("stance", document.getElementById("contract-stance").value);
  const title = document.getElementById("contract-title").value.trim();
  if (title) data.append("title", title);
  uploading = true; uploadButton.disabled = true; uploadButton.textContent = "正在上传并解析…";
  Api.message(errorBox, "");
  try {
    const result = await Api.request("/contracts", { method: "POST", body: data, timeout: 90000 });
    dialog.close();
    document.getElementById("upload-form").reset();
    document.getElementById("file-summary").textContent = "尚未选择文件";
    Api.message(listMessage, result.warnings.length ? "上传成功，请进入详情核对解析警告。" : "上传成功，已解析为 " + result.clause_count + " 条条款。", "success");
    await loadContracts();
  } catch (error) { Api.message(errorBox, error.message); }
  finally { uploading = false; uploadButton.disabled = false; uploadButton.textContent = "上传"; }
});
if (Api.requireSession()) {
  loadContracts();
  if (new URLSearchParams(location.search).get("upload") === "1") dialog.showModal();
}
