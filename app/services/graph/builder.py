"""Knowledge-graph construction over extracted invoices (networkx)."""

from __future__ import annotations

import hashlib
from collections import defaultdict

import networkx as nx

from app.models.invoice import InvoiceDocument


class GraphBuilder:
    """Builds a directed multi-graph of companies, invoices and products.

    Node types: ``invoice``, ``company``, ``product``.
    Edge relations: ``bought`` / ``sold`` (company → invoice),
    ``contains`` (invoice → product), ``transaction`` (seller → buyer,
    aggregated over the batch).
    """

    def __init__(self) -> None:
        self.graph: nx.MultiDiGraph = nx.MultiDiGraph()
        self._company_stats: dict[str, dict] = defaultdict(
            lambda: {
                "invoice_count": 0,
                "total_amount": 0.0,
                "roles": set(),
                "tax_ids": set(),
            }
        )
        self._product_stats: dict[str, dict] = defaultdict(
            lambda: {
                "invoice_count": 0,
                "total_amount": 0.0,
                "companies": set(),
            }
        )
        self._transactions: dict[tuple[str, str], dict] = defaultdict(
            lambda: {"total_amount": 0.0, "transaction_count": 0, "invoices": []}
        )

    @staticmethod
    def _node_id(node_type: str, value: str) -> str:
        digest = hashlib.sha1(value.strip().encode("utf-8")).hexdigest()[:10]
        return f"{node_type}:{digest}"

    @staticmethod
    def _amount(doc: InvoiceDocument) -> float:
        return doc.amount_including_tax or 0.0

    def build(self, batch: list[InvoiceDocument]) -> dict:
        self.graph = nx.MultiDiGraph()
        self._company_stats.clear()
        self._product_stats.clear()
        self._transactions.clear()

        for doc in batch:
            self._add_invoice(doc)

        self._finalize_node_properties()
        self._add_transaction_edges()
        return self.serialize()

    # -- construction -------------------------------------------------

    def _add_invoice(self, doc: InvoiceDocument) -> None:
        number = (doc.invoice_number or "").strip()
        if not number:
            return
        invoice_id = self._node_id("invoice", number)
        self.graph.add_node(
            invoice_id,
            label=f"Invoice {number}",
            type="invoice",
            properties={
                "number": number,
                "type": doc.invoice_type,
                "date": doc.issue_date.isoformat() if doc.issue_date else None,
                "amount": self._amount(doc),
            },
        )

        buyer_id = self._add_company(doc.buyer.name, "buyer", doc.buyer.tax_id)
        if buyer_id:
            self.graph.add_edge(
                buyer_id, invoice_id, relation="bought",
                properties={"amount": self._amount(doc), "date": doc.issue_date.isoformat() if doc.issue_date else None},
            )
            self._company_stats[doc.buyer.name]["total_amount"] += self._amount(doc)
            self._company_stats[doc.buyer.name]["invoice_count"] += 1

        seller_id = self._add_company(doc.seller.name, "seller", doc.seller.tax_id)
        if seller_id:
            self.graph.add_edge(
                seller_id, invoice_id, relation="sold",
                properties={"amount": self._amount(doc), "date": doc.issue_date.isoformat() if doc.issue_date else None},
            )
            self._company_stats[doc.seller.name]["total_amount"] += self._amount(doc)
            self._company_stats[doc.seller.name]["invoice_count"] += 1

        if buyer_id and seller_id:
            key = (seller_id, buyer_id)
            self._transactions[key]["total_amount"] += self._amount(doc)
            self._transactions[key]["transaction_count"] += 1
            self._transactions[key]["invoices"].append(invoice_id)

        for item in doc.items:
            product_name = (item.name or "").strip()
            if not product_name:
                continue
            product_id = self._node_id("product", product_name)
            self.graph.add_node(
                product_id,
                label=product_name,
                type="product",
                properties={
                    "name": product_name,
                    "specification": item.specification,
                    "unit": item.unit,
                    "tax_rate": item.tax_rate,
                },
            )
            self.graph.add_edge(
                invoice_id, product_id, relation="contains",
                properties={
                    "quantity": item.quantity,
                    "unit_price": item.unit_price,
                    "amount": item.amount_excluding_tax,
                    "tax_amount": item.tax_amount,
                },
            )
            self._product_stats[product_name]["total_amount"] += item.amount_excluding_tax or 0.0
            self._product_stats[product_name]["invoice_count"] += 1
            if buyer_id:
                self._product_stats[product_name]["companies"].add(doc.buyer.name)

    def _add_company(self, name: str, role: str, tax_id: str | None) -> str | None:
        name = (name or "").strip()
        if not name:
            return None
        company_id = self._node_id("company", name)
        self.graph.add_node(
            company_id,
            label=name,
            type="company",
            properties={"name": name, "role": role, "tax_id": tax_id},
        )
        stats = self._company_stats[name]
        stats["roles"].add(role)
        if tax_id:
            stats["tax_ids"].add(tax_id)
        return company_id

    def _finalize_node_properties(self) -> None:
        for name, stats in self._company_stats.items():
            company_id = self._node_id("company", name)
            if self.graph.has_node(company_id):
                self.graph.nodes[company_id]["properties"].update(
                    {
                        "invoice_count": stats["invoice_count"],
                        "total_amount": stats["total_amount"],
                        "all_roles": sorted(stats["roles"]),
                        "all_tax_ids": sorted(stats["tax_ids"]),
                    }
                )
        for name, stats in self._product_stats.items():
            product_id = self._node_id("product", name)
            if self.graph.has_node(product_id):
                self.graph.nodes[product_id]["properties"].update(
                    {
                        "invoice_count": stats["invoice_count"],
                        "total_amount": stats["total_amount"],
                        "related_companies": sorted(stats["companies"]),
                    }
                )

    def _add_transaction_edges(self) -> None:
        for (seller_id, buyer_id), stats in self._transactions.items():
            self.graph.add_edge(
                seller_id, buyer_id, relation="transaction",
                properties={
                    "total_amount": stats["total_amount"],
                    "transaction_count": stats["transaction_count"],
                    "invoices": stats["invoices"],
                },
            )

    # -- serialization -------------------------------------------------

    def serialize(self) -> dict:
        nodes = [
            {
                "id": node_id,
                "label": data["label"],
                "type": data["type"],
                "properties": data["properties"],
            }
            for node_id, data in self.graph.nodes(data=True)
        ]
        edges = [
            {
                "source": source,
                "target": target,
                "relation": data.get("relation"),
                "properties": data.get("properties", {}),
            }
            for source, target, data in self.graph.edges(data=True)
        ]
        return {"nodes": nodes, "edges": edges, "statistics": self._statistics()}

    def _statistics(self) -> dict:
        node_types: dict[str, int] = defaultdict(int)
        edge_types: dict[str, int] = defaultdict(int)
        for _, data in self.graph.nodes(data=True):
            node_types[data["type"]] += 1
        for _, _, data in self.graph.edges(data=True):
            edge_types[data.get("relation", "unknown")] += 1
        return {
            "total_nodes": self.graph.number_of_nodes(),
            "total_edges": self.graph.number_of_edges(),
            "node_types": dict(node_types),
            "edge_types": dict(edge_types),
        }

    # -- insights ------------------------------------------------------

    def build_with_insights(self, batch: list[InvoiceDocument]) -> dict:
        graph = self.build(batch)
        return {"graph": graph, "insights": self._insights(graph)}

    def _insights(self, graph: dict) -> dict:
        companies = [
            n
            for n in graph["nodes"]
            if n["type"] == "company" and n["properties"].get("invoice_count", 0) > 0
        ]
        products = [
            n for n in graph["nodes"] if n["type"] == "product"
        ]
        transactions = [
            e for e in graph["edges"] if e["relation"] == "transaction"
        ]
        return {
            "top_companies_by_amount": [
                {
                    "name": n["label"],
                    "amount": n["properties"]["total_amount"],
                    "invoice_count": n["properties"]["invoice_count"],
                    "roles": n["properties"].get("all_roles", []),
                }
                for n in sorted(
                    companies,
                    key=lambda n: n["properties"]["total_amount"],
                    reverse=True,
                )[:10]
            ],
            "top_products_by_amount": [
                {
                    "name": n["label"],
                    "amount": n["properties"]["total_amount"],
                    "invoice_count": n["properties"]["invoice_count"],
                }
                for n in sorted(
                    products,
                    key=lambda n: n["properties"]["total_amount"],
                    reverse=True,
                )[:10]
            ],
            "business_relationships": sorted(
                transactions,
                key=lambda e: e["properties"]["total_amount"],
                reverse=True,
            )[:10],
        }
