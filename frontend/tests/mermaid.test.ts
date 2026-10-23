import { beforeEach, describe, expect, it, vi } from "vitest";
import mermaid from "mermaid";
import { renderMermaid } from "../src/lib/mermaid-renderer";

vi.mock("mermaid", () => ({
  default: {
    initialize: vi.fn(), render: vi.fn(),
    mermaidAPI: { defaultConfig: { securityLevel: "strict", secure: [], themeCSS: "", theme: "default", htmlLabels: true } },
  },
}));

beforeEach(() => {
  vi.mocked(mermaid.render).mockReset();
});

describe("Mermaid 渲染隔离", () => {
  it("SVG 插入前清洗脚本、事件、HTML、链接与图片；保留几何和样式", async () => {
    vi.mocked(mermaid.render).mockImplementation(async () => ({ svg: '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><style>text{fill:red}</style><script>alert(1)</script><foreignObject><div>bad</div></foreignObject><a href="javascript:alert(1)">link</a><image href="https://evil.test/a.png"/><text onclick="alert(1)">安全标签</text><path d="M0 0L10 10"/></svg>', diagramType: "flowchart-v2" }));
    const svg = await renderMermaid("flowchart LR\nA --> B", false);
    const container = document.createElement("div");
    container.innerHTML = svg;
    expect(container.querySelector("script, foreignObject, a, image, [onload], [onclick]")).toBeNull();
    expect(container.querySelector("text")?.textContent).toBe("安全标签");
    expect(container.querySelector("style")).not.toBeNull();
    expect(container.querySelector("path")).not.toBeNull();
    expect(mermaid.initialize).toHaveBeenCalledWith(expect.objectContaining({
      securityLevel: "strict", startOnLoad: false, htmlLabels: false,
      secure: expect.arrayContaining(["securityLevel", "secure", "themeCSS", "htmlLabels"]),
    }));
    expect(document.querySelector(".mermaid-measure")).toBeNull();
  });

  it("initialize+render 串行执行、ID 唯一，失败清理测量节点且不阻塞下一张图", async () => {
    const order: string[] = [];
    const ids: string[] = [];
    let failFirst!: (reason: Error) => void;
    vi.mocked(mermaid.render).mockImplementation(async (id, source, container) => {
      order.push(source);
      ids.push(id);
      expect(container?.isConnected).toBe(true);
      if (source === "bad") return new Promise((_, reject) => { failFirst = reject; });
      return { svg: "<svg><text>ok</text></svg>", diagramType: "flowchart-v2" };
    });
    const first = renderMermaid("bad", false);
    const failed = expect(first).rejects.toThrow("bad graph");
    const second = renderMermaid("good", true);
    await vi.waitFor(() => expect(order).toEqual(["bad"]));
    expect(mermaid.initialize).toHaveBeenCalledTimes(1);
    failFirst(new Error("bad graph"));
    await failed;
    await expect(second).resolves.toContain("ok");
    expect(order).toEqual(["bad", "good"]);
    expect(new Set(ids).size).toBe(2);
    expect(document.querySelector(".mermaid-measure")).toBeNull();
  });
});
