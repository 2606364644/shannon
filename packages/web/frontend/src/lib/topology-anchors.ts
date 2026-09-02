/** 拓扑图边端点几何（编辑画布 + 结果视图共用）。
 *
 * 背景：画布是自由拖放工作台，边端点若写死「from 右缘 → to 左缘」（沿左右列布局
 * 假设），用户把 to 拖到 from 左侧后连线会横穿节点本体或反向折返，方向感错乱。
 * anchorPair 按两节点中心连线的主轴方向选「面向对方」的一侧中点，保证箭头永远
 * 指向目标节点朝向来源的那一面。凸矩形 + 直线在主轴分离时线段落在两节点之间的
 * 空带，不穿本体（节点被用户手工叠放时除外）。
 *
 * 纯函数无 React 依赖，导出便于单测。
 */

export interface Box {
  /** 左上角 x（SVG 坐标） */
  x: number;
  /** 左上角 y（SVG 坐标） */
  y: number;
  w: number;
  h: number;
}

export interface Point {
  x: number;
  y: number;
}

/** 选中点：水平主轴取左右缘中点，垂直主轴取上下缘中点（取面向对方的那一侧）。 */
export function anchorPair(a: Box, b: Box): { from: Point; to: Point } {
  const ac: Point = { x: a.x + a.w / 2, y: a.y + a.h / 2 };
  const bc: Point = { x: b.x + b.w / 2, y: b.y + b.h / 2 };
  const dx = bc.x - ac.x;
  const dy = bc.y - ac.y;
  if (Math.abs(dx) >= Math.abs(dy)) {
    // 水平主轴：from 在左 → 右缘→to 左缘；from 在右 → 左缘→to 右缘
    return dx >= 0
      ? { from: { x: a.x + a.w, y: ac.y }, to: { x: b.x, y: bc.y } }
      : { from: { x: a.x, y: ac.y }, to: { x: b.x + b.w, y: bc.y } };
  }
  // 垂直主轴：from 在上 → 下缘→to 上缘；from 在下 → 上缘→to 下缘
  return dy >= 0
    ? { from: { x: ac.x, y: a.y + a.h }, to: { x: bc.x, y: b.y } }
    : { from: { x: ac.x, y: a.y }, to: { x: bc.x, y: b.y + b.h } };
}
