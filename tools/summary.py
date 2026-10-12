"""
Tool: produce a human-readable summary of the scan results.
"""
from typing import Any

from tools.java_scanner import get_symbol_map
from tools.dependency_builder import get_dependency_map


def get_scan_summary() -> dict[str, Any]:
    """
    Return a human-readable summary of the last Java symbol scan and dependency map.

    Returns:
        dict with top-level statistics, top packages by class count,
        most-depended-upon types, and a breakdown of dependency kinds.
    """
    sym = get_symbol_map()
    deps = get_dependency_map()

    if sym is None:
        return {"error": "No scan results available."}

    # Package breakdown
    from collections import Counter
    pkg_counts = Counter(t.package for t in sym.types)
    top_packages = [
        {"package": pkg, "type_count": cnt}
        for pkg, cnt in pkg_counts.most_common(10)
    ]

    # Kind breakdown
    kind_counts = Counter(t.kind for t in sym.types)

    # Most referenced project types (targets in usage deps)
    most_referenced: list[dict] = []
    if deps:
        ref_counts = Counter(u.resolved_qualified for u in deps.usage if u.is_project_type)
        most_referenced = [
            {"type": t, "reference_count": c}
            for t, c in ref_counts.most_common(15)
        ]

    summary: dict[str, Any] = {
        "files_scanned": len({p.file_path for p in sym.packages}),
        "parse_errors": len(sym.parse_errors),
        "unique_packages": len(pkg_counts),
        "total_types": len(sym.types),
        "type_breakdown": dict(kind_counts),
        "total_methods": len(sym.methods),
        "total_fields": len(sym.fields),
        "top_packages_by_type_count": top_packages,
    }

    if deps:
        summary.update({
            "total_import_deps": len(deps.imports),
            "project_import_deps": sum(1 for i in deps.imports if i.is_project_type),
            "external_import_deps": sum(1 for i in deps.imports if not i.is_project_type),
            "inheritance_deps": len(deps.inheritance),
            "usage_deps": len(deps.usage),
            "most_referenced_project_types": most_referenced,
        })

    return summary
