// 账号 → 展示名。
//
// 库里的 author_id / validator_id / user_id 一律是 ASCII 账号：它们要经 X-User 请求头传递，
// HTTP 头不能放非 ASCII。原型里展示的是中文名，所以在展示层做一次映射。
// V1.1 接 SSO 后这张表由用户服务提供，届时删掉本文件。

const DISPLAY_NAMES: Record<string, string> = {
  wanglei: "王磊",
  chenyuwei: "陈雨薇",
  sunxiaodong: "孙晓东",
  lihao: "李昊",
  zhangqiyuan: "张启元",
};

/** 认不出的账号原样返回，不猜、不拼。 */
export function userName(userId: string): string {
  return DISPLAY_NAMES[userId] ?? userId;
}
