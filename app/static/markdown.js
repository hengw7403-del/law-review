/* DOM-only markdown subset. Raw HTML/links are always text, never executed. */
window.renderMarkdown = (text,host) => {
  host.replaceChildren();let paragraph=[],code=null,list=null;
  function inline(parent,value) {
    const parts=value.split(/(\*\*[^*]+\*\*)/g);
    for(const part of parts) { if(part.startsWith("**")&&part.endsWith("**")) {const strong=document.createElement("strong");strong.textContent=part.slice(2,-2);parent.append(strong);} else parent.append(document.createTextNode(part.replace(/\\([\\`*_{}\[\]()#+!|])/g,"$1"))); }
  }
  function flush() {if(paragraph.length) {const p=document.createElement("p");inline(p,paragraph.join("\n"));host.append(p);paragraph=[];}list=null;}
  for(const line of text.replace(/\r\n?/g,"\n").split("\n")) {
    if(code) {if(new RegExp("^"+code.fence+"\\s*$").test(line)) {host.append(code.pre);code=null;}else code.node.textContent+=line+"\n";continue;}
    const fence=line.match(/^(`{3,})(.*)$/);
    if(fence) {flush();const pre=document.createElement("pre"),node=document.createElement("code");pre.append(node);code={pre,node,fence:fence[1]};continue;}
    if(!line.trim()) {flush();continue;}
    const heading=line.match(/^(#{1,6})\s+(.+)$/);
    if(heading) {flush();const h=document.createElement("h"+heading[1].length);inline(h,heading[2]);host.append(h);continue;}
    const bullet=line.match(/^\s*-\s+(.+)$/);
    if(bullet) {if(paragraph.length)flush();if(!list){list=document.createElement("ul");host.append(list);}const li=document.createElement("li");inline(li,bullet[1]);list.append(li);continue;}
    if(list)flush();paragraph.push(line);
  }
  if(code)host.append(code.pre);flush();
};
