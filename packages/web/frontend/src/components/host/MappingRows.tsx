// HOST 映射录入草稿（前端内部态，档案 dialog 持有）。
//  - 新建无 id（后端分配）；编辑透传原 ip+host 作 key。
//  - 面板范式：粘顶工具栏（计数 + 实时筛选 + 添加 + 清空）+ hairline 单行表体。
//  - **大列表不爆框 + 不卡**：表体恒定高度内滚；行数超 VIRTUAL_THRESHOLD 用 react-window
//    虚拟滚动（只渲染可视区行），上千条映射（如 hosts 拉取 id=4000）也丝滑。行 React.memo +
//    稳定 handlers（读最新 value via ref），编辑单行只重渲该行。aria-label 透传列名 ->
//    getByLabelText 可定位（a11y + 测试）。
import { memo, useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { FixedSizeList } from "react-window";
import { useTranslation } from "react-i18next";
import { Search, Plus, Trash2, Eraser } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface MappingDraft {
  ip: string;
  host: string;
}

interface Props {
  value: MappingDraft[];
  onChange: (next: MappingDraft[]) => void;
  /** 递增信号 = 刚从来源链接导入一批，触发计数 chip 一次 coral 脉冲（仅视觉反馈）。 */
  importSignal?: number;
  /** 提交被拦后置 true：未填完整的行高亮 ring，提示用户补全或删除。 */
  highlightInvalid?: boolean;
}

const ROW_H = 44; // px，与行 h-[44px] box-border 对齐（react-window itemSize 精确，行不错位）
const VIRTUAL_THRESHOLD = 100; // 超过才虚拟滚动；少于则普通 map（测试场景 & 小列表零开销）

/** 单行：IP / 域名 两个行内输入 + 末列删除。memo 化--draft 引用不变即跳过重渲，
 *  故编辑某行不会带动其余可见行重渲。aria-label 透传列名 -> getByLabelText 可定位 + a11y。
 *  style 仅虚拟模式透传（react-window 绝对定位）；普通模式为 undefined。 */
const Row = memo(function Row({
  draft,
  highlightInvalid,
  ipLabel,
  hostLabel,
  onIp,
  onHost,
  onRemove,
  style,
}: {
  draft: MappingDraft;
  highlightInvalid?: boolean;
  ipLabel: string;
  hostLabel: string;
  onIp: (v: string) => void;
  onHost: (v: string) => void;
  onRemove: () => void;
  style?: CSSProperties;
}) {
  const ipBlank = !draft.ip.trim();
  const hostBlank = !draft.host.trim();
  const invalid = highlightInvalid && (ipBlank || hostBlank);
  return (
    <div
      style={style}
      className="grid h-[44px] box-border grid-cols-[1fr_1fr_2rem] items-center gap-2 border-b border-border/50 px-3 transition-colors hover:bg-muted/30"
    >
      <Input
        data-hm-ip
        aria-label={ipLabel}
        value={draft.ip}
        onChange={(e) => onIp(e.target.value)}
        placeholder="10.0.0.1"
        className={cn(
          "h-8 border-transparent bg-transparent px-2 font-mono text-xs shadow-none focus-visible:border-input focus-visible:ring-1",
          invalid && ipBlank && "ring-1 ring-destructive/50",
        )}
      />
      <Input
        aria-label={hostLabel}
        value={draft.host}
        onChange={(e) => onHost(e.target.value)}
        placeholder="api.example.com"
        className={cn(
          "h-8 border-transparent bg-transparent px-2 font-mono text-xs shadow-none focus-visible:border-input focus-visible:ring-1",
          invalid && hostBlank && "ring-1 ring-destructive/50",
        )}
      />
      <Button
        type="button"
        variant="ghost"
        size="icon"
        onClick={onRemove}
        aria-label="delete mapping"
        className="h-7 w-7 text-muted-foreground hover:text-destructive"
      >
        <Trash2 className="size-3.5" />
      </Button>
    </div>
  );
});

/** 虚拟行：react-window FixedSizeList 的子组件，从 itemData 取该行草稿 + handlers。 */
interface RowData {
  rows: { d: MappingDraft; idx: number }[];
  highlightInvalid?: boolean;
  ipLabel: string;
  hostLabel: string;
  onPatch: (idx: number, patch: Partial<MappingDraft>) => void;
  onRemove: (idx: number) => void;
}
function VirtualRow({ index, style, data }: { index: number; style: CSSProperties; data: RowData }) {
  const { d, idx } = data.rows[index];
  return (
    <Row
      draft={d}
      highlightInvalid={data.highlightInvalid}
      ipLabel={data.ipLabel}
      hostLabel={data.hostLabel}
      onIp={(v) => data.onPatch(idx, { ip: v })}
      onHost={(v) => data.onPatch(idx, { host: v })}
      onRemove={() => data.onRemove(idx)}
      style={style}
    />
  );
}

/** domain->IP 映射行编辑器：粘顶工具栏 + 可滚动表体（大列表虚拟化）。
 *  受控组件--值与变更全由父级管。add() append + 滚到底 + 聚焦新行；最少 1 行。 */
export function MappingRows({ value, onChange, importSignal, highlightInvalid }: Props) {
  const { t, i18n } = useTranslation();
  const [query, setQuery] = useState("");
  const [pulse, setPulse] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<FixedSizeList>(null);
  const focusLast = useRef(false);
  const prevSignal = useRef(importSignal);
  // 稳定 handlers：读最新 value via ref，使 handlers 引用不随 value 变化 -> itemData 稳定 ->
  // memo Row 仅重渲值变行（编辑单行不带动其余可见行重渲）。
  const valueRef = useRef(value);
  valueRef.current = value;

  const q = query.trim().toLowerCase();
  // 保留原始 index 作 key + 编辑回写目标，保证筛选切换时输入焦点不丢。
  const rows = value
    .map((d, idx) => ({ d, idx }))
    .filter(({ d }) => !q || d.ip.toLowerCase().includes(q) || d.host.toLowerCase().includes(q));

  const nf = new Intl.NumberFormat(i18n.language);
  const countLabel = q
    ? t("hostProfiles.filterCount", { shown: nf.format(rows.length), total: nf.format(value.length) })
    : t("hostProfiles.totalCount", { count: nf.format(value.length) });

  const update = useCallback(
    (i: number, patch: Partial<MappingDraft>) => {
      const next = valueRef.current.slice();
      next[i] = { ...next[i], ...patch };
      onChange(next);
    },
    [onChange],
  );
  const remove = useCallback(
    (i: number) => {
      onChange(valueRef.current.filter((_, idx) => idx !== i));
    },
    [onChange],
  );

  function add() {
    focusLast.current = true;
    setQuery(""); // 新行追加在末尾，清筛选确保可见 + 可聚焦
    onChange([...valueRef.current, { ip: "", host: "" }]);
  }

  function clearAll() {
    if (valueRef.current.length === 0) return;
    if (!window.confirm(t("hostProfiles.clearConfirm"))) return;
    onChange([{ ip: "", host: "" }]);
    setQuery("");
  }

  // 导入脉冲：importSignal 变化 -> 计数 chip 亮 coral 2.6s。
  useEffect(() => {
    if (importSignal === prevSignal.current) return;
    prevSignal.current = importSignal;
    setPulse(true);
    const id = setTimeout(() => setPulse(false), 2600);
    return () => clearTimeout(id);
  }, [importSignal]);

  const virtual = rows.length > VIRTUAL_THRESHOLD;
  // react-window FixedSizeList 需要像素高度。测容器高，容器随视口弹性变化时实时跟随。
  // jsdom 无 ResizeObserver -> guard 跳过、回退初值（测试只验渲染与不崩，不验像素布局，回退值不影响断言）。
  const [listH, setListH] = useState(360);
  useEffect(() => {
    const el = containerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const measure = () => setListH(Math.max(1, Math.floor(el.clientHeight)));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [virtual]);

  // 添加后滚到底 + 聚焦新行的 IP 输入（虚拟模式 scrollToItem 把新行带入可视区再聚焦）。
  useEffect(() => {
    if (!focusLast.current) return;
    focusLast.current = false;
    const lastIdx = valueRef.current.length - 1;
    if (virtual) {
      listRef.current?.scrollToItem(lastIdx, "end");
      requestAnimationFrame(() => {
        const ips = containerRef.current?.querySelectorAll<HTMLInputElement>("input[data-hm-ip]");
        ips?.[ips.length - 1]?.focus();
      });
    } else {
      const ips = containerRef.current?.querySelectorAll<HTMLInputElement>("input[data-hm-ip]");
      ips?.[ips.length - 1]?.focus();
      containerRef.current?.scrollTo({ top: containerRef.current.scrollHeight });
    }
  }, [value.length, virtual]);

  const emptyFiltered = q && rows.length === 0;
  const ipLabel = t("hostProfiles.ip");
  const hostLabel = t("hostProfiles.host");
  const itemData: RowData = { rows, highlightInvalid, ipLabel, hostLabel, onPatch: update, onRemove: remove };

  return (
    <div className="flex flex-col overflow-hidden rounded-lg border border-border bg-card/30">
      {/* 粘顶工具栏：计数 chip + 筛选 + 添加 / 清空 */}
      <div className="flex shrink-0 items-center gap-2 border-b border-border bg-secondary/50 px-2.5 py-2">
        <span
          className={cn(
            "shrink-0 rounded-full px-2 py-0.5 font-mono text-[11px] font-medium tabular-nums transition-colors",
            pulse
              ? "bg-primary/15 text-primary"
              : "bg-muted/60 text-muted-foreground",
          )}
        >
          {countLabel}
        </span>
        <div className="relative min-w-0 flex-1">
          <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("hostProfiles.filterPlaceholder")}
            className="h-8 border-border/60 bg-background/40 pl-7 text-xs shadow-none"
          />
        </div>
        <Button type="button" variant="ghost" size="sm" onClick={add} className="h-8 shrink-0 gap-1 px-2 text-xs">
          <Plus className="size-3.5" /> {t("hostProfiles.addMapping")}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={clearAll}
          disabled={value.length === 0}
          className="h-8 shrink-0 gap-1 px-2 text-xs text-muted-foreground hover:text-destructive"
        >
          <Eraser className="size-3.5" /> {t("hostProfiles.clearAll")}
        </Button>
      </div>
      {/* 列头：IP / 域名（对齐行 grid，末列留删除位） */}
      <div className="grid shrink-0 grid-cols-[1fr_1fr_2rem] gap-2 border-b border-border bg-muted/30 px-3 py-1.5 text-[11px] font-medium text-muted-foreground">
        <span>{ipLabel}</span>
        <span>{hostLabel}</span>
        <span />
      </div>
      {/* 表体：恒定高度内滚--大列表不爆框的核心；超阈值虚拟滚动只渲染可视区行。 */}
      <div
        ref={containerRef}
        className={cn(
          "h-[calc(85vh-22rem)] min-h-[8rem]",
          virtual ? "overflow-hidden" : "overflow-y-auto",
        )}
      >
        {emptyFiltered ? (
          <p className="px-3 py-6 text-center text-xs text-muted-foreground">
            {t("hostProfiles.emptyFilter", { query })}
          </p>
        ) : virtual ? (
          <FixedSizeList
            ref={listRef}
            height={listH}
            width="100%"
            itemCount={rows.length}
            itemSize={ROW_H}
            itemData={itemData}
          >
            {VirtualRow}
          </FixedSizeList>
        ) : (
          rows.map(({ d, idx }) => (
            <Row
              key={idx}
              draft={d}
              highlightInvalid={highlightInvalid}
              ipLabel={ipLabel}
              hostLabel={hostLabel}
              onIp={(v) => update(idx, { ip: v })}
              onHost={(v) => update(idx, { host: v })}
              onRemove={() => remove(idx)}
            />
          ))
        )}
      </div>
    </div>
  );
}
