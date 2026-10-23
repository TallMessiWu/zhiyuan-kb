import { beforeAll, describe, expect, it } from "vitest";
import mermaid from "mermaid";
import { renderMermaid } from "../src/lib/mermaid-renderer";

// 只替代 jsdom 缺少的 SVG 测量；解析器、布局、SVG 输出与清洗均用真实依赖。
// 这验证 DOM 集成，不替代真实浏览器的排版/截图验收。
beforeAll(() => {
  Object.defineProperty(SVGElement.prototype, "getBBox", {
    configurable: true,
    value() { return { x: 0, y: 0, width: 100, height: 20 }; },
  });
  Object.defineProperty(SVGElement.prototype, "getComputedTextLength", {
    configurable: true,
    value() { return (this.textContent?.length ?? 0) * 8; },
  });
});

describe("真实 Mermaid 引擎", () => {
  it.each([
    ["流程图", "flowchart LR\nA[提问] --> B{找到知识?}\nB -->|是| C[回答]"],
    ["时序图", "sequenceDiagram\n用户->>知识库: 搜索\n知识库-->>用户: 返回知识"],
    ["状态图", "stateDiagram-v2\n[*] --> DRAFT\nDRAFT --> VERIFIED: 人工验证"],
  ])("%s 生成可插入的 SVG", async (_, source) => {
    const svg = await renderMermaid(source, false);
    const container = document.createElement("div");
    container.innerHTML = svg;
    expect(container.querySelector("svg[viewBox]")).not.toBeNull();
    expect(container.querySelector("text")).not.toBeNull();
    expect(container.querySelector("foreignObject, script")).toBeNull();
    expect(document.querySelector(".mermaid-measure")).toBeNull();
  });

  it("文档 init/frontmatter 不得放宽安全选项或注入主题 CSS", async () => {
    const source = '---\nconfig:\n  securityLevel: loose\n  htmlLabels: true\n  themeCSS: "body{display:none}"\n---\nflowchart LR\nA --> B';
    const svg = await renderMermaid(source, true);
    expect(mermaid.mermaidAPI.getConfig().securityLevel).toBe("strict");
    expect(mermaid.mermaidAPI.getConfig().htmlLabels).toBe(false);
    expect(svg).not.toContain("body{display:none}");
    expect(svg).not.toContain("foreignObject");
    expect(mermaid.mermaidAPI.getConfig().themeVariables.darkMode).toBe(true);
  });

  it("语法错误不遗留错误图层，并能继续渲染下一张图", async () => {
    const before = document.body.innerHTML;
    await expect(renderMermaid("not a diagram", false)).rejects.toThrow();
    expect(document.body.innerHTML).toBe(before);
    await expect(renderMermaid("graph TD\nA --> B", false)).resolves.toContain("<svg");
  });
});
