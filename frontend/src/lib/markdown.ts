// 极简 markdown → HTML。不引任何第三方依赖（硬性约束）。
//
// 只支持知识正文 body_md 与问答 answer_md 实际用到的六种语法：
//   1. `## 标题`      → <h2>（一到六级 # 一律渲染为 h2，theme.css 的 .doc 只定义了 h2）
//   2. 空行分段        → <p>（段内的换行渲染为 <br>，中文正文里不能靠空白折叠）
//   3. 行内 `code`     → <code>
//   4. ``` 围栏代码块  → <pre><code>
//   5. `**强调**`      → <strong>（M5：问答回答里 LLM 高频使用）
//   6. `- ` 列表行     → <ul><li>（M5 同上；只认一级，不嵌套）
// 其余 markdown 语法（表格、链接）一律按纯文本原样输出。
//
// 安全：先对整段输入做 HTML 转义，再套标签。转义后的文本里不可能出现 < >，
// 因此后续的正则替换只会命中我们自己插入的标签，配合 dangerouslySetInnerHTML 是安全的。

const ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

/** 行内 `code` 与 **强调**（已在转义后的文本上执行） */
function inline(s: string): string {
  return s
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
}

export function renderMarkdown(md: string): string {
  if (!md) return "";

  const lines = escapeHtml(md).replace(/\r\n?/g, "\n").split("\n");
  const out: string[] = [];
  let para: string[] = [];
  let list: string[] = [];
  let fence: string[] | null = null; // 非 null 表示正处于 ``` 围栏内

  const flushPara = () => {
    if (para.length === 0) return;
    out.push(`<p>${inline(para.join("<br>"))}</p>`);
    para = [];
  };
  const flushList = () => {
    if (list.length === 0) return;
    out.push(`<ul>${list.map((li) => `<li>${inline(li)}</li>`).join("")}</ul>`);
    list = [];
  };

  for (const line of lines) {
    // 围栏代码块：开合都靠行首 ```，块内内容不做任何行内处理
    if (/^\s*```/.test(line)) {
      if (fence === null) {
        flushPara();
        flushList();
        fence = [];
      } else {
        out.push(`<pre><code>${fence.join("\n")}</code></pre>`);
        fence = null;
      }
      continue;
    }
    if (fence !== null) {
      fence.push(line);
      continue;
    }

    if (line.trim() === "") {
      flushPara();
      flushList();
      continue;
    }

    const heading = /^#{1,6}\s+(.*)$/.exec(line);
    if (heading) {
      flushPara();
      flushList();
      out.push(`<h2>${inline(heading[1].trim())}</h2>`);
      continue;
    }

    const item = /^\s*[-*]\s+(.*)$/.exec(line);
    if (item) {
      flushPara();
      list.push(item[1].trim());
      continue;
    }

    flushList();
    para.push(line.trim());
  }

  // 收尾：未闭合的围栏也要输出，不能吞内容
  flushPara();
  flushList();
  if (fence !== null && fence.length > 0) {
    out.push(`<pre><code>${fence.join("\n")}</code></pre>`);
  }

  return out.join("\n");
}
