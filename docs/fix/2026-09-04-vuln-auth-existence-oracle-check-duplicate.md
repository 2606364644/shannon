# [Fix Report] libs `/api/open-account/check-duplicate` certId 开户状态枚举漏报

| 项 | 值 |
|---|---|
| 报告日期 | 2026-09-04 |
| 类型 | 检出能力缺口(detection gap),非基础设施故障 |
| 基线 session | `libs-20260903-084517`(__legacy__ 工作区,2026-09-03) |
| 修复 commit | `afb5ec7e`(prompts/vuln-auth.txt +11 行) |
| 状态 | **修复已部署 · 复扫验证待执行(R 段待回填)** |
| 影响面 | 所有白盒扫描中「业务功能构成存在性预言机」类漏洞的 auth 轨检出 |

---

## 摘要(Executive Summary)

外部安全测试确认 libs 仓库 `POST /api/open-account/check-duplicate` 存在用户枚举漏洞:更换 `certId`(泰国身份证号)可从响应差异匿名判断任意证件号是否已开户。基线扫描(2026-09-03)未检出——报告仅有一条把该接口淹没在 19 条路由清单里的「未授权访问」打包 finding(AUTHZ-VULN-18),枚举危害零字描述,等效漏报。

根因不是能力缺失,而是**分类学缝隙**:该漏洞不是 taint、不是 IDOR、missing-authn 框子装不下(无鉴权是开户前接口的业务设计),而 vuln-auth 方法论中的枚举检查只锚定「login/signup 错误信息」与「密码重置」两个场景——「业务功能本身构成存在性预言机」没有任何检查项挂载。同轮扫描对 `verify-profile` 报出「PII 探测预言机」(AUTH-VULN-02)证明 agent 具备完全相同的推理能力,缺的只是锚点。

修复:在 vuln-auth 方法论新增 §10「Existence oracles」检查节(触发词 + 四条件判定 + safe 准则),schema 零改动,不触碰「确定性产物不喂 LLM 轨」铁律。修复已随 worker 镜像 rebuild 部署,**复扫验收由用户执行后回填 R 段**。

---

## S — Situation(情境)

### 业务与漏洞

目标为券商开户 BFF(TS/Node,`@futu/agudo` 框架,多 business 服务)。开户流程含匿名「查重」预检接口:

- **接口**:`POST /api/open-account/check-duplicate`,公共路由,**无鉴权中间件**(`business/open-account-service/src/agudo-plugin/app/router.ts:114`)——开户前用户尚无登录态,无鉴权本身是业务设计,不是缺陷;
- **漏洞**(外部安全测试确认):入参 `certificate.certNo` 等身份标识符,响应按「是否已开户」分叉——未开户返回空 `{}`,已开户返回 `{duplicateCid, phone:{number, checked}}`;更换任意泰国身份证号即可枚举开户状态。

### 漏洞形态(源码,`openAccountNiceService.ts:620-661`)

```ts
const mask = params.mask !== false;        // ① 掩码开关客户端可控
const firstCid = parsed.data.duplicateCids[0];
if (!firstCid) return ok({});              // ② 存在性分叉:空响应
const duplicateCid = mask ? maskMiddle(cidStr) : cidStr;   // ③ mask=false 拿全裸客户号
return { duplicateCid, phone }             // ④ 命中后回查该账户预留手机号
```

三层危害:**存在性预言机/用户枚举**(CWE-204,核心)· **全裸客户号**(mask 客户端可控)· **回显他人手机号**(BOLA 成分,CWE-639)。

### 漏报现象(基线 session 实证)

| 检查点 | 结果 |
|---|---|
| recon 路由清单 | ✅ 有,但整行 `\| None \| None \| None \| Duplicate check. No auth middleware` —— certId、响应差异、mask 语义全部未记录 |
| auth 轨 agent log | ❌ `check-duplicate`/`certId`/`enumerat` 出现 **0 次**;7 条 finding 全部集中在 verify-service/upstream-bind |
| authz GitNexus queue | ❌ 候选数 0(整轨本仓无产出) |
| authz LLM 轨 | ⚠️ 触及(log 7 次),但打包进 AUTHZ-VULN-18「19 条公共路由无鉴权」(high),PoC 验证的是 `upsert-profile`;check-duplicate 的枚举危害在报告中不可见 |
| 最终报告 | ❌ 无任何 finding 描述「certId 可枚举开户状态」 |

**产品语义确认**(用户):同接口「未授权」与「枚举」是**两个独立漏洞,并存不替代**——未授权反而放大枚举的可利用性;attack-chain 可组合两者,但前置是各 vuln agent 各自检出。打包式 finding 淹没单路由独特危害 = 等效漏报。

---

## T — Task(任务)

1. **定位漏报根因**:四条检测轨(GitNexus taint / GitNexus authz IDOR / authz LLM / auth LLM)逐轨确认是否触及、卡在哪一环;
2. **设计并实施修复**:使 auth agent 在不破坏既有架构铁律的前提下能检出此类漏洞,且与 AUTHZ-VULN-18 类未授权 finding 独立并存;
3. **建立可执行的验收标准**:以 libs 复扫为 oracle,明确「检出的判定特征」与「未检出时的迭代路径」。

---

## A — Action(行动)

### A1 根因调查(证据链)

**四轨 autopsy:**

| 轨 | 触及 | 为什么漏 |
|---|---|---|
| GitNexus 确定性(inj/xss/ssrf) | 否 | certId 不流向危险 sink,非 taint 形态——架构性不覆盖 |
| GitNexus authz(IDOR) | 否 | `authz_gitnexus_queue.json` 候选=0。certId 是**查询探针**而非对象引用(`_REQ_REF_RE` 只抓 `req.params.xxx` 类资源引用);漏洞在**响应回显**而非 sink 路径;无鉴权使其脱离 IDOR「已认证越权」形态(无 caller 无从比对归属) |
| authz LLM(vuln-authz) | 是 | 判定轴是「归属门」:对这类接口问的是「cid 是否绑 caller」(log 09:36/09:44 实证),不问「响应回显了什么」。modify-profile 的同名接口(cid 强制=ctx.user.uid)被判 SAFE 进 dismissed——判定轴本身无错,但对该漏洞形态**没有问题可问** |
| auth LLM(vuln-auth) | 否 | 见下「三层衰减」。**最应命中的一轨** |

**auth 轨三层衰减**(核心根因;模型:agent 行为 = 方法论锚点 × 输入线索,该接口两项皆零):

1. **类别缺口(根因层)**——vuln-auth 9 类方法论的枚举检查只锚定 §7「login/signup 错误信息 generic」与 §8「密码重置 avoid user enumeration」;§2 速率限制的端点筛选清单是「login, signup, reset/recovery, token endpoints」。「Duplicate check」业务命名、认证 None,任何清单挂不上。**「业务功能本身构成存在性预言机」在五类 vuln agent 分类体系中无挂载点。**
2. **认知衰减(传导层)**——recon 落表丢失全部语义 → digest(`recon_context_digest.json`,endpoints 段摘要格式 `(role, object-id param)`)进一步压成一行 `POST /api/open-account/check-duplicate (None, none)`:身份标识符参数不在提取范围,响应差异语义无从进入 prompt。
3. **注意力错配(执行层)**——无锚点驱动 → agent 不 grep `duplicate`(log 0 次)→ 接口对 agent 等于不存在;注意力被 verify-service 吸干(方法论锚点密集处,产出 7 条)。

**对照先例(能力存在的直接证据)**:同轮 AUTH-VULN-02「verify-profile 无速率限制,可当 PII 探测预言机」(CWE-307)。verify-profile 被检出 = 名字带 verify + webLogin 后,**天然落进 §2 credential 端点清单**;check-duplicate 两项皆无。结论:**能力在,锚点缺**——这决定了修复方向是补锚点(prompt),而非改基础设施。

**IDOR/BOLA 辨析**(评审中确认,影响归类与修复建议):回显 duplicateCid+phone 部分成立 BOLA(API1);但核心危害「boolean/空响应分叉」不是对象访问,是元信息泄露(CWE-204)。IDOR 的修法「加归属校验」对匿名接口无从落地;真实修法(服务端强制掩码/响应压 boolean/按标识符限速/登录后仅查自身)均在枚举框架下。**按缺陷本质归存在性预言机,BOLA 层危害写入 finding 的 impact。**

### A2 方案设计与取舍

| 方案 | 内容 | 决策 | 理由 |
|---|---|---|---|
| **1. vuln-auth 方法论补类** | 新增 §10 检查节:触发词种子 + 四条件判定 + safe 准则 | ✅ **采纳** | 直接补「锚点=0」的根因;TS 式自给自足下 agent 自主 grep/read 补齐语义,输入衰减不再致命;单文件改动,回归面最小 |
| 2. recon 语义增强 | recon 端点表为查重类接口记录标识符参数名 + 响应差异形态 | ⏸ 延后 | 修的是传导层;对本漏洞边际增益小(名字显式 grep 稳命中),价值在名字隐晦的同类接口;recon prompt 变更影响**所有** vuln 轨输入,回归面大。待方案 1 落地后视残余漏报再评估 |
| 3. authz LLM 轨补「回显维度」 | vuln-authz 增加响应回显检查项 | ❌ 否决 | 双轨 OR 下任一轨检出即报;auth 轨一个检查节已同时覆盖枚举层与 BOLA 层,避免两轨重复改 |
| 4. 确定性 authz 轨扩展 | IDOR 候选加「无鉴权+标识符入参+响应分叉」模式 | ❌ 否决 | 该判定本质是语义判断,确定性层只能产出低质候选终归 LLM 裁决,性价比低 |

**§10 设计要点**(实现在 `prompts/vuln-auth.txt`,§9 后新增,不重编号):

- **行为定义优先于名单**:不限 login/signup——任何(含匿名)接受身份标识符(证件号/护照/手机/邮箱/用户名/客户号)作查询条件、返回存在性语义的端点;显式点名「匿名 pre-registration 查重是头号候选,无鉴权是流程设计不是防御」;
- **四条件判定**(同时防漏报与误报):① 标识符是否服务端绑定 caller(任意可探=枚举)② 响应是否超 boolean(回显 cid/资料=超)③ 掩码是否服务端强制(客户端可控 flag=缺陷)④ 是否按标识符限速(无→叠加 `abuse_defenses_missing`);
- **输出通道零 schema 改动**:`login_flow_logic`(+`abuse_defenses_missing`)→ `account_enumeration`,merger/poc/attack-chain 全兼容,与未授权 finding 天然并存;
- **safe 准则**:标识符严格绑 caller,或严格 boolean + 服务端限速 → `set_safe_vectors`;
- **铁律合规**:纯方法论文本,零确定性产物引用,不踩 `test_static_dataflow_hints_decoupling.py` 锁定的不变量。

### A3 实施与部署

| 步骤 | 结果 |
|---|---|
| `prompts/vuln-auth.txt` +11 行(§10) | commit `afb5ec7e` |
| prompts 相关测试(10 文件含铁律 decoupling) | 43 passed,**0 新增失败**(4 failed 经 stash 对比确认为预存,与本次无关) |
| Worker 生效 | 生产 prompts 烧镜像非挂载 → `docker compose build worker && up -d`;容器内 md5 = 宿主(`f1133cd9`)确认生效;重启窗口无活跃扫描 |

---

## R — Result(结果)

### 已验证 ✅

- prompts 测试无回归(见 A3);
- 修复已部署至生产 worker(§10 已在扫描链路生效)。

### 待验证 ⏳(复扫验收,由用户执行 libs 复扫后回填)

| # | 验收项 | 通过标准 | 结果 |
|---|---|---|---|
| R1 | **主验收**:枚举 finding 产出 | auth 轨产出「POST /api/open-account/check-duplicate certId 可枚举开户状态」finding,四条件证据齐全(标识符任意可探/超 boolean 回显/客户端可控 mask/无限速) | 待填 |
| R2 | 对照校准(防误报) | modify-profile/web/check-duplicate(cid 绑 caller)判 safe 进 `auth_safe_vectors`,证明判定轴未写歪 | 待填 |
| R3 | 独立并存 | 枚举 finding 与 AUTHZ-VULN-18(未授权)并存于报告,无互斥/去重 | 待填 |
| R4 | NodeGoat 回归 | auth findings 数量/质量不劣化,无误报潮 | 待填 |

**未检出时的迭代路径**(R1 失败):查 `agents/*auth-vuln*.log` 定位卡点——没 grep 到 `duplicate`(触发词不足)→ 读了没判(safe 准则过宽)→ 判了没提交(schema 问题)→ 对应迭代 §10 措辞 → rebuild → 再扫。

---

## 经验教训(Lessons Learned)

1. **漏报分析先分轨定位**:同一漏洞在不同检测轨的覆盖形态完全不同(taint 不适用/IDOR 探针锚点抓不到/authz 判定轴不问回显/auth 锚点缺失)——「扫不出」必须先问哪条轨哪一环,不能笼统归因「LLM 不行」。
2. **打包式 finding 是隐性漏报温床**:多路由合并进一条「未授权访问」时,单条路由的独特危害会被淹没;判定覆盖的标准是「危害语义在独立 finding 的标题/impact/PoC 中可见」。
3. **agent 检出 = 方法论锚点 × 输入线索**:两者皆零时接口对 agent 不存在。补锚点(prompt)通常比补线索(recon/digest)性价比高——TS 式自给自足轨道上,锚点驱动 agent 自己找语义。
4. **归类跟缺陷本质而非危害表现**:BOLA 是本漏洞的危害表现之一,但「匿名接口加归属校验」无从落地;按存在性预言机归类,修复建议才可执行。

## 后续行动(Action Items)

| # | 事项 | 状态 |
|---|---|---|
| 1 | libs 复扫验收 R1-R3 | ⏳ 用户执行中 |
| 2 | NodeGoat 回归验收 R4 | ⏳ 待排 |
| 3 | 方案 2(recon 查重类接口语义增强) | ⏸ 视复扫后残余漏报案例再评估 |
| 4 | vuln-authz「回显维度」补强 | ❌ 已否决(双轨 OR 下不必),除非未来出现 auth 轨补类后仍高频漏报的形态 |
