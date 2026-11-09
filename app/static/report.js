const id=new URLSearchParams(location.search).get("id");let markdown="";
document.getElementById("back-result").href="/result.html?id="+encodeURIComponent(id||"");
async function loadReport() {
  document.getElementById("retry").hidden=true;Api.message(document.getElementById("error"),"");
  try {if(!id)throw new Error("缺少任务ID，请返回合同详情。");markdown=await Api.request("/reviews/"+encodeURIComponent(id)+"/report",{responseType:"text"});renderMarkdown(markdown,document.getElementById("report"));document.getElementById("download").disabled=false;}
  catch(e){Api.message(document.getElementById("error"),e.message);document.getElementById("retry").hidden=false;}
  finally{document.getElementById("loading").hidden=true;}
}
document.getElementById("retry").onclick=loadReport;
document.getElementById("download").onclick=()=>{const url=URL.createObjectURL(new Blob([markdown],{type:"text/markdown;charset=utf-8"}));const a=document.createElement("a");a.href=url;a.download="review-"+id+".md";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
if(Api.requireSession())loadReport();
