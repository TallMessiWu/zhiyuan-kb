import { useEffect, useState } from "react";

function isDarkTheme(): boolean {
  const theme = document.documentElement.dataset.theme;
  return theme === "dark" || (theme !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
}

type Result = { source: string; dark: boolean; svg: string; failed: boolean };

export function MermaidDiagram({ source }: { source: string }) {
  const [dark, setDark] = useState(isDarkTheme);
  const [result, setResult] = useState<Result | null>(null);

  useEffect(() => {
    const update = () => setDark(isDarkTheme());
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    media.addEventListener("change", update);
    update();
    return () => {
      observer.disconnect();
      media.removeEventListener("change", update);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    // Mermaid 体积较大，仅在正文真的包含图表时加载；普通文档不下载它。
    void import("./mermaid-renderer").then(async ({ renderMermaid }) => {
      if (cancelled) return;
      const svg = await renderMermaid(source, dark);
      if (!cancelled) setResult({ source, dark, svg, failed: false });
    }).catch(() => {
      if (!cancelled) setResult({ source, dark, svg: "", failed: true });
    });
    // 切换资产/主题、卸载或 StrictMode 重放时，不允许旧结果覆盖新内容。
    return () => { cancelled = true; };
  }, [source, dark]);

  const current = result?.source === source && result.dark === dark ? result : null;
  return (
    <figure className="mermaid-block" aria-label="Mermaid 图表" aria-busy={!current}>
      {current && !current.failed ? (
        <div
          className="mermaid-diagram"
          tabIndex={0}
          role="region"
          aria-label="图表，可横向滚动"
          dangerouslySetInnerHTML={{ __html: current.svg }}
        />
      ) : (
        <>
          <figcaption role="status" className={current?.failed ? "mermaid-error" : "mermaid-status"}>
            {current?.failed ? "图表渲染失败，请检查 Mermaid 语法；源码已保留。" : "正在渲染图表…"}
          </figcaption>
          <pre><code className="language-mermaid">{source}</code></pre>
        </>
      )}
    </figure>
  );
}
