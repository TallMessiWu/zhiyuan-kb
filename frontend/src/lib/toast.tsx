import { useEffect, useState, type ReactNode } from "react";

// 右下角轻提示（对照 prototype 的 toast()）。三键反馈、记缺口、认领都要在一次点击后给回执，
// 所以实现只留这一份：两份会在停留时长、层级、堆叠规则上慢慢漂开。
const DURATION_MS = 5200;

export function useToast() {
  const [toast, setToast] = useState<ReactNode>(null);

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), DURATION_MS);
    return () => window.clearTimeout(t);
  }, [toast]);

  const toastBox = toast ? (
    <div className="toastbox">
      <div className="toast">{toast}</div>
    </div>
  ) : null;

  return { setToast, toastBox };
}
