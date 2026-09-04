# libs check-duplicate certId 开户状态枚举漏报——根因与修复留痕

> 日期:2026-09-04 ｜ 分支:feat/fork-py ｜ 修复:prompts/vuln-auth.txt 方法论新增 §10 存在性预言机检查项
> 验收基线扫描:libs-20260903-084517(__legacy__ 工作区,2026-09-03)

## 1. 漏洞与漏报现象

**漏洞**(用户外部确认):`POST /api/open-account/check-duplicate` 的 `certId`(泰国身份证号)参数,更换为任意证件号可从响应差异判断该证件号是否已开户——匿名用户枚举预言机。

**源码形态**(`business/open-account-service/src/agudo-plugin/app/service/openAccountNiceService.ts:620-661`):

- 公共路由无鉴权(`router.ts:114`,开户前无登录态,无鉴权本身是业务设计);
- 入参 `certificate`/`email`/`cellphone` + **客户端可控 `mask`**(`params.mask !== false`);
- 响应分叉:未开户 → 空 `{}`;已开户 → `{duplicateCid, phone:{number, checked}}`,命中后回查该账户预留手机号。

三层危害:存在性预言机(CWE-204)/ 全裸客户号(mask=false)/ 回显他人手机号(BOLA 成分,CWE-639)。

**扫描结果**:报告只有 AUTHZ-VULN-18「open-account/* 19 条公共路由无鉴权」(high)的打包清单,枚举语义零字描述,PoC 验证的是 upsert-profile——对该漏洞等效漏报。已报未授权与枚举是两个独立漏洞,不互斥不替代(未授权反而放大枚举的可利用性,attack-chain 可组「免登录 × 批量枚举」链,前提是 vuln agent 先各自检出)。

## 2. 漏报根因:四轨 autopsy + 三层衰减

| 轨 | 触及? | 证据 | 为什么漏 |
|---|---|---|---|
| GitNexus 确定性(inj/xss/ssrf) | 否 | certId 不流向危险 sink | 非 taint 形态,架构性不覆盖 |
| GitNexus authz(IDOR) | 否 | `authz_gitnexus_queue.json` 候选数=0 | certId 是查询探针非对象引用(`_REQ_REF_RE` 抓不到锚点);漏洞在响应回显不在 sink;无鉴权使其脱离 IDOR「已认证越权」形态 |
| authz LLM 轨 | 是 | log 7 次提及;打包进 AUTHZ-VULN-18 | 判定轴是「归属门」(guard/ownership),问「cid 是否绑 caller」,不问「响应回显了什么」;同名接口 modify-profile/web/check-duplicate(cid 强制=ctx.user.uid)被判 SAFE 进 dismissed |
| auth LLM 轨(最应命中) | 否 | agent log 中 check-duplicate/certId/enumerat 出现 **0 次**;7 条 finding 全在 verify-service | 见下三层 |

**auth 轨三层衰减**(agent 行为 = 方法论锚点 × 输入线索,该接口两项皆零):

1. **类别缺口(根因)**:漏洞横跨分类缝隙——不是 taint、不是 missing authn(无鉴权是设计)、不是 IDOR。vuln-auth 9 类方法论的枚举检查只锚在 §7「login/signup 错误信息 generic」和 §8「密码重置 avoid user enumeration」;§2 速率限制端点清单是「login/signup/reset/token」。「Duplicate check」业务命名、无认证,任何清单都挂不上。方法论缺「业务功能本身构成存在性预言机」这个一般化类别。
2. **认知衰减(传导层)**:recon 落表 `| None | None | None | Duplicate check. No auth middleware |`(certId/响应差异/mask 语义全丢)→ digest 压成一行 `POST /api/open-account/check-duplicate (None, none)`(摘要格式只提取 object-id 类参数,身份标识符不在提取范围)。
3. **注意力错配**:无锚点驱动 → agent 不 grep duplicate → 0 次接触;注意力被 verify-service 吸干(锚点密集)。

**对照先例证明能力存在**:同轮 AUTH-VULN-02「verify-profile 无速率限制,可当 PII 探测预言机」(CWE-307)——同一 agent 有完全相同的预言机推理模式。verify-profile 被检出是因为名字带 verify、在 webLogin 后,天然落进 §2 credential 端点清单;check-duplicate 两项皆无。

**IDOR 辨析**(讨论中确认):回显 duplicateCid+phone 的部分成立 BOLA(API1);但核心危害「判断是否开户」(boolean/空响应分叉)不是对象访问,是元信息泄露(CWE-204)。且 IDOR 修法「加归属校验」对匿名接口无从落地,真实修法(服务端强制掩码/压到 boolean/按标识符限速/登录后仅查自身)都在枚举框架下。按缺陷本质归类存在性预言机,危害表现里的 BOLA 层写进 finding。

## 3. 修复方案与取舍

**方案 1(采纳)**:仅改 `prompts/vuln-auth.txt`——方法论新增 `## 10) Existence oracles (duplicate-check / availability / lookup-by-identifier endpoints)`:

- 触发词种子(duplicate/exists/availability/lookup…)+ 行为定义:**不限 login/signup,任何接受身份标识符(证件号/手机/邮箱/用户名/客户号)作查询条件、返回存在性语义的端点都算**,显式点名「匿名 pre-registration duplicate-check 是头号候选,无鉴权是流程设计不是防御」;
- 四条件判定:①标识符是否服务端绑定 caller(任意可探=枚举)/②响应是否超 boolean(回显 cid/资料=超)/③掩码是否服务端强制(客户端可控 mask=缺陷)/④是否有按标识符限速(无→叠加 abuse_defenses_missing);
- 输出通道 schema 零改动:`login_flow_logic`(+`abuse_defenses_missing`)→ `account_enumeration`;
- safe 准则防误报:标识符严格绑 caller 或严格 boolean+服务端限速 → `set_safe_vectors`。

为什么能解决:锚点从 0→1 后,输入衰减不再致命——LLM 轨 TS 式自给自足,agent 有方法论驱动就自己 grep/read 补齐语义(不碰「确定性产物不喂 LLM 轨」铁律,新增文本零确定性产物引用)。

**方案 2(延后)**:recon 端点表为查重类接口记录身份标识符参数名+响应差异形态(修传导层)。对本漏洞边际增益小(check-duplicate 名字显式,grep 稳命中);价值在名字隐晦的同类接口(`/api/user/status` 查手机号占用类)。覆盖面投资,待方案 1 落地后看是否仍有漏报案例再决定——改 recon prompt 影响所有 vuln 轨输入,回归面大。

**否决项**:确定性 authz 轨加「无鉴权+标识符入参+响应分叉」候选模式——语义判定终究靠 LLM,优先级低;authz LLM 轨补「回显维度」——双轨 OR 下任一轨检出即报,auth 轨一个检查节已兜住两层危害,避免两轨重复改。

## 4. 实施与验证

- 改动:`prompts/vuln-auth.txt` +11 行(§10 检查节,放 §9 后不重编号)。
- 测试:prompts 相关 43 passed,0 新增失败(4 个失败为预存,stash 对比确认与本次无关)。
- 生效:worker 镜像 rebuild + 重启,容器内 md5=宿主(`f1133cd9`)。**改 prompt 生产必须 --build 重烧,不 build 不生效**。
- **验收(待执行,用户自行发起 libs 复扫)**:
  1. 主验收:auth 轨产出「POST /api/open-account/check-duplicate certId 可枚举开户状态」finding,四条件证据齐全;
  2. 对照校准:modify-profile 版 check-duplicate(cid 绑 caller)应判 safe 进 auth_safe_vectors;
  3. 并存确认:与 AUTHZ-VULN-18 独立共存(merger 只做 verdict OR,无同端点互斥);
  4. 未检出迭代路径:查 agent log 卡点(没 grep 到/读了没判/判了没提交)→ 迭代 §10 措辞 → rebuild → 再扫;
  5. NodeGoat 回归:auth findings 数量/质量不劣化(防误报潮)。
