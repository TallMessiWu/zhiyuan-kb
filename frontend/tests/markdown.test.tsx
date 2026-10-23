import { StrictMode } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Markdown } from "../src/lib/markdown";
import * as mermaidRenderer from "../src/lib/mermaid-renderer";

// Spy on the loaded module: Vitest 3's factory mock can be bypassed by concurrent dynamic imports.
const renderer = vi.spyOn(mermaidRenderer, "renderMermaid");
const fence = (source: string, language = "mermaid") => `\`\`\`${language}\n${source}\n\`\`\``;

beforeEach(() => {
  renderer.mockReset();
  renderer.mockImplementation(async (source) => `<svg role="img"><text>${source}</text></svg>`);
});

describe("共用 Markdown", () => {
  it("渲染普通 Markdown，保留单换行、代码中的原始符号和未闭合代码块", () => {
    const { container } = render(<Markdown>{"## 标题\n\n正文\n换行 **强调**\n\n- 第一项\n- 第二项\n\n`$x$ **code**`\n\n```python\nif x < 2:\n  print('$x$')"}</Markdown>);
    expect(screen.getByRole("heading", { name: "标题" }).tagName).toBe("H2");
    expect(container.querySelector("br")).not.toBeNull();
    expect(container.querySelector("strong")?.textContent).toBe("强调");
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelector("p code")?.textContent).toBe("$x$ **code**");
    expect(container.querySelector("pre code")?.textContent).toContain("if x < 2:\n  print('$x$')");
    expect(container.querySelector(".katex")).toBeNull();
    expect(renderer).not.toHaveBeenCalled();
  });

  it("支持表格、链接和嵌套列表", () => {
    const { container } = render(<Markdown>{"[文档](https://example.com)\n\n| 参数 | 值 |\n| --- | --- |\n| batch | 2 |\n\n1. 步骤\n   - 子项"}</Markdown>);
    expect(screen.getByRole("link").getAttribute("href")).toBe("https://example.com");
    expect(container.querySelector("table td")?.textContent).toBe("batch");
    expect(container.querySelector("ol ul li")?.textContent).toBe("子项");
  });

  it("渲染行内、独立 $$ 公式块与 math 围栏，保留 MathML", () => {
    const source = "行内 $E=mc^2$。\n\n$$\n\\frac{a_1}{b^2} + \\sqrt{x}\n$$\n\n" + fence("\\sum_{i=1}^{n} i", "math");
    const { container } = render(<Markdown>{source}</Markdown>);
    expect(container.querySelectorAll(".katex")).toHaveLength(3);
    expect(container.querySelectorAll(".katex-display")).toHaveLength(2);
    expect(container.querySelectorAll("math")).toHaveLength(3);
    expect(container.querySelector("annotation")?.textContent).toBe("E=mc^2");
  });

  it("错误公式保留源码，不影响后续正文", () => {
    const { container } = render(<Markdown>{"$$\n\\frac{\n$$\n\n后续正文"}</Markdown>);
    expect(container.querySelector(".katex-error")?.textContent).toContain("\\frac{");
    expect(screen.getByText("后续正文")).toBeTruthy();
  });

  it("原始 HTML、危险 URL 和 KaTeX HTML 扩展不执行", () => {
    const { container } = render(<Markdown>{'<script>alert(1)</script>\n\n<img src=x onerror="alert(1)">\n\n[危险](javascript:alert%281%29)\n\n$\\href{javascript:alert(1)}{危险}$\n\n$\\htmlStyle{background:red}{x}$'}</Markdown>);
    expect(container.querySelector("script, img, [onerror], [style*='background:red']")).toBeNull();
    expect([...container.querySelectorAll("a")].every((a) => !a.getAttribute("href")?.startsWith("javascript:"))).toBe(true);
  });

  it("同页多图在 StrictMode 下正常渲染，不嵌套在 pre 里", async () => {
    const { container } = render(<StrictMode><Markdown>{fence("flowchart LR\nA --> B") + "\n\n" + fence("sequenceDiagram\nA->>B: Hello", "Mermaid")}</Markdown></StrictMode>);
    await waitFor(() => expect(container.querySelectorAll(".mermaid-diagram svg")).toHaveLength(2));
    expect(container.querySelector("pre figure")).toBeNull();
  });

  it("图表失败时保留源码，其它图表和正文仍能显示", async () => {
    renderer.mockRejectedValueOnce(new Error("syntax error"));
    const { container } = render(<Markdown>{fence("broken <script>") + "\n\n正文\n\n" + fence("flowchart LR\nA --> B")}</Markdown>);
    await screen.findByText(/图表渲染失败/);
    expect(container.querySelector("pre code")?.textContent).toBe("broken <script>");
    await waitFor(() => expect(container.querySelectorAll(".mermaid-diagram svg")).toHaveLength(1));
    expect(screen.getByText("正文")).toBeTruthy();
  });

  it("切换正文时不显示迟到的旧图；主题变化重新渲染", async () => {
    let resolveOld!: (svg: string) => void;
    renderer.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
    const { container, rerender } = render(<Markdown>{fence("old")}</Markdown>);
    await waitFor(() => expect(renderer).toHaveBeenCalledWith("old", false));
    rerender(<Markdown>{fence("new")}</Markdown>);
    await waitFor(() => expect(container.querySelector("svg text")?.textContent).toBe("new"));
    await act(async () => { resolveOld("<svg><text>old</text></svg>"); });
    expect(container.querySelector("svg text")?.textContent).toBe("new");
    await act(async () => { document.documentElement.dataset.theme = "dark"; });
    await waitFor(() => expect(renderer).toHaveBeenLastCalledWith("new", true));
  });

  it("空文档不触发图表加载", () => {
    const { container } = render(<Markdown>{""}</Markdown>);
    expect(container.textContent).toBe("");
    expect(renderer).not.toHaveBeenCalled();
  });
});
