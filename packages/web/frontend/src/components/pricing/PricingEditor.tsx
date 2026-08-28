import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Plus, RotateCcw, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import type { PricingRow, PricingSource, Prices } from "@/api/pricing";

/**
 * 模型定价编辑器（spec 2026-08-28 §4.3）——全局 / 工作区两 scope 复用。
 *
 * 设计签名 = 来源徽章四态：内置（secondary·muted）/ 环境（outline·muted）/
 * 全局（实底 primary）/ 本工作区（outline·primary）——徽章即优先级链的可见化，
 * 一眼看清每个价来自哪层、被谁覆盖。数字右对齐 tabular-nums（金融表格惯例）。
 * 列序（2026-08-28 修复「列名和值对不上」）：输入/输出主干档相邻靠前（对齐智谱/
 * DeepSeek 官方定价页「输入|输出|缓存」序，抄数不再错位），缓存两档靠后成组——
 * 且缓存写入对 GLM/DeepSeek 恒 0，不再横插在输出之前当噪音。
 * 行级币种（2026-08-28）：每行可指定 CNY/USD（null = 跟随表级默认）；
 * 表顶切换语义 = 默认币种（新行 / 未覆盖行的生效值）。
 * canEdit=false 只读纯展示；编辑态提供：改价、恢复默认（builtin 模型）、
 * 删行、新增模型（normalize 感知查重）、币种切换、脏提示 + 保存/重置。
 */

const PRICE_KEYS = ["input", "output", "cache_read", "cache_creation"] as const;
type PriceKey = (typeof PRICE_KEYS)[number];

const CURRENCIES = ["CNY", "USD"] as const;
const CURRENCY_SYMBOLS: Record<string, string> = { CNY: "¥", USD: "$" };

/** 模型 id 归一（对齐后端 normalize_model 语义）：小写 + 剥 [..]/-YYYYMMDD 后缀 + -coder 尾。 */
export function normalizeModelId(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/\[.*?\]/g, "")
    .replace(/-\d{8}.*$/g, "")
    .replace(/-coder$/g, "")
    .trim();
}

interface EditRow {
  model: string; // 现有行固定；新行（isNew）可编辑
  draft: Record<PriceKey, string>; // 输入草稿（string，保留输入过程）
  source: PricingSource;
  currency: string | null; // 行级币种：null = 跟随表级默认
  isNew?: boolean;
}

function toDraft(p: Prices): Record<PriceKey, string> {
  return {
    input: String(p.input),
    cache_read: String(p.cache_read),
    cache_creation: String(p.cache_creation),
    output: String(p.output),
  };
}

function parsePrice(raw: string): number | null {
  if (raw.trim() === "") return null;
  const v = Number(raw);
  if (!isFinite(v) || v < 0) return null;
  return v;
}

/** 来源徽章：四态对应优先级链（越「实」= 越高优先的手工管理层）。 */
function SourceBadge({ source }: { source: PricingSource }) {
  const { t } = useTranslation();
  const cls: Record<PricingSource, string | undefined> = {
    builtin: "text-muted-foreground",
    profile_env: "text-muted-foreground",
    global: undefined, // default 实底 primary
    workspace: "border-primary/60 text-primary",
  };
  const variant = source === "global" ? "default" : source === "builtin" ? "secondary" : "outline";
  return (
    <Badge variant={variant} className={cls[source]}>
      {t(`pricing.source.${source}`)}
    </Badge>
  );
}

export interface PricingEditorProps {
  scope: "global" | "workspace";
  currency: string;
  rows: PricingRow[];
  builtinDefaults: Record<string, Prices>;
  canEdit: boolean;
  onSave: (currency: string, models: Record<string, Prices>) => Promise<void>;
  onClear?: () => Promise<void>;
  hasOverride?: boolean;
}

export function PricingEditor({
  scope, currency, rows, builtinDefaults, canEdit, onSave, onClear, hasOverride,
}: PricingEditorProps) {
  const { t } = useTranslation();
  const [editRows, setEditRows] = useState<EditRow[]>(
    () => rows.map((r) => ({
      model: r.model, draft: toDraft(r.prices), source: r.source, currency: r.currency ?? null,
    })),
  );
  const [cur, setCur] = useState(currency);
  const [saving, setSaving] = useState(false);

  // 初始快照 = draft 形态（string 草稿），与 current 同构可比较；
  // 只随传入 rows/currency 变化重算（挂载点保存后刷新数据 → 编辑器重置）
  const initial = useMemo(
    () => JSON.stringify({
      currency,
      rows: rows.map((r) => [r.model, toDraft(r.prices), r.source, r.currency ?? null]),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rows, currency],
  );

  const rowInvalid = (r: EditRow): PriceKey | null => {
    for (const k of PRICE_KEYS) if (parsePrice(r.draft[k]) === null) return k;
    return null;
  };
  const anyInvalid = editRows.some(rowInvalid);

  // 新增行 id：非空 + 归一后不与任何其他行重复（后端 400 兜底）
  const newRows = editRows.filter((r) => r.isNew);
  const dupId = newRows.some((r) => {
    const n = normalizeModelId(r.model);
    if (!n) return false; // 空 id 由「缺 id」禁用条件兜住
    return editRows.some((o) => o !== r && normalizeModelId(o.model) === n);
  });

  const current = JSON.stringify({
    currency: cur,
    rows: editRows.map((r) => [r.model, r.draft, r.source, r.currency]),
  });
  const dirty = current !== initial;

  const canSave = canEdit && dirty && !anyInvalid && !dupId
    && newRows.every((r) => r.model.trim() !== "" && !rowInvalid(r))
    && !saving;

  function setCell(model: string, key: PriceKey, value: string) {
    setEditRows((prev) => prev.map((r) =>
      r.model === model ? { ...r, draft: { ...r.draft, [key]: value } } : r));
  }

  function setRowCurrency(model: string, value: string | null) {
    setEditRows((prev) => prev.map((r) => (r.model === model ? { ...r, currency: value } : r)));
  }

  function addRow() {
    // 一次只挂一行新行：新行 model 从空串起编辑（key 固定 __new__），再点加行无意义
    // 且会撞 key / setCell 按 model 匹配会串行。保存（或删除）后可继续加。
    if (editRows.some((r) => r.isNew)) return;
    setEditRows((prev) => [
      ...prev,
      { model: "", draft: { input: "", output: "", cache_read: "", cache_creation: "" },
        source: "builtin", currency: null, isNew: true },
    ]);
  }

  function removeRow(model: string) {
    setEditRows((prev) => prev.filter((r) => r.model !== model));
  }

  function restoreDefault(model: string) {
    const d = builtinDefaults[model];
    if (!d) return;
    setEditRows((prev) => prev.map((r) => (r.model === model ? { ...r, draft: toDraft(d) } : r)));
  }

  function reset() {
    setEditRows(rows.map((r) => ({
      model: r.model, draft: toDraft(r.prices), source: r.source, currency: r.currency ?? null,
    })));
    setCur(currency);
  }

  async function save() {
    if (!canSave) return;
    const models: Record<string, Prices> = {};
    for (const r of editRows) {
      const parsed = {
        ...Object.fromEntries(PRICE_KEYS.map((k) => [k, parsePrice(r.draft[k])])),
        currency: r.currency,
      } as unknown as Prices;
      models[r.model.trim()] = parsed;
    }
    setSaving(true);
    try {
      await onSave(cur, models);
    } finally {
      setSaving(false);
    }
  }

  const unitNote = t("pricing.unitNote");

  return (
    <div className="space-y-3" data-testid={`pricing-editor-${scope}`}>
      {/* 顶部说明行：单位 + 币种（编辑态可切，segmented 两钮对齐 ThemePicker 语言） */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">{unitNote}</p>
        {canEdit ? (
          <div className="flex items-center gap-1.5" role="group" aria-label={t("pricing.currencyLabel")}>
            {CURRENCIES.map((c) => (
              <button
                key={c}
                type="button"
                data-testid={`pricing-currency-${c}`}
                aria-pressed={cur === c}
                onClick={() => setCur(c)}
                className={`rounded-md border px-2.5 py-1 text-xs font-medium tabular-nums transition-colors ${
                  cur === c
                    ? "border-primary bg-accent/50 text-primary"
                    : "border-border text-muted-foreground hover:text-foreground"
                }`}
              >
                {CURRENCY_SYMBOLS[c]} {c}
              </button>
            ))}
          </div>
        ) : (
          <span className="text-xs tabular-nums text-muted-foreground">
            {CURRENCY_SYMBOLS[cur] ?? ""} {cur}
          </span>
        )}
      </div>

      <div className="rounded-lg border">
        <Table className="text-sm">
          <TableHeader>
            <TableRow>
              <TableHead>{t("pricing.colModel")}</TableHead>
              {PRICE_KEYS.map((k) => (
                <TableHead key={k} className="text-right">{t(`pricing.col.${k}`)}</TableHead>
              ))}
              <TableHead className="text-center">{t("pricing.colCurrency")}</TableHead>
              <TableHead>{t("pricing.colSource")}</TableHead>
              {canEdit && <TableHead className="w-px">{t("pricing.colActions")}</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {editRows.map((r) => {
              const rowId = r.isNew ? "__new__" : r.model;
              const invalidKey = rowInvalid(r);
              const hasDefault = !r.isNew && builtinDefaults[r.model] !== undefined;
              return (
                <TableRow key={r.isNew ? "__new__" : r.model} data-testid={`pricing-row-${rowId}`}>
                  <TableCell className="font-mono text-xs">
                    {r.isNew && canEdit ? (
                      <Input
                        data-testid="pricing-new-model"
                        className="h-8 w-44 font-mono text-xs"
                        placeholder={t("pricing.newModelPlaceholder")}
                        value={r.model}
                        aria-label={t("pricing.newModelPlaceholder")}
                        onChange={(e) =>
                          setEditRows((prev) => prev.map((o) =>
                            o.isNew ? { ...o, model: e.target.value } : o))}
                      />
                    ) : (
                      r.model
                    )}
                    {invalidKey && (
                      <p data-testid={`pricing-invalid-${rowId}`} className="mt-1 text-[10px] text-red">
                        {t("pricing.invalidNumber")}
                      </p>
                    )}
                  </TableCell>
                  {PRICE_KEYS.map((k) => (
                    <TableCell key={k} className="text-right">
                      {canEdit ? (
                        <Input
                          data-testid={`pricing-cell-${rowId}-${k}`}
                          inputMode="decimal"
                          aria-invalid={invalidKey === k}
                          aria-label={`${r.model} ${k}`}
                          className="h-8 w-24 text-right font-mono text-xs tabular-nums"
                          value={r.draft[k]}
                          onChange={(e) => setCell(r.model, k, e.target.value)}
                        />
                      ) : (
                        <span
                          data-testid={`pricing-readonly-${r.model}-${k}`}
                          className="font-mono text-xs tabular-nums"
                        >
                          {r.draft[k]}
                        </span>
                      )}
                    </TableCell>
                  ))}
                  <TableCell className="text-center">
                    {canEdit ? (
                      <div
                        className="flex items-center justify-center gap-0.5"
                        role="group"
                        aria-label={`${r.model || t("pricing.newModelPlaceholder")} ${t("pricing.colCurrency")}`}
                      >
                        <button
                          type="button"
                          data-testid={`pricing-row-currency-${rowId}-default`}
                          aria-pressed={r.currency === null}
                          title={t("pricing.currencyDefault")}
                          onClick={() => setRowCurrency(r.model, null)}
                          className={`rounded-md border px-1.5 py-0.5 text-[10px] font-medium transition-colors ${
                            r.currency === null
                              ? "border-primary bg-accent/50 text-primary"
                              : "border-border text-muted-foreground hover:text-foreground"
                          }`}
                        >
                          {t("pricing.currencyDefault")}
                        </button>
                        {CURRENCIES.map((c) => (
                          <button
                            key={c}
                            type="button"
                            data-testid={`pricing-row-currency-${rowId}-${c}`}
                            aria-pressed={r.currency === c}
                            onClick={() => setRowCurrency(r.model, c)}
                            className={`rounded-md border px-1.5 py-0.5 text-[10px] font-medium tabular-nums transition-colors ${
                              r.currency === c
                                ? "border-primary bg-accent/50 text-primary"
                                : "border-border text-muted-foreground hover:text-foreground"
                            }`}
                          >
                            {CURRENCY_SYMBOLS[c]}
                          </button>
                        ))}
                      </div>
                    ) : (
                      <span
                        data-testid={`pricing-row-currency-${r.model}`}
                        title={r.currency === null ? t("pricing.currencyDefault") : undefined}
                        className={`font-mono text-xs tabular-nums${
                          r.currency === null ? " text-muted-foreground" : ""
                        }`}
                      >
                        {CURRENCY_SYMBOLS[r.currency ?? cur] ?? ""}
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    {r.isNew ? (
                      <Badge variant="secondary" className="text-muted-foreground">
                        {t("pricing.source.new")}
                      </Badge>
                    ) : (
                      <span data-testid={`pricing-source-${r.model}`}>
                        <SourceBadge source={r.source} />
                      </span>
                    )}
                  </TableCell>
                  {canEdit && (
                    <TableCell>
                      <div className="flex items-center justify-end gap-1">
                        {hasDefault && (
                          <Button
                            type="button" variant="ghost" size="sm"
                            data-testid={`pricing-restore-${r.model}`}
                            className="h-7 gap-1 px-2 text-xs text-muted-foreground"
                            title={t("pricing.restoreDefault")}
                            onClick={() => restoreDefault(r.model)}
                          >
                            <RotateCcw className="size-3" />
                          </Button>
                        )}
                        {!r.isNew && !hasDefault && (
                          <Button
                            type="button" variant="ghost" size="sm"
                            data-testid={`pricing-delete-${r.model}`}
                            className="h-7 gap-1 px-2 text-xs text-muted-foreground hover:text-destructive"
                            title={t("pricing.deleteRow")}
                            onClick={() => removeRow(r.model)}
                          >
                            <Trash2 className="size-3" />
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  )}
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>

      {/* 校验反馈：行级错误已渲染在各行模型名下方；此处仅全局性（跨行）的新增 id 冲突 */}
      {canEdit && dupId && (
        <p className="text-xs text-red" role="alert" data-testid="pricing-dup-id">
          {t("pricing.dupId")}
        </p>
      )}

      {/* 操作条 */}
      {canEdit && (
        <div className="flex flex-wrap items-center gap-2">
          {dirty && (
            <span data-testid="pricing-dirty" className="text-xs text-muted-foreground">
              {t("pricing.dirtyHint")}
            </span>
          )}
          <Button type="button" size="sm" data-testid="pricing-save" onClick={save} disabled={!canSave}>
            {t("pricing.save")}
          </Button>
          <Button
            type="button" variant="ghost" size="sm"
            data-testid="pricing-reset" onClick={reset} disabled={!dirty || saving}
            className="text-muted-foreground"
          >
            {t("pricing.reset")}
          </Button>
          {canEdit && (
            <Button
              type="button" variant="outline" size="sm"
              data-testid="pricing-add-row" onClick={addRow} disabled={saving}
              className="ml-auto gap-1"
            >
              <Plus className="size-3.5" />
              {t("pricing.addModel")}
            </Button>
          )}
          {onClear && (
            <Button
              type="button" variant="ghost" size="sm"
              data-testid="pricing-clear" onClick={() => void onClear()}
              disabled={saving || !hasOverride}
              className="text-muted-foreground"
            >
              {t("pricing.clear")}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
