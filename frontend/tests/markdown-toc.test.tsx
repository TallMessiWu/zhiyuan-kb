import { StrictMode } from "react";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown } from "../src/lib/markdown";

function expectLocalTargets(root: HTMLElement) {
  const headings = [...root.querySelectorAll("h1,h2,h3,h4,h5,h6")];
  const links = [...root.querySelectorAll("nav a")];
  expect(links).toHaveLength(headings.length);
  links.forEach((link, index) => {
    const id = decodeURIComponent(link.getAttribute("href")!.slice(1));
    expect(id).not.toBe("");
    expect(document.getElementById(id)).toBe(headings[index]);
  });
}

describe("Markdown 目录", () => {
  it.each(["[toc]", "[TOC]", "[ToC]", "  [toc]  "])("%s 生成全篇分级目录，保留后续正文", (marker) => {
    const { container } = render(<Markdown>{`${marker}\n\n# 概览\n\n### 跳级小节\n\n#### 细节\n\n## 部署\n\n正文\n\n# 附录`}</Markdown>);
    const nav = screen.getByRole("navigation", { name: "目录" });
    expect(within(nav).getAllByRole("link").map((a) => a.textContent)).toEqual(["概览", "跳级小节", "细节", "部署", "附录"]);
    expect(nav.querySelectorAll(":scope > ul > li")).toHaveLength(2);
    expect(nav.querySelectorAll(":scope > ul > li:first-child > ul > li")).toHaveLength(2);
    expect(nav.querySelector("ul ul ul a")?.textContent).toBe("细节");
    expect(container.querySelector("p nav, a a")).toBeNull();
    expect(screen.getByText("正文")).toBeTruthy();
    expectLocalTargets(container);
  });

  it("收录全部六级标题及 Setext 标题，不将目录自身算作标题", () => {
    const { container } = render(<Markdown>{"总览\n===\n\n[toc]\n\n环境\n---\n\n### 三级\n\n#### 四级\n\n##### 五级\n\n###### 六级"}</Markdown>);
    expect(screen.getAllByRole("heading")).toHaveLength(6);
    expect(screen.getAllByRole("link")).toHaveLength(6);
    expectLocalTargets(container);
  });

  it("重复、中文及纯符号标题的锚点唯一，同页多条回答互不串跳", () => {
    const source = "[toc]\n\n## 中文标题\n\n## 中文标题\n\n## !!!";
    const { container, rerender } = render(<StrictMode><Markdown>{source}</Markdown><Markdown>{source}</Markdown></StrictMode>);
    const roots = [...container.querySelectorAll<HTMLElement>(".markdown")];
    roots.forEach(expectLocalTargets);
    const ids = [...container.querySelectorAll("h2")].map((h) => h.id);
    expect(new Set(ids).size).toBe(6);
    rerender(<StrictMode><Markdown>{source + "\n\n正文更新"}</Markdown><Markdown>{source}</Markdown></StrictMode>);
    expect([...container.querySelectorAll("h2")].map((h) => h.id)).toEqual(ids);
  });

  it("标题格式提取为安全目录文字，不复制链接、图片或 HTML", () => {
    const { container } = render(<Markdown>{'[toc]\n\n## **加粗** `code` [文档](https://example.com) ![示意](image.png) $x^2$\n\n## <img src=x onerror="alert(1)">安全'}</Markdown>);
    const nav = screen.getByRole("navigation", { name: "目录" });
    expect(within(nav).getAllByRole("link").map((a) => a.textContent)).toEqual(["加粗 code 文档 示意 x^2", "安全"]);
    expect(nav.querySelector("img, code, a strong, script, [onerror], a a, .katex")).toBeNull();
    expect(container.querySelectorAll(".katex")).toHaveLength(1);
    expectLocalTargets(container);
  });

  it("不误识别行内文字、行内代码、代码块、转义、链接或 HTML 中的标记", () => {
    const source = "说明 [toc]\n\n`[toc]`\n\n```md\n[toc]\n# 代码标题\n```\n\n    [toc]\n\n\\[toc]\n\n[toc](https://example.com)\n\n<div>[toc]</div>\n\n## 正常标题";
    const { container } = render(<Markdown>{source}</Markdown>);
    expect(screen.queryByRole("navigation")).toBeNull();
    expect(container.querySelectorAll("pre code")).toHaveLength(2);
    expect(container.querySelector("p code")?.textContent).toBe("[toc]");
    expect(screen.getByRole("link").getAttribute("href")).toBe("https://example.com");
    expect(screen.getAllByRole("heading")).toHaveLength(1);
  });

  it("无标题时移除占位符，不生成空导航；无标记时不自动插入目录", () => {
    const { container, rerender } = render(<Markdown>{"[toc]\n\n只有正文"}</Markdown>);
    expect(screen.queryByRole("navigation")).toBeNull();
    expect(container.textContent?.trim()).toBe("只有正文");
    rerender(<Markdown>{"## 标题\n\n正文"}</Markdown>);
    expect(screen.queryByRole("navigation")).toBeNull();
    expect(screen.getByRole("heading").id).toBe("");
  });

  it("多个占位符共享本篇目录，正文切换后不保留旧目录", () => {
    const { container, rerender } = render(<Markdown>{"[toc]\n\n## 旧标题\n\n[TOC]"}</Markdown>);
    expect(screen.getAllByRole("navigation")).toHaveLength(2);
    expect(new Set(screen.getAllByRole("link").map((a) => a.getAttribute("href"))).size).toBe(1);
    rerender(<Markdown>{"[toc]\n\n## 新标题\n\n### 新子节"}</Markdown>);
    expect(screen.queryByText("旧标题")).toBeNull();
    expectLocalTargets(container);
  });
});
