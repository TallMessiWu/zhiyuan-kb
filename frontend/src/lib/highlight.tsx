// 命中词高亮。规则照搬 prototype 的 highlight(text, terms)：
// 词表由后端给（GET /search 的 terms，已滤掉单字），前端只负责套 <mark>。
// 不用 dangerouslySetInnerHTML —— 标题和摘要都是用户内容，走 React 节点天然免疫 XSS。

const ESCAPE = /[.*+?^${}()|[\]\\]/g;

export function Highlight({ text, terms }: { text: string; terms: string[] }) {
  if (!text || terms.length === 0) return <>{text}</>;

  const pattern = new RegExp(`(${terms.map((t) => t.replace(ESCAPE, "\\$&")).join("|")})`, "gi");
  // 带捕获组的 split：奇数下标就是命中片段
  const chunks = text.split(pattern);
  return (
    <>
      {chunks.map((chunk, i) =>
        i % 2 === 1 ? <mark key={i}>{chunk}</mark> : <span key={i}>{chunk}</span>,
      )}
    </>
  );
}
