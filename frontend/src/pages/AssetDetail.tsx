import { useParams } from "react-router-dom";

// 知识详情页：正文 + 右栏（适用环境/关联代码/Issue·PR/验证记录/复用记录/版本历史）
// + 底部常驻三键反馈条（有用/可能过时/没找到）。UI 对照 prototype 的 renderAsset()。
// 实现于 M1（详情）+ M3（反馈条）。
export default function AssetDetail() {
  const { id } = useParams();
  return <h1>知识详情 #{id}（M1 实现）</h1>;
}
