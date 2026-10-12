"""
Tool: write symbol map and dependency data as CSV files.

Produces:
  - symbols_classes.csv    — all types (class/interface/enum/annotation)
  - symbols_methods.csv    — all methods and constructors
  - symbols_fields.csv     — all fields
  - deps_imports.csv       — import-level dependencies
  - deps_inheritance.csv   — extends/implements relationships
  - deps_usage.csv         — type-usage cross-references
  - summary.csv            — per-file statistics
  - parse_errors.csv       — files that failed to parse (if any)
"""
import csv
import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from tools.java_scanner import get_symbol_map
from tools.dependency_builder import get_dependency_map

logger = logging.getLogger(__name__)


def write_csvs(output_dir: str) -> dict[str, Any]:
    """
    Write all symbol and dependency data to CSV files in output_dir.

    Args:
        output_dir: Directory where CSV files will be written. Created if absent.

    Returns:
        dict with file paths for each CSV written and row counts.
    """
    sym = get_symbol_map()
    deps = get_dependency_map()

    if sym is None:
        return {"error": "No symbol map — call scan_java_symbols first."}
    if deps is None:
        return {"error": "No dependency map — call build_dependency_map first."}

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    written: dict[str, Any] = {"output_dir": output_dir, "files": {}}

    # -------------------------------------------------------------------
    # symbols_classes.csv
    # -------------------------------------------------------------------
    classes_path = os.path.join(output_dir, "symbols_classes.csv")
    _write_csv(
        classes_path,
        headers=[
            "file_path", "package", "name", "qualified_name", "kind",
            "modifiers", "superclass", "interfaces", "outer_class", "line",
        ],
        rows=[asdict(t) for t in sym.types],
    )
    written["files"]["symbols_classes.csv"] = {
        "path": classes_path,
        "rows": len(sym.types),
    }

    # -------------------------------------------------------------------
    # symbols_methods.csv
    # -------------------------------------------------------------------
    methods_path = os.path.join(output_dir, "symbols_methods.csv")
    _write_csv(
        methods_path,
        headers=[
            "file_path", "qualified_class", "method_name", "return_type",
            "parameters", "modifiers", "kind", "line",
        ],
        rows=[asdict(m) for m in sym.methods],
    )
    written["files"]["symbols_methods.csv"] = {
        "path": methods_path,
        "rows": len(sym.methods),
    }

    # -------------------------------------------------------------------
    # symbols_fields.csv
    # -------------------------------------------------------------------
    fields_path = os.path.join(output_dir, "symbols_fields.csv")
    _write_csv(
        fields_path,
        headers=[
            "file_path", "qualified_class", "field_name", "field_type",
            "modifiers", "line",
        ],
        rows=[asdict(f) for f in sym.fields],
    )
    written["files"]["symbols_fields.csv"] = {
        "path": fields_path,
        "rows": len(sym.fields),
    }

    # -------------------------------------------------------------------
    # deps_imports.csv
    # -------------------------------------------------------------------
    imports_path = os.path.join(output_dir, "deps_imports.csv")
    _write_csv(
        imports_path,
        headers=[
            "source_file", "source_qualified_class", "imported_type", "is_project_type",
        ],
        rows=[asdict(i) for i in deps.imports],
    )
    written["files"]["deps_imports.csv"] = {
        "path": imports_path,
        "rows": len(deps.imports),
    }

    # -------------------------------------------------------------------
    # deps_inheritance.csv
    # -------------------------------------------------------------------
    inherit_path = os.path.join(output_dir, "deps_inheritance.csv")
    _write_csv(
        inherit_path,
        headers=[
            "source_qualified_class", "relationship", "target_simple_name",
            "target_qualified_name", "is_project_type",
        ],
        rows=[asdict(i) for i in deps.inheritance],
    )
    written["files"]["deps_inheritance.csv"] = {
        "path": inherit_path,
        "rows": len(deps.inheritance),
    }

    # -------------------------------------------------------------------
    # deps_usage.csv
    # -------------------------------------------------------------------
    usage_path = os.path.join(output_dir, "deps_usage.csv")
    _write_csv(
        usage_path,
        headers=[
            "source_qualified_class", "context", "member_name",
            "used_type", "resolved_qualified", "is_project_type",
        ],
        rows=[asdict(u) for u in deps.usage],
    )
    written["files"]["deps_usage.csv"] = {
        "path": usage_path,
        "rows": len(deps.usage),
    }

    # -------------------------------------------------------------------
    # summary.csv  — per-file statistics
    # -------------------------------------------------------------------
    summary_rows = _build_summary(sym)
    summary_path = os.path.join(output_dir, "summary.csv")
    _write_csv(
        summary_path,
        headers=["file_path", "package", "type_count", "method_count", "field_count"],
        rows=summary_rows,
    )
    written["files"]["summary.csv"] = {
        "path": summary_path,
        "rows": len(summary_rows),
    }

    # -------------------------------------------------------------------
    # parse_errors.csv  — only written if there are errors
    # -------------------------------------------------------------------
    if sym.parse_errors:
        errors_path = os.path.join(output_dir, "parse_errors.csv")
        _write_csv(
            errors_path,
            headers=["file_path", "error"],
            rows=[asdict(e) for e in sym.parse_errors],
        )
        written["files"]["parse_errors.csv"] = {
            "path": errors_path,
            "rows": len(sym.parse_errors),
        }

    logger.info(f"CSV output written to {output_dir}: {list(written['files'].keys())}")
    return written


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_csv(path: str, headers: list[str], rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    logger.debug(f"Wrote {len(rows)} rows to {path}")


def _build_summary(sym) -> list[dict]:
    """Compute per-file counts."""
    from collections import defaultdict

    file_pkg: dict[str, str] = {p.file_path: p.package for p in sym.packages}
    type_counts: dict[str, int] = defaultdict(int)
    method_counts: dict[str, int] = defaultdict(int)
    field_counts: dict[str, int] = defaultdict(int)

    for t in sym.types:
        type_counts[t.file_path] += 1
    for m in sym.methods:
        method_counts[m.file_path] += 1
    for f in sym.fields:
        field_counts[f.file_path] += 1

    all_files = (
        {p.file_path for p in sym.packages}
        | {t.file_path for t in sym.types}
    )

    return [
        {
            "file_path": fp,
            "package": file_pkg.get(fp, ""),
            "type_count": type_counts[fp],
            "method_count": method_counts[fp],
            "field_count": field_counts[fp],
        }
        for fp in sorted(all_files)
    ]
