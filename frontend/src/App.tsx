import { NavLink, Route, Routes } from "react-router-dom";
import Home from "./pages/Home";
import Search from "./pages/Search";
import Ask from "./pages/Ask";
import AssetDetail from "./pages/AssetDetail";
import Capture from "./pages/Capture";
import Review from "./pages/Review";
import Dashboard from "./pages/Dashboard";

// 布局与导航对照 prototype/kms-prototype.html：左侧窄导航 + 主内容区。
// M1 时把原型的 CSS 变量（双主题 token）抽到 src/theme.css。
const NAV = [
  { to: "/", label: "首页 · 搜索" },
  { to: "/ask", label: "AI 问答" },
  { to: "/capture", label: "沉淀记录" },
  { to: "/review", label: "复核队列" },
  { to: "/dashboard", label: "数据看板" },
];

export default function App() {
  return (
    <div style={{ display: "flex", minHeight: "100vh", fontFamily: "system-ui, sans-serif" }}>
      <aside style={{ width: 198, borderRight: "1px solid #ddd", padding: 12 }}>
        <div style={{ fontWeight: 700, marginBottom: 12 }}>知源</div>
        <nav style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to}>
              {n.label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main style={{ flex: 1, padding: 24 }}>
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
