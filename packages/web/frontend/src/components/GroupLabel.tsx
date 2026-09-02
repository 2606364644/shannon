/** 分组小标题：coral 竖条 eyebrow——卡内分组的全站统一视觉语言（Settings Section /
 *  ScanFormFields GroupLabel 同款）。跨仓表单 auto/manual 两模式共用（原两处内嵌
 *  副本，第三处出现时抽出；适配中文卡内分组——无 uppercase/tracking-wider，
 *  仅 coral 竖条 + 小号 semibold 标签拉层次）。 */
export function GroupLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="h-3 w-[3px] rounded-full bg-primary" aria-hidden />
      <span className="text-[11px] font-semibold text-muted-foreground">{children}</span>
    </div>
  );
}
