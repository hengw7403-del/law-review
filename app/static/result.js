const taskId = new URLSearchParams(location.search).get("id");
const names = {liability:"违约责任",payment:"付款条件",dispute_resolution:"争议解决",payment_default:"逾期付款责任缺失",delivery_default:"交付违约责任缺失",acceptance:"验收标准",confidentiality:"保密"};
Object.assign(names,{service_scope:"服务范围与交付物",milestones:"里程碑与进度",change_control:"需求变更机制",intellectual_property:"知识产权归属",confidentiality_data:"保密与数据处理",termination:"解除与终止"});
function element(tag,text,className) { const e=document.createElement(tag); if(text!==undefined)e.textContent=text; if(className)e.className=className; return e; }
function label(item) { return (item.scope==="contract"?"合同级缺失":item.clause_no||"条款 "+item.clause_seq)+" · "+(names[item.rule_id]||item.rule_id); }
function section(host,title,text) { host.append(element("h3",title),element("p",text||"（无）","clause-content")); }
async function loadResults() {
  Api.message(document.getElementById("error"),"");document.getElementById("retry").hidden=true;
  try {
    if(!taskId)throw new Error("缺少任务ID，请返回合同详情选择任务。");
    const data=await Api.request("/reviews/"+encodeURIComponent(taskId)+"/items");
    const s=data.stats; document.getElementById("summary").hidden=false;
    document.getElementById("title").textContent=data.contract.title;
    document.getElementById("metadata").textContent="我方立场："+Api.stanceLabels[data.contract.party_stance]+" · 审查时间："+Api.date(data.reviewed_at);
    document.getElementById("risk-total").textContent=(s.high+s.medium+s.low)+" 风险（已获依据支持）";
    const stats=document.getElementById("stats");stats.replaceChildren();
    for(const [key,text] of [["high","高风险"],["medium","中风险"],["low","低风险"],["insufficient","依据不足"],["no_issue","未发现问题"],["not_applicable","不适用"],["failed","失败"]]) {
      const cell=element("span",undefined,"stat-item");cell.dataset.stat=key;
      cell.append(element("span",String(s[key]),"stat-value"),element("span",text,"stat-label"));stats.append(cell);
    }
    const report=document.getElementById("full-report");report.hidden=false;report.href="/report.html?id="+encodeURIComponent(taskId);
    const cards=document.getElementById("cards");cards.replaceChildren();
    for(const item of data.items.filter(i=>["supported","insufficient","failed","pending"].includes(i.check_status))) {
      const kind=item.check_status==="supported"?item.risk_level:"unknown";
      const card=element("article",undefined,"panel risk-card "+kind);
      const header=element("div",undefined,"page-head");header.append(element("h2",label(item)),element("span",({high:"高风险",medium:"中风险",low:"低风险"})[kind]||({failed:"检查失败",pending:"尚未完成"})[item.check_status]||"依据不足","badge risk-badge "+kind));card.append(header);
      if(item.scope==="clause")section(card,"条款原文",item.clause_content);
      section(card,"问题",item.description);section(card,"建议",item.suggestion);
      const details=element("details");details.append(element("summary",`依据（${item.evidence.length} 条）`));
      if(!item.evidence.length)details.append(element("p","无通过校验的引用，需补充依据并人工核查。","muted"));
      for(const e of item.evidence) { const b=element("section",undefined,"clause");b.append(element("h3",[e.source_name||e.source_file,e.article_no].filter(Boolean).join(" · ")));b.append(element("p",e.content,"clause-content"));details.append(b); }
      card.append(details);cards.append(card);
    }
    if(!cards.children.length)cards.append(element("p","本次没有已支持风险或待核查问题。","notice info"));
    const other=document.getElementById("other");other.replaceChildren();
    for(const [status,text] of [["no_issue_detected",`其余 ${s.no_issue} 项检查未发现问题`],["not_applicable",`${s.not_applicable} 项不适用`],["merged",`${s.merged||0} 项已合并`]]) {
      const d=element("details",undefined,"panel");d.append(element("summary",text));
      for(const i of data.items.filter(i=>i.check_status===status))d.append(element("p",label(i)+" —— "+(status==="merged"?"已合并至 "+i.merged_into:(i.reason||i.description))));
      other.append(d);
    }
    if(!["completed","partial_failed"].includes(data.status))Api.message(document.getElementById("error"),"任务尚未正常完成，当前仅展示已保存结果。");
  } catch(e) { Api.message(document.getElementById("error"),e.message);document.getElementById("retry").hidden=false; }
  finally {document.getElementById("loading").hidden=true;}
}
document.getElementById("retry").onclick=loadResults;
if(Api.requireSession())loadResults();
