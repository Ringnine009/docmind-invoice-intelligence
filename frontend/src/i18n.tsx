import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

export type Lang = "en" | "zh";

export const en: Record<string, string> = {
  // brand / chrome
  "brand.subtitle": "Multimodal invoice intelligence & audit",
  "demo.load": "Load demo (30)",
  "source.demo": "DEMO · synthetic data",
  "source.upload": "REAL UPLOAD",
  "batch.label": "batch",
  "lang.en": "EN",
  "lang.zh": "中文",
  "footer": "DocMind · synthetic sample data only · no real PII",
  // tabs
  "tab.results": "Results",
  "tab.audit": "Audit",
  "tab.analysis": "Analysis",
  "tab.graph": "Graph",
  "analysis.gated": "Analysis is available once the batch finishes.",
  // status
  "status.pending": "pending",
  "status.running": "running",
  "status.done": "done",
  "status.failed": "failed",
  "status.progress": "{done}/{total} · {pct}%",
  "status.summary": "{done}/{total} extracted · {findings} findings",
  // upload
  "upload.title": "Drop invoice PDFs here or click to browse",
  "upload.processing": "Processing…",
  "upload.selected": "{n} PDF{s} selected — processing…",
  "upload.hint": "single or batch · rendered at 200 dpi · Qwen vision extraction",
  "empty.title": "Upload invoice PDFs or load the synthetic demo batch to get started.",
  "empty.hint":
    "Extraction uses a Qwen vision model (DashScope); the audit engine then checks duplicates, arithmetic, tax rates, QR codes and party information.",
  // actions
  "retry.failed": "Retry {n} failed",
  "export.csv": "Export CSV",
  "export.json": "Export JSON",
  "retry.single": "Retry",
  // results table
  "results.noResults": "No results yet.",
  "results.file": "File",
  "results.invoiceNo": "Invoice №",
  "results.date": "Date",
  "results.seller": "Seller",
  "results.buyer": "Buyer",
  "results.amount": "Amount",
  "results.failed": "extraction failed: {error}",
  // analysis
  "analysis.dateFrom": "Date from",
  "analysis.dateTo": "Date to",
  "analysis.buyer": "Buyer",
  "analysis.seller": "Seller",
  "analysis.allBuyers": "All buyers",
  "analysis.allSellers": "All sellers",
  "analysis.clearFilters": "Clear filters",
  "analysis.stats": "{n} records · total {amount}",
  "analysis.noMatch": "No invoices match the filters.",
  "analysis.thInvoice": "Invoice №",
  "analysis.thDate": "Issue date",
  "analysis.thSeller": "Seller",
  "analysis.thBuyer": "Buyer",
  "analysis.thTotal": "Total",
  // audit charts
  "audit.byRule": "Findings by rule",
  "audit.bySeverity": "Findings by severity",
  "audit.noChartData": "No audit findings to chart.",
  // audit panel
  "audit.clean": "No audit findings — batch passed all rules.",
  "severity.critical": "CRITICAL",
  "severity.error": "ERROR",
  "severity.warning": "WARNING",
  "severity.info": "INFO",
  // graph
  "graph.building": "Building graph…",
  "graph.nodeTypes": "Node types",
  "graph.company": "Company",
  "graph.allCompanies": "All companies",
  "graph.minAmount": "Min amount (¥)",
  "graph.reset": "Reset",
  "graph.stats": "{nodes} nodes · {edges} edges · total {amount}",
  "graph.type.invoice": "invoice",
  "graph.type.company": "company",
  "graph.type.product": "product",
  "graph.legendEdges": "bought / sold",
  "graph.legendEdgesDashed": "contains / transaction",
  "graph.empty": "No nodes match the filters.",
  "graph.clearFocus": "Clear focus",
  "graph.cardType": "type · {type}",
  "graph.cardAmount": "amount · {amount}",
  "graph.cardConnections": "connections · {n}",
  "graph.topCompanies": "Top companies by volume",
  "graph.hint": "click a node to focus its neighbourhood; click empty canvas to reset",
  // drawer
  "drawer.fields": "Fields",
  "drawer.confidence": "Model confidence",
  "drawer.noConfidence": "no confidence reported",
  "drawer.items": "Line items",
  "drawer.evidence": "Audit evidence ({n})",
  "drawer.noFindings": "No audit findings for this invoice.",
  "drawer.connections": "Graph connections",
  "drawer.noConnections": "No graph connections.",
  "drawer.nodeProps": "Node properties",
  "drawer.failed": "extraction failed",
  "drawer.close": "close drawer",
};

export const zh: Record<string, string> = {
  "brand.subtitle": "多模态票据智能审核平台",
  "demo.load": "加载演示 (30)",
  "source.demo": "演示 · 合成数据",
  "source.upload": "真实上传",
  "batch.label": "批次",
  "lang.en": "EN",
  "lang.zh": "中文",
  "footer": "DocMind · 仅合成样例数据 · 不含真实个人信息",
  "tab.results": "结果",
  "tab.audit": "审计",
  "tab.analysis": "分析",
  "tab.graph": "图谱",
  "analysis.gated": "批次完成后即可使用分析功能。",
  "status.pending": "等待中",
  "status.running": "处理中",
  "status.done": "完成",
  "status.failed": "失败",
  "status.progress": "进度 {done}/{total} · {pct}%",
  "status.summary": "已抽取 {done}/{total} · 共 {findings} 条告警",
  "upload.title": "拖拽发票 PDF 到此处，或点击选择文件",
  "upload.processing": "处理中…",
  "upload.selected": "已选择 {n} 个 PDF，正在处理…",
  "upload.hint": "支持单张或批量 · 200dpi 渲染 · Qwen 视觉抽取",
  "empty.title": "上传发票 PDF，或加载合成演示批次开始体验。",
  "empty.hint":
    "抽取使用 Qwen 视觉模型（DashScope）；审计引擎随后检查重复、算术一致性、税率、二维码与购销方信息。",
  "retry.failed": "重试 {n} 个失败",
  "export.csv": "导出 CSV",
  "export.json": "导出 JSON",
  "retry.single": "重试",
  "results.noResults": "暂无结果。",
  "results.file": "文件",
  "results.invoiceNo": "发票号码",
  "results.date": "日期",
  "results.seller": "销售方",
  "results.buyer": "购买方",
  "results.amount": "金额",
  "results.failed": "抽取失败：{error}",
  "analysis.dateFrom": "开始日期",
  "analysis.dateTo": "结束日期",
  "analysis.buyer": "购买方",
  "analysis.seller": "销售方",
  "analysis.allBuyers": "全部购买方",
  "analysis.allSellers": "全部销售方",
  "analysis.clearFilters": "清除筛选",
  "analysis.stats": "共 {n} 条记录 · 合计 {amount}",
  "analysis.noMatch": "没有符合筛选条件的发票。",
  "analysis.thInvoice": "发票号码",
  "analysis.thDate": "开票日期",
  "analysis.thSeller": "销售方",
  "analysis.thBuyer": "购买方",
  "analysis.thTotal": "价税合计",
  "audit.byRule": "按规则统计告警",
  "audit.bySeverity": "按严重级统计告警",
  "audit.noChartData": "暂无告警数据可绘图。",
  "audit.clean": "无审计告警 —— 批次通过全部规则。",
  "severity.critical": "严重",
  "severity.error": "错误",
  "severity.warning": "警告",
  "severity.info": "提示",
  "graph.building": "图谱构建中…",
  "graph.nodeTypes": "节点类型",
  "graph.company": "公司",
  "graph.allCompanies": "全部公司",
  "graph.minAmount": "最低金额 (¥)",
  "graph.reset": "重置",
  "graph.stats": "节点 {nodes} · 边 {edges} · 合计 {amount}",
  "graph.type.invoice": "发票",
  "graph.type.company": "公司",
  "graph.type.product": "商品",
  "graph.legendEdges": "购买 / 销售",
  "graph.legendEdgesDashed": "包含 / 交易",
  "graph.empty": "没有符合筛选条件的节点。",
  "graph.clearFocus": "取消聚焦",
  "graph.cardType": "类型 · {type}",
  "graph.cardAmount": "金额 · {amount}",
  "graph.cardConnections": "连接数 · {n}",
  "graph.topCompanies": "按交易额排名公司",
  "graph.hint": "点击节点聚焦其邻域；点击空白画布恢复全览",
  "drawer.fields": "字段",
  "drawer.confidence": "模型置信度",
  "drawer.noConfidence": "未提供置信度",
  "drawer.items": "项目明细",
  "drawer.evidence": "审计证据（{n}）",
  "drawer.noFindings": "该发票无审计告警。",
  "drawer.connections": "图谱连接",
  "drawer.noConnections": "无图谱连接。",
  "drawer.nodeProps": "节点属性",
  "drawer.failed": "抽取失败",
  "drawer.close": "关闭抽屉",
};

export const messages: Record<Lang, Record<string, string>> = { en, zh };

interface I18nValue {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (key: string) => string;
  tf: (key: string, vars: Record<string, string | number>) => string;
}

const I18nContext = createContext<I18nValue>({
  lang: "en",
  setLang: () => undefined,
  t: (k) => en[k] ?? k,
  tf: (k, v) => (en[k] ?? k).replace(/\{(\w+)\}/g, (_, name) => String(v[name] ?? "")),
});

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => {
    try {
      return localStorage.getItem("docmind.lang") === "zh" ? "zh" : "en";
    } catch {
      return "en";
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem("docmind.lang", lang);
    } catch {
      /* private mode etc. */
    }
  }, [lang]);

  const value = useMemo<I18nValue>(() => {
    const dict = messages[lang];
    return {
      lang,
      setLang: setLangState,
      t: (key) => dict[key] ?? en[key] ?? key,
      tf: (key, vars) =>
        (dict[key] ?? en[key] ?? key).replace(/\{(\w+)\}/g, (_, name) =>
          String(vars[name] ?? "")
        ),
    };
  }, [lang]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  return useContext(I18nContext);
}
