// Shared API types (mirror the FastAPI schemas).

export type Severity = "INFO" | "WARNING" | "ERROR" | "CRITICAL";

export interface InvoiceItem {
  name: string;
  specification: string | null;
  unit: string | null;
  quantity: number | null;
  unit_price: number | null;
  amount_excluding_tax: number | null;
  tax_rate: number | null;
  tax_amount: number | null;
}

export interface InvoiceParty {
  name: string;
  tax_id: string | null;
}

export interface InvoiceDoc {
  invoice_type: string | null;
  invoice_number: string | null;
  issue_date: string | null;
  buyer: InvoiceParty;
  seller: InvoiceParty;
  items: InvoiceItem[];
  amount_excluding_tax: number | null;
  tax_amount: number | null;
  amount_including_tax: number | null;
  amount_in_words: string | null;
  remarks: string | null;
  issuer: string | null;
  check_code: string | null;
  qr_payload: string | null;
  corrections: Record<string, string>;
  confidence: Record<string, number>;
}

export interface BatchResult {
  filename: string;
  success: boolean;
  invoice_number: string | null;
  error: string | null;
  doc: InvoiceDoc | null;
}

export interface AuditFinding {
  rule_id: string;
  rule_name: string;
  severity: Severity;
  message: string;
  evidence: Record<string, unknown>;
  invoice_index: number | null;
  invoice_number: string | null;
  field: string | null;
}

export interface GraphNode {
  id: string;
  label: string;
  type: "invoice" | "company" | "product";
  properties: Record<string, unknown>;
}

export interface GraphEdge {
  source: string;
  target: string;
  relation: string;
  properties: Record<string, unknown>;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  statistics: {
    total_nodes: number;
    total_edges: number;
    node_types: Record<string, number>;
    edge_types: Record<string, number>;
  };
}

export interface BatchError {
  filename: string | null;
  error: string;
}

/** A rule that crashed during the audit. Isolated, never silently dropped. */
export interface RuleError {
  rule_id: string;
  rule_name: string;
  error: string;
}

export interface AuditSummary {
  total: number;
  by_severity: Record<Severity, number>;
  /** How many documents the findings were derived from (0 = nothing audited). */
  documents_audited?: number;
}

export interface Batch {
  id: string;
  status: "pending" | "running" | "done" | "failed";
  source: "upload" | "demo";
  total: number;
  /** Successful extractions only — failures are counted in `failed`. */
  done: number;
  failed: number;
  files: string[];
  results: (BatchResult | null)[];
  findings: AuditFinding[];
  audit_summary: AuditSummary | null;
  graph: GraphData | null;
  insights: Record<string, unknown>;
  errors: BatchError[];
  rule_errors: RuleError[];
  created_at: string;
  completed_at: string | null;
}

/** `GET /api/batches/{id}/audit` — findings plus whether they mean anything. */
export interface AuditReport {
  findings: AuditFinding[];
  summary: AuditSummary | null;
  status: Batch["status"];
  total: number;
  done: number;
  failed: number;
  errors: BatchError[];
  rule_errors: RuleError[];
  audited_documents: number;
  /**
   * False when the batch did not fully audit — some documents failed to
   * extract, or a rule crashed. An empty `findings` list with
   * `audit_conclusive: false` means "we could not tell", never "clean".
   */
  audit_conclusive: boolean;
}
