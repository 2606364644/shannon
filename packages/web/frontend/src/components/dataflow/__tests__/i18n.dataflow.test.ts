import { describe, it, expect } from "vitest";
import zh from "@/locales/zh.json";
import en from "@/locales/en.json";

// 白话文案快照（spec §5 白话文案规范 + Task 13 新增 key）。
// 守 zh 真中文 + zh/en key 对齐（漏翻陷阱：en 漏 key 时 t() 回落 key 名）。

const zhDf = (zh as { workspaceDetail: { dataflow: Record<string, string> } }).workspaceDetail.dataflow;
const enDf = (en as { workspaceDetail: { dataflow: Record<string, string> } }).workspaceDetail.dataflow;

describe("数据流白话文案 i18n（zh/en）", () => {
  it("zh 含「数据流」「打通」「剪断」白话口径（spec §5 文案表）", () => {
    expect(zhDf.title).toContain("数据流");
    expect(zhDf.branchVulnLabel).toContain("打通");
    expect(zhDf.branchVulnLabel).toContain("一路无有效防护");
    expect(zhDf.branchSafeLabel).toContain("剪断");
    expect(zhDf.branchSafeLabel).toContain("被拦下");
    expect(zhDf.noCodeHint).toContain("LLM 扫描的节点不带源码");
  });

  it("zh 禁用词不出现（贯通 / 污点 / 未被触及 直译腔）", () => {
    const joined = Object.values(zhDf).join(" ");
    expect(joined).not.toContain("贯通");
    expect(joined).not.toContain("污点");
    expect(joined).not.toContain("未被触及"); // sink 无枝到达的禁用词（spec §5 表）
  });

  it("Fix round 1 白话补齐：无输入到达 / 存储中转（spec §5 表逐条）", () => {
    expect(zhDf.sinkNoInput).toBe("无输入到达");
    expect(zhDf.storageRelayMark).toContain("存储中转");
    expect(zhDf.storageRelayFull).toContain("先存进数据库");
    expect(zhDf.storageRelayFull).toContain("读出来才发起请求");
    // en 侧同 key 存在（parity 测试已锁全集，此处抽查语义非空）
    expect(enDf.sinkNoInput).toContain("input");
    expect(enDf.storageRelayFull).toContain("database");
  });

  it("zh/en key 对齐（workspaceDetail.dataflow 全集一致）", () => {
    expect(Object.keys(enDf).sort()).toEqual(Object.keys(zhDf).sort());
  });

  it("Task 13 新增 key：目录三区分组 / 关卡链 / 排查过的入口 / VulnCard 跳转", () => {
    // 目录三区分组
    expect(zhDf.tocGroupTrees).toContain("漏洞数据流树");
    expect(zhDf.tocGroupControls).toContain("认证·授权风险");
    expect(zhDf.tocGroupSafe).toContain("排查过的入口");
    expect(zhDf.tocCounts).toContain("打通");
    expect(zhDf.tocCounts).toContain("剪断");
    // 关卡链（区 2）
    expect(zhDf.controlsTitle).toContain("认证 / 授权风险");
    expect(zhDf.controlsIntro).toContain("不画树");
    expect(zhDf.guardOk).toBe("正常");
    expect(zhDf.guardMissing).toBe("缺失");
    expect(zhDf.guardIneffective).toBe("失效");
    // 排查过的入口（区 3）
    expect(zhDf.safeTitle).toBe("排查过的入口");
    expect(zhDf.safeIntro).toContain("没有流向任何危险调用点");
    expect(zhDf.safeIntro).toContain("有起点");
    expect(zhDf.safeIntro).toContain("不成树");
    // VulnCard 跳转（vuln 命名空间）
    const zhVuln = (zh as unknown as { vuln: Record<string, string> }).vuln;
    expect(zhVuln.viewDataflow).toBe("查看数据流");
  });

  it("en 侧对应 key 存在且为英文（抽查非空 + 非 key 名回落）", () => {
    expect(enDf.tocGroupTrees).toContain("trees");
    expect(enDf.controlsTitle).toContain("Authentication");
    expect(enDf.safeTitle).toContain("Checked entries");
    expect(enDf.guardMissing).toBe("Missing");
    const enVuln = (en as unknown as { vuln: Record<string, string> }).vuln;
    expect(enVuln.viewDataflow).toContain("data flow");
  });

  it("Task 14 新增 key：筛选器（vuln_class 下拉 + 只看有漏洞 toggle）+ 图例条白话", () => {
    // 筛选器
    expect(zhDf.filterVulnOnly).toBe("只看有漏洞的");
    expect(zhDf.filterAll).toBe("全部");
    expect(zhDf.filterAllClasses).toContain("全部");
    expect(zhDf.filterClassLabel).toContain("漏洞类型");
    // 图例 5 项（spec §5 视觉语言表：打通/剪断/黄盾/绿盾/靶心双态）
    expect(zhDf.legendVuln).toContain("打通");
    expect(zhDf.legendVuln).toContain("漏洞链路");
    expect(zhDf.legendCut).toContain("剪断");
    expect(zhDf.legendCut).toContain("防护拦下");
    expect(zhDf.legendShieldBypass).toContain("防护被绕过");
    expect(zhDf.legendShieldEffective).toContain("有效防护");
    expect(zhDf.legendShieldEffective).toContain("剪断点");
    expect(zhDf.legendTarget).toContain("有打通枝到达");
    expect(zhDf.legendTarget).toContain("无输入到达");
    // en 侧语义非空（parity 测试已锁 zh/en key 全集一致）
    expect(enDf.filterVulnOnly).toContain("Vulnerable");
    expect(enDf.legendVuln).toContain("Breached");
    expect(enDf.legendShieldEffective).toContain("effective");
  });

  it("Fix round 0 新增 key：树区区头（标题 + 组织方式说明段，spec §5 区 1）", () => {
    expect(zhDf.treesTitle).toBe("漏洞数据流树");
    expect(zhDf.treesIntro).toContain("每个危险点（sink）一棵树");
    expect(zhDf.treesIntro).toContain("打通");
    expect(zhDf.treesIntro).toContain("剪断=防护拦下");
    expect(zhDf.treesIntro).toContain("断得越靠右说明输入走得越远");
    expect(enDf.treesTitle).toContain("trees");
    expect(enDf.treesIntro).toContain("One tree per dangerous point");
  });
});
