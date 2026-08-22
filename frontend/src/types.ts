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

export interface Batch {
  id: string;
  status: "pending" | "running" | "done" | "failed";
  source: "upload" | "demo";
  total: number;
  done: number;
  files: string[];
  results: (BatchResult | null)[];
  findings: AuditFinding[];
  audit_summary: { total: number; by_severity: Record<Severity, number> } | null;
  graph: GraphData | null;
  insights: Record<string, unknown>;
  errors: string[];
  created_at: string;
  completed_at: string | null;
}
