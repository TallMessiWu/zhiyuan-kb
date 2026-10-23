import mermaid from "mermaid";
import DOMPurify from "dompurify";

let nextId = 0;
let queue = Promise.resolve();

/** initialize 与 render 共用全局配置，整个操作串行，避免多图/主题切换相互污染。 */
export function renderMermaid(source: string, dark: boolean): Promise<string> {
  const job = queue.then(async () => {
    const style = getComputedStyle(document.documentElement);
    const color = (name: string) => style.getPropertyValue(name).trim();
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      suppressErrorRendering: true,
      // 文档中的 init/frontmatter 不得重写站点配置（含安全等级、CSS、HTML 标签）。
      secure: [...Object.keys(mermaid.mermaidAPI.defaultConfig), "htmlLabels", "dompurifyConfig"],
      theme: "base",
      themeVariables: {
        darkMode: dark,
        background: color("--surface"),
        primaryColor: color("--accent-weak"),
        primaryTextColor: color("--ink"),
        primaryBorderColor: color("--accent-line"),
        lineColor: color("--ink2"),
        secondaryColor: color("--surface2"),
        tertiaryColor: color("--surface3"),
        fontFamily: color("--sans"),
      },
      htmlLabels: false,
      flowchart: { htmlLabels: false, useMaxWidth: false },
      sequence: { useMaxWidth: false },
    });

    // Mermaid 需要已挂载 DOM 测量 SVG（折叠/隐藏的正文也不能直接拿来测量）。
    const container = document.createElement("div");
    container.className = "mermaid-measure";
    container.setAttribute("aria-hidden", "true");
    document.body.append(container);
    try {
      const { svg } = await mermaid.render(`zy-mermaid-${++nextId}`, source, container);
      // 不绑定 Mermaid click 回调；插入前再清洗一次 SVG，禁用 HTML、链接和图片标签。
      return DOMPurify.sanitize(svg, {
        USE_PROFILES: { svg: true, svgFilters: true },
        FORBID_TAGS: ["foreignObject", "a", "image"],
      });
    } finally {
      container.remove();
    }
  });
  // 一个坏图不能阻塞后续图表。
  queue = job.then(() => undefined, () => undefined);
  return job;
}
