import type { Element, ElementContent, Root, RootContent } from "hast";

const marker = /^\[toc\]$/i;

function element(tagName: string, children: ElementContent[] = [], properties: Element["properties"] = {}): Element {
  return { type: "element", tagName, properties, children };
}

/** 在 KaTeX 展开前提取文字，避免公式重复；不复制 HTML、嵌套链接或图片。 */
function label(node: RootContent): string {
  if (node.type === "text") return node.value;
  if (node.type !== "element") return "";
  if (node.tagName === "img") return String(node.properties.alt ?? "");
  return node.children.map(label).join("");
}

/** 只解释原文中独立成段的 [toc]，不预处理 Markdown 字符串或放开原始 HTML。 */
export function rehypeToc({ prefix, source }: { prefix: string; source: string }) {
  return (tree: Root) => {
    const headings: Element[] = [];
    const markers: { node: Element; parent: Root | Element }[] = [];
    function collect(parent: Root | Element) {
      for (const node of parent.children) {
        if (node.type !== "element") continue;
        // position 排除 GFM 自动生成的脚注标题等非正文标题。
        if (/^h[1-6]$/.test(node.tagName) && node.position) headings.push(node);
        if (
          node.tagName === "p" && node.children.length === 1 &&
          node.children[0].type === "text" && marker.test(node.children[0].value.trim()) &&
          node.position && marker.test(source.slice(node.position.start.offset, node.position.end.offset).trim())
        ) {
          markers.push({ node, parent });
        } else {
          collect(node);
        }
      }
    }
    collect(tree);
    if (!markers.length) return;

    const list = element("ul");
    const stack: { depth: number; item: Element; list: Element }[] = [];
    headings.forEach((heading, index) => {
      // 实例前缀 + 序号：中文/符号/重复标题以及同页多条回答都不会冲突。
      const id = `${prefix}-heading-${index + 1}`;
      heading.properties.id = id;
      const depth = Number(heading.tagName[1]);
      while (stack.length && stack[stack.length - 1].depth >= depth) stack.pop();
      const parent = stack[stack.length - 1];
      const target = parent?.list ?? list;
      if (parent && !target.children.length) parent.item.children.push(target);
      const item = element("li", [element("a", [
        { type: "text", value: label(heading).trim() || "（无标题）" },
      ], { href: `#${id}` })]);
      target.children.push(item);
      stack.push({ depth, item, list: element("ul") });
    });

    for (const { node, parent } of markers) {
      const index = parent.children.indexOf(node);
      if (!headings.length) {
        parent.children.splice(index, 1);
        continue;
      }
      parent.children.splice(index, 1, element("nav", [
        element("strong", [{ type: "text", value: "目录" }]),
        list,
      ], { className: ["markdown-toc"], ariaLabel: "目录" }));
    }
  };
}
