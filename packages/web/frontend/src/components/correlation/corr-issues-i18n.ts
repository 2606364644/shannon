import type { TFunction } from "i18next";

/** 渲染层 i18n 映射（D7）：D1 的 correlation-yaml.ts（validateForm/yamlToForm/CorrYamlError）
 *  产出硬编码中文 issue 字符串——该 lib 模块测试锁定不改写，故在组件渲染前把已知 issue
 *  映射为 t() 词条；带动态片段（仓库名/服务名/YAML 报错文本）经模板插值，未知字符串
 *  （如裸 TypeError message）原样透传。词条见 locales 的 scan.correlation.issues.*。 */
export function formatCorrIssue(msg: string, t: TFunction): string {
  if (msg === "至少需要一个 entrypoint 仓库") return t("scan.correlation.issues.needEntrypoint");
  if (msg === "存在未命名的仓库卡片") return t("scan.correlation.issues.unnamedRepo");
  if (msg === "缺少 repos 段") return t("scan.correlation.issues.missingRepos");
  const dupPrefix = "仓库重复: ";
  if (msg.startsWith(dupPrefix)) return t("scan.correlation.issues.dupRepo", { name: msg.slice(dupPrefix.length) });
  const noSourceSuffix = " 缺少来源";
  if (msg.startsWith("仓库 ") && msg.endsWith(noSourceSuffix)) {
    return t("scan.correlation.issues.repoNoSource", { name: msg.slice("仓库 ".length, msg.length - noSourceSuffix.length) });
  }
  const undeclaredPrefix = "relations 引用未声明服务: ";
  if (msg.startsWith(undeclaredPrefix)) {
    return t("scan.correlation.issues.undeclaredService", { name: msg.slice(undeclaredPrefix.length) });
  }
  const yamlPrefix = "YAML 语法错误: ";
  if (msg.startsWith(yamlPrefix)) return t("scan.correlation.issues.yamlSyntax", { msg: msg.slice(yamlPrefix.length) });
  return msg;
}
