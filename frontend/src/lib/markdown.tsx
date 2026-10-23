import { memo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { MermaidDiagram } from "./MermaidDiagram";
import "katex/dist/katex.min.css";

const components: Components = {
  pre({ node, children }) {
    const code = node?.children[0];
    if (
      code?.type === "element" && code.tagName === "code" &&
      Array.isArray(code.properties.className) &&
      code.properties.className.some((name) => String(name).toLowerCase() === "language-mermaid")
    ) {
      const source = code.children.map((child) => child.type === "text" ? child.value : "").join("");
      // 在 pre 层替换，避免生成 <pre><div> 或 <p><div> 这样的非法嵌套。
      return <MermaidDiagram source={source.trimEnd()} />;
    }
    return <pre>{children}</pre>;
  },
};

/** 用户原始 HTML 不执行；保留默认安全 URL 过滤，KaTeX 不信任 HTML/链接命令。 */
export const Markdown = memo(function Markdown({ children }: { children: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath, remarkBreaks]}
        rehypePlugins={[[rehypeKatex, { trust: false, strict: "ignore", maxSize: 20, maxExpand: 1000 }]]}
        components={components}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
});
