import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import Home from "./pages/Home";
import Search from "./pages/Search";
import Ask from "./pages/Ask";
import AssetDetail from "./pages/AssetDetail";
import Capture from "./pages/Capture";
import Review from "./pages/Review";
import Dashboard from "./pages/Dashboard";
import { CURRENT_USER, get } from "./api/client";
import type { HomeResponse } from "./types";

// 布局与导航对照 prototype/kms-prototype.html 的 #app / #sidebar / nav.menu / .side-note。
// 样式全部走 theme.css 的类，不再写内联样式。
// 图标 ic 与文案照抄原型 NAV；「首页 · 搜索」在 /search 下同样高亮（原型 match 规则）。
const NAV = [
  { to: "/", ic: "⌕", label: "首页 · 搜索", end: true, also: (p: string) => p.startsWith("/search") },
  { to: "/ask", ic: "◈", label: "AI 问答", end: false },
  { to: "/capture", ic: "＋", label: "沉淀记录", end: false },
  { to: "/review", ic: "！", label: "复核队列", end: false },
  { to: "/dashboard", ic: "▤", label: "数据看板", end: false },
];

export default function App() {
  const { pathname } = useLocation();
  // 复核队列角标：REVIEW_DUE 资产数（原型 NAV badge 的口径）。挂载时拉一次；
  // Review 页处理完任务后广播 zy:review-changed，这里重拉 —— 不引全局状态库。
  const [reviewDue, setReviewDue] = useState(0);
  useEffect(() => {
    const load = () =>
      get<HomeResponse>("/home")
        .then((r) => setReviewDue(r.stats.review_due))
        .catch(() => {}); // 角标是锦上添花，后端没起时不打扰页面
    load();
    window.addEventListener("zy:review-changed", load);
    return () => window.removeEventListener("zy:review-changed", load);
  }, []);

  return (
    <div id="app">
      <aside id="sidebar">
        <div className="brand">
          <div className="t">
            知<em>源</em>
          </div>
          <div className="s">INFERENCE KB · 内部</div>
        </div>
        <nav className="menu">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) =>
                isActive || n.also?.(pathname) ? "on" : ""
              }
            >
              <span className="ic">{n.ic}</span>
              {n.label}
              {n.to === "/review" && reviewDue > 0 && (
                <span className="bdg">{reviewDue}</span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="side-note">
          当前用户 <span className="u">王磊 {CURRENT_USER}</span>
          <br />
          方向：vLLM Ascend / SGLang
        </div>
      </aside>
      <main id="main">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/search" element={<Search />} />
          <Route path="/ask" element={<Ask />} />
          <Route path="/asset/:id" element={<AssetDetail />} />
          <Route path="/capture" element={<Capture />} />
          <Route path="/review" element={<Review />} />
          <Route path="/dashboard" element={<Dashboard />} />
        </Routes>
      </main>
    </div>
  );
}
