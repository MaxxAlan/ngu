from __future__ import annotations

import csv
import io
import json
import sqlite3
from pathlib import Path
from typing import Any

from .common import ToolError, allow_path, clip, resolve_path


def read_excel(
    path: str,
    sheet_name: str | None = None,
    max_rows: int = 100,
    max_cols: int = 30,
) -> str:
    """Read an Excel file (.xlsx, .xlsm, .xltx) and return its structure and formatted table data."""
    allow_path(path)
    p = resolve_path(path)
    if not p.exists():
        raise ToolError(f"file not found: {p}")
    if p.suffix.lower() not in {".xlsx", ".xlsm", ".xltx", ".xls"}:
        raise ToolError(f"not an Excel file: {p.name}")

    try:
        import openpyxl
    except ImportError as exc:
        raise ToolError(f"openpyxl is not installed: {exc}") from exc

    try:
        # data_only=True evaluates formulas to their cached values
        wb = openpyxl.load_workbook(p, data_only=True, read_only=True)
    except Exception as exc:
        raise ToolError(f"cannot open Excel workbook {p}: {exc}") from exc

    sheet_names = wb.sheetnames
    if not sheet_names:
        return f"Excel file {p.name} has no sheets."

    target_sheet = sheet_name if (sheet_name and sheet_name in sheet_names) else sheet_names[0]
    ws = wb[target_sheet]

    rows: list[list[str]] = []
    total_rows = 0

    for r_idx, row in enumerate(ws.iter_rows(values_only=True)):
        total_rows += 1
        if r_idx < max_rows:
            formatted_row = [
                str(cell) if cell is not None else ""
                for cell in row[:max_cols]
            ]
            # Strip trailing empty cells
            while formatted_row and not formatted_row[-1].strip():
                formatted_row.pop()
            if any(c.strip() for c in formatted_row):
                rows.append(formatted_row)

    wb.close()

    if not rows:
        return (
            f"Excel file: {p.name}\n"
            f"Sheets: {', '.join(sheet_names)}\n"
            f"Sheet '{target_sheet}' is empty."
        )

    # Build Markdown table
    header = rows[0]
    md_lines: list[str] = [
        f"### Excel: {p.name} | Sheet: {target_sheet} (Sheets available: {', '.join(sheet_names)})",
        f"Showing {len(rows)} of {total_rows} rows (max cols: {max_cols}):",
        "",
    ]

    col_count = max(len(r) for r in rows)
    # Normalize row lengths
    norm_rows = [r + [""] * (col_count - len(r)) for r in rows]

    header_cells = [c.replace("\n", " ").strip() or f"Col_{i+1}" for i, c in enumerate(norm_rows[0])]
    md_lines.append("| " + " | ".join(header_cells) + " |")
    md_lines.append("| " + " | ".join(["---"] * col_count) + " |")

    for r in norm_rows[1:]:
        cells = [c.replace("\n", " ").strip() for c in r]
        md_lines.append("| " + " | ".join(cells) + " |")

    if total_rows > max_rows:
        md_lines.append(f"\n... [{total_rows - max_rows} more rows not shown. Adjust max_rows or query specific rows]")

    return clip("\n".join(md_lines), 8000)


def read_docx(path: str, max_paragraphs: int = 300) -> str:
    """Read a Word document (.docx) extracting headings, paragraphs, and tables."""
    allow_path(path)
    p = resolve_path(path)
    if not p.exists():
        raise ToolError(f"file not found: {p}")
    if p.suffix.lower() != ".docx":
        raise ToolError(f"not a .docx file: {p.name}")

    try:
        import docx
    except ImportError as exc:
        raise ToolError(f"python-docx is not installed: {exc}") from exc

    try:
        doc = docx.Document(p)
    except Exception as exc:
        raise ToolError(f"cannot read Word document {p}: {exc}") from exc

    out: list[str] = [f"### Word Document: {p.name}\n"]
    p_count = 0

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        p_count += 1
        if p_count > max_paragraphs:
            out.append(f"\n... [{len(doc.paragraphs) - max_paragraphs} more paragraphs truncated]")
            break

        style_name = (para.style.name if para.style else "").lower()
        if "heading 1" in style_name:
            out.append(f"# {text}")
        elif "heading 2" in style_name:
            out.append(f"## {text}")
        elif "heading 3" in style_name:
            out.append(f"### {text}")
        else:
            out.append(text)

    # Tables in docx
    if doc.tables:
        out.append(f"\n#### Tables in document ({len(doc.tables)} tables):\n")
        for t_idx, table in enumerate(doc.tables[:5]):
            out.append(f"**Table {t_idx + 1}:**")
            table_rows: list[list[str]] = []
            for row in table.rows:
                table_rows.append([cell.text.replace("\n", " ").strip() for cell in row.cells])
            if table_rows:
                out.append("| " + " | ".join(table_rows[0]) + " |")
                out.append("| " + " | ".join(["---"] * len(table_rows[0])) + " |")
                for r in table_rows[1:30]:
                    out.append("| " + " | ".join(r) + " |")
            out.append("")

    return clip("\n".join(out), 8000)


def read_pdf(path: str, max_pages: int = 20) -> str:
    """Read a PDF file extracting text page by page."""
    allow_path(path)
    p = resolve_path(path)
    if not p.exists():
        raise ToolError(f"file not found: {p}")
    if p.suffix.lower() != ".pdf":
        raise ToolError(f"not a PDF file: {p.name}")

    try:
        import pdfplumber
    except ImportError as exc:
        raise ToolError(f"pdfplumber is not installed: {exc}") from exc

    try:
        with pdfplumber.open(p) as pdf:
            total_pages = len(pdf.pages)
            out: list[str] = [f"### PDF Document: {p.name} (Total pages: {total_pages})\n"]
            for idx, page in enumerate(pdf.pages[:max_pages]):
                text = (page.extract_text() or "").strip()
                out.append(f"--- Page {idx + 1} ---")
                out.append(text if text else "[Empty or image-based page]")
                out.append("")

            if total_pages > max_pages:
                out.append(f"\n... [{total_pages - max_pages} more pages truncated]")
            return clip("\n".join(out), 8000)
    except Exception as exc:
        raise ToolError(f"cannot read PDF {p}: {exc}") from exc


def inspect_data(path: str) -> str:
    """Inspect structured data files (CSV, JSON, SQLite, Parquet, XML) and return schema, stats, and preview."""
    allow_path(path)
    p = resolve_path(path)
    if not p.exists():
        raise ToolError(f"file not found: {p}")

    ext = p.suffix.lower()

    # Excel
    if ext in {".xlsx", ".xlsm", ".xls"}:
        return read_excel(str(p), max_rows=25)

    # Word
    if ext == ".docx":
        return read_docx(str(p), max_paragraphs=50)

    # PDF
    if ext == ".pdf":
        return read_pdf(str(p), max_pages=5)

    # SQLite
    if ext in {".db", ".sqlite", ".sqlite3"}:
        try:
            conn = sqlite3.connect(p)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [row[0] for row in cursor.fetchall()]
            summary: list[str] = [f"### SQLite Database: {p.name} (Tables: {len(tables)})"]
            for tbl in tables[:10]:
                cursor.execute(f"PRAGMA table_info({tbl});")
                cols = [f"{col[1]} ({col[2]})" for col in cursor.fetchall()]
                cursor.execute(f"SELECT COUNT(*) FROM {tbl};")
                count = cursor.fetchone()[0]
                summary.append(f"\nTable `{tbl}` ({count} rows):\nColumns: {', '.join(cols)}")
                # Sample 3 rows
                cursor.execute(f"SELECT * FROM {tbl} LIMIT 3;")
                sample = cursor.fetchall()
                if sample:
                    summary.append(f"Sample: {json.dumps(sample, ensure_ascii=False)}")
            conn.close()
            return clip("\n".join(summary), 6000)
        except Exception as exc:
            raise ToolError(f"cannot inspect SQLite DB {p}: {exc}") from exc

    # CSV / TSV
    if ext in {".csv", ".tsv"}:
        delimiter = "\t" if ext == ".tsv" else ","
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                reader = csv.reader(f, delimiter=delimiter)
                rows = [next(reader) for _ in range(25)]
            if not rows:
                return f"CSV {p.name} is empty."
            header = rows[0]
            lines = [
                f"### CSV File: {p.name}",
                f"Columns ({len(header)}): {', '.join(header)}",
                "",
                "| " + " | ".join(header) + " |",
                "| " + " | ".join(["---"] * len(header)) + " |",
            ]
            for r in rows[1:]:
                # pad or slice
                padded = (r + [""] * len(header))[:len(header)]
                lines.append("| " + " | ".join(c.replace("\n", " ").strip() for c in padded) + " |")
            return clip("\n".join(lines), 6000)
        except Exception as exc:
            raise ToolError(f"cannot inspect CSV {p}: {exc}") from exc

    # JSON / JSONL
    if ext in {".json", ".jsonl"}:
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
            if ext == ".jsonl":
                lines = [json.loads(line) for line in content.splitlines()[:10] if line.strip()]
                return clip(f"### JSONL: {p.name} ({len(content.splitlines())} lines)\nFirst 10 items:\n" + json.dumps(lines, indent=2, ensure_ascii=False), 6000)
            data = json.loads(content)
            if isinstance(data, list):
                summary = f"### JSON Array: {p.name} ({len(data)} items)\nFirst item schema:\n"
                sample = data[0] if data else {}
                summary += json.dumps(sample, indent=2, ensure_ascii=False)
                return clip(summary, 6000)
            if isinstance(data, dict):
                keys = list(data.keys())
                summary = f"### JSON Object: {p.name} (Top keys: {', '.join(keys[:30])})\nPreview:\n"
                summary += json.dumps({k: data[k] for k in keys[:10]}, indent=2, ensure_ascii=False)
                return clip(summary, 6000)
        except Exception as exc:
            raise ToolError(f"cannot inspect JSON {p}: {exc}") from exc

    # Fallback to plain text read
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        return clip(f"### File: {p.name} ({len(text)} chars)\nPreview:\n{text[:2000]}", 4000)
    except Exception as exc:
        raise ToolError(f"unsupported format for inspect_data: {p.name} ({exc})") from exc
