#!/usr/bin/env python3
"""Generate a fully synthetic invoice sample set + ground truth.

WHY: the original course-work invoices contain real personal/company data
(real tax ids, real issuer names, real bank accounts). This script produces
30+ fabricated Chinese e-invoice PDFs (reportlab) so the repository contains
NO real personal information, and a machine-readable ground-truth file so the
extraction benchmark is objective.

Outputs
-------
samples/invoice_XXX.pdf   synthetic invoice PDFs
benchmark/ground_truth.json   expected field values per file (English schema)

Some invoices embed *deliberate* anomalies (duplicate numbers, wrong totals,
unusual tax rates, missing seller tax id, self-dealing, future date) so the
audit engine has something realistic to catch in demos. They are annotated in
the ground truth under ``anomalies``.

Usage
-----
python scripts/generate_synthetic_invoices.py [--count 30] [--seed 42]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = REPO_ROOT / "samples"
BENCHMARK_DIR = REPO_ROOT / "benchmark"

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

_USCC_CHARSET = "0123456789ABCDEFGHJKLMNPQRTUWXY"  # no I/O/Z/S/V
_USCC_WEIGHTS = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]


def uscc_check_char(code17: str) -> str:
    """GB 32100-2015 unified-social-credit-code check character."""
    total = sum(_USCC_WEIGHTS[i] * _USCC_CHARSET.index(ch) for i, ch in enumerate(code17))
    return _USCC_CHARSET[(31 - total % 31) % 31]


def make_uscc(rng: random.Random, region: str) -> str:
    """Fabricate a valid-format unified social credit code."""
    body = "91" + region + "".join(rng.choice(_USCC_CHARSET) for _ in range(9))
    return body + uscc_check_char(body)


_CN_NUM = "零壹贰叁肆伍陆柒捌玖"
_CN_UNIT = "拾佰仟万"  # units for digit positions 1..4


def amount_in_words(amount: float) -> str:
    """Convert an RMB amount to Chinese uppercase words (e.g. 199.00 → 壹佰玖拾玖圆整)."""
    total_cents = round(amount * 100)
    yuan, cents = divmod(total_cents, 100)
    if yuan == 0 and cents == 0:
        return "零圆整"

    def int_to_words(n: int) -> str:
        if n == 0:
            return ""
        s = str(n)
        result = ""
        zero_pending = False
        for i, ch in enumerate(s):
            digit = int(ch)
            pos = len(s) - i - 1
            unit = _CN_UNIT[pos - 1] if pos >= 1 else ""
            if digit == 0:
                if result and not result.endswith("零"):
                    zero_pending = True
                continue
            if zero_pending:
                result += "零"
                zero_pending = False
            result += _CN_NUM[digit] + unit
        return result or "零"

    parts: list[str] = []
    if yuan:
        parts.append(int_to_words(yuan) + "圆")
    if cents == 0:
        parts.append("整")
    elif cents % 10 == 0:
        parts.append(_CN_NUM[cents // 10] + "角")
    else:
        if cents // 10:
            parts.append(_CN_NUM[cents // 10] + "角")
        parts.append(_CN_NUM[cents % 10] + "分")
    return "".join(parts)


def fmt_date(d: date) -> str:
    return f"{d.year}年{d.month:02d}月{d.day:02d}日"


# ---------------------------------------------------------------------------
# Pools (all fabricated — no real entities)
# ---------------------------------------------------------------------------

_REGIONS = ["310000", "110000", "440000", "330000", "510000", "320000", "120000", "370000"]

_ADJ = ["华辰", "启明", "恒宇", "瑞丰", "盛达", "科锐", "远景", "博远", "天成", "联创",
        "云帆", "旭日", "宏图", "嘉禾", "瑞祥", "星海", "峻岭", "清源", "朗月", "观澜"]
_INDUSTRY = ["科技", "贸易", "信息技术", "物流", "餐饮", "商务服务", "文化传媒",
             "电子", "机械", "网络", "软件", "咨询"]
_SUFFIX = ["有限公司", "股份有限公司"]

_ISSUERS = ["祁远山", "苏明轩", "骆清扬", "顾星野", "江流云", "白砚秋", "陆青禾", "沈砚舟",
            "温既白", "程慕远"]

# (name, spec, unit, rate) — tax rates follow real-world Chinese VAT rates.
_ITEMS = [
    ("固态硬盘", "1TB", "个", 13.0),
    ("办公耗材", None, "批", 13.0),
    ("网络设备", None, "台", 13.0),
    ("会议注册费", None, "次", 6.0),
    ("服务器租赁服务费", None, "月", 6.0),
    ("物流收派服务费", None, "次", 6.0),
    ("电脑维修服务", None, "次", 1.0),
    ("建筑安装服务", None, "项", 9.0),
    ("餐饮服务", None, "次", 6.0),
    ("云服务器租赁费", "2核4G", "月", 6.0),
    ("培训服务费", None, "期", 6.0),
    ("软件服务费", None, "年", 6.0),
    ("农产品", None, "千克", 9.0),
    ("仓储服务", None, "月", 6.0),
    ("设计服务", None, "次", 6.0),
    ("打印复印服务", None, "次", 13.0),
]

_INVOICE_TYPE = "电子发票（普通发票）"


def make_company(rng: random.Random) -> tuple[str, str]:
    name = rng.choice(_ADJ) + rng.choice(_INDUSTRY) + rng.choice(_SUFFIX)
    return name, make_uscc(rng, rng.choice(_REGIONS))


def make_buyer(rng: random.Random) -> tuple[str, str]:
    return "远景云服务有限公司", make_uscc(rng, "310000")


# ---------------------------------------------------------------------------
# Invoice spec generation
# ---------------------------------------------------------------------------


def _make_item_line(rng: random.Random, base_amount: float) -> dict:
    name, spec, unit, rate = rng.choice(_ITEMS)
    incl = round(base_amount * rng.uniform(0.6, 1.4), 2)
    excl = round(incl / (1 + rate / 100), 2)
    tax = round(incl - excl, 2)
    qty = 1
    unit_price = round(excl / qty, 2)
    return {
        "name": name,
        "specification": spec,
        "unit": unit,
        "quantity": qty,
        "unit_price": unit_price,
        "amount_excluding_tax": excl,
        "tax_rate": rate,
        "tax_amount": tax,
    }


def generate_invoice_spec(rng: random.Random, idx: int) -> tuple[dict, list[str]]:
    """Return (spec dict, anomaly labels) for invoice index ``idx``."""
    anomalies: list[str] = []
    number = "24" + f"{idx:04d}" + "".join(str(rng.randrange(10)) for _ in range(14))
    issue_date = date(2024, 1, 1) + timedelta(days=rng.randrange(0, 500))
    buyer_name, buyer_tax = make_buyer(rng)
    seller_name, seller_tax = make_company(rng)
    issuer = rng.choice(_ISSUERS)
    check_code = "".join(str(rng.randrange(10)) for _ in range(20))

    n_items = rng.choice([1, 1, 1, 2, 2, 3])
    base = rng.choice([30.0, 90.0, 200.0, 500.0, 1200.0, 4000.0])
    items = [_make_item_line(rng, base) for _ in range(n_items)]
    excl_total = round(sum(it["amount_excluding_tax"] for it in items), 2)
    tax_total = round(sum(it["tax_amount"] for it in items), 2)
    incl_total = round(excl_total + tax_total, 2)

    # --- deliberate anomalies (deterministic by index) -------------------
    if idx == 4:
        number = "24417000000019990001"  # duplicated with idx 5
        anomalies.append("duplicate_number")
    if idx == 5:
        number = "24417000000019990001"
        anomalies.append("duplicate_number")
    if idx == 14:
        number = "24417000000029990002"
        anomalies.append("duplicate_number")
    if idx == 15:
        number = "24417000000029990002"
        anomalies.append("duplicate_number")
    if idx == 7:
        incl_total = round(incl_total + 10.00, 2)  # total ≠ excl + tax
        anomalies.append("arithmetic_mismatch")
    if idx == 20:
        incl_total = round(incl_total - 50.00, 2)
        anomalies.append("arithmetic_mismatch")
    if idx == 9:
        items[0]["tax_rate"] = 17.0
        items[0]["tax_amount"] = round(items[0]["amount_excluding_tax"] * 0.17, 2)
        tax_total = round(sum(it["tax_amount"] for it in items), 2)
        incl_total = round(excl_total + tax_total, 2)
        anomalies.append("anomalous_tax_rate")
    if idx == 18:
        items[0]["tax_rate"] = 0.5
        items[0]["tax_amount"] = round(items[0]["amount_excluding_tax"] * 0.005, 2)
        tax_total = round(sum(it["tax_amount"] for it in items), 2)
        incl_total = round(excl_total + tax_total, 2)
        anomalies.append("anomalous_tax_rate")
    if idx == 12:
        seller_tax = None
        anomalies.append("missing_seller_tax_id")
    if idx == 24:
        buyer_name = seller_name
        anomalies.append("self_dealing")
    if idx == 27:
        issue_date = date(2030, 1, 15)
        anomalies.append("future_date")

    # QR payload — normally consistent with the printed fields; for idx 22 we
    # deliberately encode a different amount so the qr_crosscheck rule fires.
    qr_payload = f"01,32,,{number},{incl_total:.2f},{issue_date.strftime('%Y%m%d')}"
    if idx == 22:
        qr_payload = f"01,32,,{number},{incl_total + 100.00:.2f},{issue_date.strftime('%Y%m%d')}"
        anomalies.append("qr_mismatch")

    spec = {
        "invoice_type": _INVOICE_TYPE,
        "invoice_number": number,
        "issue_date": issue_date.isoformat(),
        "buyer": {"name": buyer_name, "tax_id": buyer_tax},
        "seller": {"name": seller_name, "tax_id": seller_tax},
        "items": items,
        "amount_excluding_tax": excl_total,
        "tax_amount": tax_total,
        "amount_including_tax": incl_total,
        "amount_in_words": amount_in_words(incl_total),
        "remarks": "合成样例数据，仅供演示与评测（DocMind）",
        "issuer": issuer,
        "check_code": check_code,
        "qr_payload": qr_payload,
        "anomalies": anomalies,
    }
    return spec, anomalies


# ---------------------------------------------------------------------------
# PDF rendering (reportlab)
# ---------------------------------------------------------------------------


def render_invoice_pdf(spec: dict, path: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import (
        Image,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    FONT = "STSong-Light"

    style_title = ParagraphStyle("t", fontName=FONT, fontSize=18, leading=24, alignment=1)
    style_label = ParagraphStyle("l", fontName=FONT, fontSize=9, leading=13)
    style_value = ParagraphStyle("v", fontName=FONT, fontSize=9, leading=13)

    qr = _make_qr_png(spec.get("qr_payload") or _default_qr_payload(spec))

    doc = SimpleDocTemplate(str(path), pagesize=A4,
                            leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm)
    story = []

    story.append(Paragraph("电子发票（普通发票）", style_title))
    story.append(Spacer(1, 6))
    story.append(Image(qr, width=26 * mm, height=26 * mm))
    story.append(Spacer(1, 4))

    head = Table(
        [[Paragraph(f"发票号码：{spec['invoice_number']}", style_value),
          Paragraph(f"开票日期：{fmt_date(date.fromisoformat(spec['issue_date']))}", style_value)]],
        colWidths=[90 * mm, 90 * mm],
    )
    head.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), FONT)]))
    story.append(head)
    story.append(Spacer(1, 6))

    parties = Table(
        [
            [Paragraph("购买方", style_label), Paragraph(spec["buyer"]["name"], style_value)],
            [Paragraph("购买方税号", style_label), Paragraph(spec["buyer"]["tax_id"] or "—", style_value)],
            [Paragraph("销售方", style_label), Paragraph(spec["seller"]["name"], style_value)],
            [Paragraph("销售方税号", style_label), Paragraph(spec["seller"]["tax_id"] or "—", style_value)],
        ],
        colWidths=[30 * mm, 70 * mm, 30 * mm, 50 * mm],
    )
    parties.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("BACKGROUND", (0, 0), (0, -1), colors.Color(0.95, 0.95, 0.95)),
        ("BACKGROUND", (2, 0), (2, -1), colors.Color(0.95, 0.95, 0.95)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(parties)
    story.append(Spacer(1, 6))

    rows = [[Paragraph("项目名称", style_label), Paragraph("规格型号", style_label),
             Paragraph("单位", style_label), Paragraph("数量", style_label),
             Paragraph("单价", style_label), Paragraph("金额", style_label),
             Paragraph("税率", style_label), Paragraph("税额", style_label)]]
    for it in spec["items"]:
        rows.append([
            Paragraph(it["name"], style_value),
            Paragraph(it["specification"] or "—", style_value),
            Paragraph(it["unit"] or "—", style_value),
            Paragraph(f"{it['quantity']:g}", style_value),
            Paragraph(f"{it['unit_price']:.2f}", style_value),
            Paragraph(f"{it['amount_excluding_tax']:.2f}", style_value),
            Paragraph(f"{it['tax_rate']:g}%", style_value),
            Paragraph(f"{it['tax_amount']:.2f}", style_value),
        ])
    item_table = Table(rows, colWidths=[42 * mm, 22 * mm, 12 * mm, 14 * mm, 22 * mm, 22 * mm, 14 * mm, 22 * mm])
    item_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.92, 0.92, 0.92)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(item_table)
    story.append(Spacer(1, 6))

    totals = Table(
        [
            [Paragraph(f"价税合计（大写）：{spec['amount_in_words']}", style_value),
             Paragraph(f"¥ {spec['amount_including_tax']:.2f}", style_value)],
        ],
        colWidths=[120 * mm, 60 * mm],
    )
    totals.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("BACKGROUND", (0, 0), (-1, -1), colors.Color(0.97, 0.97, 0.97)),
    ]))
    story.append(totals)
    story.append(Spacer(1, 6))

    foot = Table(
        [
            [Paragraph(f"校验码：{spec['check_code']}", style_value),
             Paragraph(f"开票人：{spec['issuer']}", style_value)],
            [Paragraph(f"备注：{spec['remarks']}", style_value), Paragraph("", style_value)],
        ],
        colWidths=[90 * mm, 90 * mm],
    )
    foot.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), FONT)]))
    story.append(foot)

    doc.build(story)


def _default_qr_payload(spec: dict) -> str:
    """Fallback payload derived from printed fields (for legacy specs)."""
    return (
        f"01,32,,{spec['invoice_number']},{spec['amount_including_tax']:.2f},"
        f"{spec['issue_date'].replace('-', '')}"
    )


def _make_qr_png(payload: str) -> BytesIO:
    import qrcode

    img = qrcode.make(payload, box_size=6, border=1)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=30, help="number of invoices (default 30)")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed (default 42)")
    parser.add_argument("--pdfs-only", action="store_true",
                        help="regenerate PDFs reusing the existing ground truth")
    args = parser.parse_args()

    SAMPLES_DIR.mkdir(exist_ok=True)
    BENCHMARK_DIR.mkdir(exist_ok=True)
    rng = random.Random(args.seed)

    gt_path = BENCHMARK_DIR / "ground_truth.json"
    existing = {}
    if args.pdfs_only and gt_path.is_file():
        existing = json.loads(gt_path.read_text(encoding="utf-8")).get("files", {})

    files: dict[str, dict] = {}
    for idx in range(1, args.count + 1):
        filename = f"invoice_{idx:03d}.pdf"
        if filename in existing:
            spec = existing[filename]
            anomalies = spec.pop("anomalies", [])
        else:
            spec, anomalies = generate_invoice_spec(rng, idx)
        render_invoice_pdf(spec, SAMPLES_DIR / filename)
        entry = {k: v for k, v in spec.items()}
        entry["anomalies"] = anomalies
        files[filename] = entry
        label = f"  [{', '.join(anomalies)}]" if anomalies else ""
        print(f"  generated {filename} — {spec['seller']['name']} → "
              f"{spec['buyer']['name']} ¥{spec['amount_including_tax']:.2f}{label}")

    gt = {
        "schema_version": 1,
        "note": "Fully synthetic data — no real personal or company information. "
                "Anomalies are intentionally injected for the audit engine demo.",
        "anomaly_count": sum(1 for f in files.values() if f.get("anomalies")),
        "files": files,
    }
    gt_path.write_text(json.dumps(gt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {len(files)} sample PDFs to samples/")
    print(f"Wrote ground truth to {gt_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
