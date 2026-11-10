// Parse a deliberately small Markdown subset into data, never HTML strings.
export function inline(text) {
  return text
    .split(/(\*\*[^*]+\*\*)/g)
    .filter(Boolean)
    .map((part) => ({
      text: part.startsWith("**") ? part.slice(2, -2) : part,
      bold: part.startsWith("**"),
    }));
}
export function parseMarkdown(text) {
  const blocks = [];
  let paragraph = [],
    list = [],
    code = null,
    fence = "";
  function flush() {
    if (paragraph.length) {
      blocks.push({ type: "p", spans: inline(paragraph.join(" ")) });
      paragraph = [];
    }
    if (list.length) {
      blocks.push({ type: "list", lines: list });
      list = [];
    }
  }
  for (const line of text.split(/\r?\n/)) {
    const marker = line.match(/^(`{3,})/);
    if (code !== null) {
      if (marker && marker[1].length >= fence.length) {
        blocks.push({ type: "code", text: code.join("\n") });
        code = null;
      } else code.push(line);
      continue;
    }
    if (marker) {
      flush();
      code = [];
      fence = marker[1];
      continue;
    }
    if (!line.trim()) {
      flush();
      continue;
    }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flush();
      blocks.push({ type: "h" + heading[1].length, spans: inline(heading[2]) });
      continue;
    }
    if (line.startsWith("- ")) {
      if (paragraph.length) flush();
      list.push(inline(line.slice(2)));
      continue;
    }
    if (list.length) flush();
    paragraph.push(line);
  }
  flush();
  if (code !== null) blocks.push({ type: "code", text: code.join("\n") });
  return blocks;
}
