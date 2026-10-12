"""
Tool: build a dependency cross-reference map from a SymbolMap.

Produces three dependency tables:
  - ImportDep: which file/class imports which qualified type
  - InheritanceDep: extends / implements relationships resolved to project types
  - UsageDep: field-type, parameter-type, return-type usages of project types
"""
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import javalang

from tools.java_scanner import SymbolMap, get_symbol_map

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dependency data models
# ---------------------------------------------------------------------------

@dataclass
class ImportDep:
    source_file: str
    source_qualified_class: str   # e.g. com.example.Foo
    imported_type: str            # fully-qualified import string
    is_project_type: bool         # True if the import resolves to a type in this repo


@dataclass
class InheritanceDep:
    source_qualified_class: str
    relationship: str             # "extends" | "implements"
    target_simple_name: str       # as written in source
    target_qualified_name: str    # resolved to a project type, or raw if external
    is_project_type: bool


@dataclass
class UsageDep:
    source_qualified_class: str
    context: str                  # "field_type" | "param_type" | "return_type"
    member_name: str              # field name or method name
    used_type: str                # simple or partially-qualified name
    resolved_qualified: str       # resolved to a project type, or raw if external
    is_project_type: bool


@dataclass
class DependencyMap:
    imports: list[ImportDep] = field(default_factory=list)
    inheritance: list[InheritanceDep] = field(default_factory=list)
    usage: list[UsageDep] = field(default_factory=list)


# Module-level state
_dep_map: DependencyMap | None = None


# ---------------------------------------------------------------------------
# Public tool function
# ---------------------------------------------------------------------------

def build_dependency_map(repo_dir: str) -> dict[str, Any]:
    """
    Build an import/inheritance/usage cross-reference map for the scanned Java project.

    Requires scan_java_symbols to have been called first (uses module-level SymbolMap).

    Args:
        repo_dir: Absolute path to the cloned repository root.

    Returns:
        dict with import_count, inheritance_count, usage_count, and dep_map summary.
    """
    global _dep_map
    sym = get_symbol_map()
    if sym is None:
        return {"error": "No symbol map found — call scan_java_symbols first."}

    dep_map = DependencyMap()

    # Build lookup sets for quick resolution
    qualified_names: set[str] = {t.qualified_name for t in sym.types}
    simple_to_qualified: dict[str, list[str]] = {}
    for t in sym.types:
        simple_to_qualified.setdefault(t.name, []).append(t.qualified_name)

    # Per-file import index: file_path -> list[import string]
    file_imports: dict[str, list[str]] = _collect_imports(repo_dir, sym)

    # Build import cross-reference
    for file_path, imports in file_imports.items():
        classes_in_file = [t.qualified_name for t in sym.types if t.file_path == file_path]
        src_class = classes_in_file[0] if classes_in_file else file_path
        for imp in imports:
            is_proj = imp in qualified_names or any(
                imp.startswith(qn + ".") for qn in qualified_names
            )
            dep_map.imports.append(ImportDep(
                source_file=file_path,
                source_qualified_class=src_class,
                imported_type=imp,
                is_project_type=is_proj,
            ))

    # Build inheritance cross-reference
    for t in sym.types:
        import_index = set(file_imports.get(t.file_path, []))

        if t.superclass:
            resolved = _resolve(t.superclass, t.package, qualified_names, simple_to_qualified, import_index)
            dep_map.inheritance.append(InheritanceDep(
                source_qualified_class=t.qualified_name,
                relationship="extends",
                target_simple_name=t.superclass,
                target_qualified_name=resolved,
                is_project_type=resolved in qualified_names,
            ))

        for iface in (t.interfaces.split(", ") if t.interfaces else []):
            iface = iface.strip()
            if not iface:
                continue
            resolved = _resolve(iface, t.package, qualified_names, simple_to_qualified, import_index)
            dep_map.inheritance.append(InheritanceDep(
                source_qualified_class=t.qualified_name,
                relationship="implements",
                target_simple_name=iface,
                target_qualified_name=resolved,
                is_project_type=resolved in qualified_names,
            ))

    # Build usage cross-reference (fields)
    for f in sym.fields:
        raw_type = _strip_generics(f.field_type)
        import_index = set(file_imports.get(f.file_path, []))
        pkg = _package_from_class(f.qualified_class)
        resolved = _resolve(raw_type, pkg, qualified_names, simple_to_qualified, import_index)
        if resolved in qualified_names:
            dep_map.usage.append(UsageDep(
                source_qualified_class=f.qualified_class,
                context="field_type",
                member_name=f.field_name,
                used_type=f.field_type,
                resolved_qualified=resolved,
                is_project_type=True,
            ))

    # Build usage cross-reference (method params and return types)
    for m in sym.methods:
        import_index = set(file_imports.get(m.file_path, []))
        pkg = _package_from_class(m.qualified_class)

        # Return type
        if m.return_type:
            raw = _strip_generics(m.return_type)
            resolved = _resolve(raw, pkg, qualified_names, simple_to_qualified, import_index)
            if resolved in qualified_names:
                dep_map.usage.append(UsageDep(
                    source_qualified_class=m.qualified_class,
                    context="return_type",
                    member_name=m.method_name,
                    used_type=m.return_type,
                    resolved_qualified=resolved,
                    is_project_type=True,
                ))

        # Parameters
        for param_str in (m.parameters.split(", ") if m.parameters else []):
            if not param_str.strip():
                continue
            parts = param_str.strip().split()
            if parts:
                ptype = parts[0]
                pname = parts[-1] if len(parts) > 1 else ""
                raw = _strip_generics(ptype)
                resolved = _resolve(raw, pkg, qualified_names, simple_to_qualified, import_index)
                if resolved in qualified_names:
                    dep_map.usage.append(UsageDep(
                        source_qualified_class=m.qualified_class,
                        context="param_type",
                        member_name=f"{m.method_name}:{pname}",
                        used_type=ptype,
                        resolved_qualified=resolved,
                        is_project_type=True,
                    ))

    _dep_map = dep_map
    result = {
        "import_dep_count": len(dep_map.imports),
        "inheritance_dep_count": len(dep_map.inheritance),
        "usage_dep_count": len(dep_map.usage),
        "project_imports": sum(1 for i in dep_map.imports if i.is_project_type),
        "external_imports": sum(1 for i in dep_map.imports if not i.is_project_type),
    }
    logger.info(f"Dependency map built: {result}")
    return result


def get_dependency_map() -> DependencyMap | None:
    return _dep_map


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_imports(repo_dir: str, sym: SymbolMap) -> dict[str, list[str]]:
    """Re-parse each Java file to extract import statements."""
    root = Path(repo_dir)
    file_imports: dict[str, list[str]] = {}
    seen_files = {t.file_path for t in sym.types}
    for rel_path in seen_files:
        abs_path = root / rel_path
        try:
            source = abs_path.read_text(encoding="utf-8", errors="replace")
            tree = javalang.parse.parse(source)
            file_imports[rel_path] = [
                imp.path for imp in (tree.imports or [])
            ]
        except Exception:
            file_imports[rel_path] = []
    return file_imports


def _resolve(
    simple_name: str,
    package: str,
    qualified_names: set[str],
    simple_to_qualified: dict[str, list[str]],
    import_index: set[str],
) -> str:
    """
    Try to resolve a simple type name to a fully-qualified project type.
    Strategy:
      1. Already fully-qualified (contains dot) and in the set → return as-is
      2. Check imports for a matching suffix
      3. Check same-package types
      4. Return as-is (unresolved)
    """
    if not simple_name or simple_name in ("void", "var"):
        return simple_name

    # Already fully-qualified
    if "." in simple_name and simple_name in qualified_names:
        return simple_name

    # Check imports: find an import that ends with .SimpleName
    for imp in import_index:
        if imp.endswith(f".{simple_name}") or imp.endswith(f".{simple_name.split('<')[0]}"):
            if imp in qualified_names:
                return imp

    # Same-package lookup
    same_pkg_candidate = f"{package}.{simple_name}" if package else simple_name
    if same_pkg_candidate in qualified_names:
        return same_pkg_candidate

    # Ambiguous simple name (multiple matches in different packages) → pick first
    candidates = simple_to_qualified.get(simple_name, [])
    if len(candidates) == 1:
        return candidates[0]

    return simple_name  # unresolved


def _strip_generics(type_str: str) -> str:
    """Remove generic parameters: 'List<String>' → 'List'."""
    return type_str.split("<")[0].strip() if type_str else ""


def _package_from_class(qualified_class: str) -> str:
    parts = qualified_class.rsplit(".", 1)
    return parts[0] if len(parts) == 2 else ""
