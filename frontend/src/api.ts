// Thin API client for the DocMind backend.

import type { AuditFinding, Batch, GraphData } from "./types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export interface UploadResponse {
  batch_id: string;
  total: number;
  status: string;
}

export function uploadInvoices(files: File[]): Promise<UploadResponse> {
  const form = new FormData();
  for (const file of files) form.append("files", file);
  return request<UploadResponse>("/api/invoices/upload", { method: "POST", body: form });
}

export interface DemoResponse {
  batch_id: string;
  status: string;
  total: number;
  findings_count: number;
  graph_nodes: number;
}

export function loadDemo(count: number): Promise<DemoResponse> {
  return request<DemoResponse>("/api/demo/load", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ count }),
  });
}

export function getBatch(batchId: string): Promise<Batch> {
  return request<Batch>(`/api/batches/${batchId}`);
}

export function getGraph(batchId: string): Promise<{ graph: GraphData; insights: Record<string, unknown> }> {
  return request(`/api/batches/${batchId}/graph`);
}

export function getAudit(batchId: string): Promise<{ findings: AuditFinding[] }> {
  return request(`/api/batches/${batchId}/audit`);
}

export function exportUrl(batchId: string, format: "csv" | "json"): string {
  return `/api/batches/${batchId}/export?format=${format}`;
}
