"""
Tool: scan Java source files and extract a symbol map.

Uses javalang for AST parsing. Produces structured records for:
  - Packages (one per file)
  - Types: classes, interfaces, enums, annotations
  - Methods (including constructors)
  - Fields
  - Parse errors
"""
import logging
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import javalang

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class PackageRecord:
    file_path: str
    package: str


@dataclass
class TypeRecord:
    file_path: str
    package: str
    name: str
    qualified_name: str          # package.Name
    kind: str                    # class | interface | enum | annotation
    modifiers: str               # space-separated: public abstract final etc.
    superclass: str              # empty string if none
    interfaces: str              # comma-separated list
    outer_class: str             # empty for top-level types
    line: int


@dataclass
class MethodRecord:
    file_path: str
    qualified_class: str
    method_name: str
    return_type: str
    parameters: str              # comma-separated "Type name" pairs
    modifiers: str
    kind: str                    # method | constructor
    line: int


@dataclass
class FieldRecord:
    file_path: str
    qualified_class: str
    field_name: str
    field_type: str
    modifiers: str
    line: int


@dataclass
class ParseErrorRecord:
    file_path: str
    error: str


@dataclass
class SymbolMap:
    packages: list[PackageRecord] = field(default_factory=list)
    types: list[TypeRecord] = field(default_factory=list)
    methods: list[MethodRecord] = field(default_factory=list)
    fields: list[FieldRecord] = field(default_factory=list)
    parse_errors: list[ParseErrorRecord] = field(default_factory=list)


# Module-level state so downstream tools can access it without re-scanning
_symbol_map: SymbolMap | None = None


# ---------------------------------------------------------------------------
# Public tool function
# ---------------------------------------------------------------------------

def scan_java_symbols(repo_dir: str, java_files: list[str]) -> dict[str, Any]:
    """
    Parse all Java source files in the repository and extract a symbol map.

    Args:
        repo_dir: Absolute path to the cloned repository root.
        java_files: List of relative file paths (from clone_repository output).

    Returns:
        dict with counts and the symbol_map (serialised as dicts).
    """
    global _symbol_map
    symbol_map = SymbolMap()

    root = Path(repo_dir)
    total = len(java_files)
    logger.info(f"Scanning {total} Java files in {repo_dir}")

    for i, rel_path in enumerate(java_files, 1):
        abs_path = root / rel_path
        if i % 100 == 0:
            logger.info(f"  Scanned {i}/{total} files…")
        _scan_file(str(abs_path), rel_path, symbol_map)

    _symbol_map = symbol_map

    result = {
        "repo_dir": repo_dir,
        "files_scanned": total,
        "files_with_errors": len(symbol_map.parse_errors),
        "type_count": len(symbol_map.types),
        "method_count": len(symbol_map.methods),
        "field_count": len(symbol_map.fields),
        "package_count": len({p.package for p in symbol_map.packages}),
    }
    logger.info(f"Scan complete: {result}")
    return result


def get_symbol_map() -> SymbolMap | None:
    """Return the last parsed SymbolMap (for use by other tools)."""
    return _symbol_map


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _scan_file(abs_path: str, rel_path: str, sym: SymbolMap) -> None:
    try:
        source = Path(abs_path).read_text(encoding="utf-8", errors="replace")
        tree = javalang.parse.parse(source)
    except Exception as e:
        sym.parse_errors.append(ParseErrorRecord(file_path=rel_path, error=str(e)[:300]))
        return

    package = tree.package.name if tree.package else ""
    sym.packages.append(PackageRecord(file_path=rel_path, package=package))

    _walk_types(rel_path, package, tree.types, outer="", sym=sym)


def _walk_types(
    rel_path: str,
    package: str,
    types,
    outer: str,
    sym: SymbolMap,
) -> None:
    """Recursively walk a list of javalang type declarations."""
    for decl in (types or []):
        kind = _type_kind(decl)
        name = decl.name
        qualified = f"{package}.{name}" if package else name
        if outer:
            qualified = f"{outer}.{name}"

        modifiers = _mods(decl)
        superclass = ""
        interfaces_str = ""

        if isinstance(decl, javalang.tree.ClassDeclaration):
            if decl.extends:
                superclass = _type_ref(decl.extends)
            if decl.implements:
                interfaces_str = ", ".join(_type_ref(i) for i in decl.implements)
        elif isinstance(decl, javalang.tree.InterfaceDeclaration):
            if decl.extends:
                interfaces_str = ", ".join(_type_ref(i) for i in decl.extends)
        elif isinstance(decl, javalang.tree.EnumDeclaration):
            if decl.implements:
                interfaces_str = ", ".join(_type_ref(i) for i in decl.implements)

        line = decl.position.line if decl.position else 0

        sym.types.append(TypeRecord(
            file_path=rel_path,
            package=package,
            name=name,
            qualified_name=qualified,
            kind=kind,
            modifiers=modifiers,
            superclass=superclass,
            interfaces=interfaces_str,
            outer_class=outer,
            line=line,
        ))

        # Methods and constructors
        for method in getattr(decl, "methods", []) or []:
            params = _params(method.parameters)
            ret = _type_ref(method.return_type) if method.return_type else "void"
            sym.methods.append(MethodRecord(
                file_path=rel_path,
                qualified_class=qualified,
                method_name=method.name,
                return_type=ret,
                parameters=params,
                modifiers=_mods(method),
                kind="method",
                line=method.position.line if method.position else 0,
            ))

        for ctor in getattr(decl, "constructors", []) or []:
            params = _params(ctor.parameters)
            sym.methods.append(MethodRecord(
                file_path=rel_path,
                qualified_class=qualified,
                method_name=ctor.name,
                return_type="",
                parameters=params,
                modifiers=_mods(ctor),
                kind="constructor",
                line=ctor.position.line if ctor.position else 0,
            ))

        # Fields (skip enum constants which have their own node type)
        for f in getattr(decl, "fields", []) or []:
            ftype = _type_ref(f.type)
            mods = _mods(f)
            for decl_fragment in f.declarators:
                sym.fields.append(FieldRecord(
                    file_path=rel_path,
                    qualified_class=qualified,
                    field_name=decl_fragment.name,
                    field_type=ftype,
                    modifiers=mods,
                    line=f.position.line if f.position else 0,
                ))

        # Recurse into nested types (inner classes etc.)
        # body contains a mix of fields/methods/nested types — filter to type declarations only.
        body = getattr(decl, "body", None) or []
        nested_types = [
            node for node in body
            if isinstance(node, (
                javalang.tree.ClassDeclaration,
                javalang.tree.InterfaceDeclaration,
                javalang.tree.EnumDeclaration,
                javalang.tree.AnnotationDeclaration,
            ))
        ]
        _walk_types(rel_path, package, nested_types, outer=qualified, sym=sym)


# ---------------------------------------------------------------------------
# javalang helpers
# ---------------------------------------------------------------------------

def _type_kind(decl) -> str:
    if isinstance(decl, javalang.tree.ClassDeclaration):
        return "class"
    if isinstance(decl, javalang.tree.InterfaceDeclaration):
        return "interface"
    if isinstance(decl, javalang.tree.EnumDeclaration):
        return "enum"
    if isinstance(decl, javalang.tree.AnnotationDeclaration):
        return "annotation"
    return "unknown"


def _mods(node) -> str:
    mods = getattr(node, "modifiers", None) or set()
    return " ".join(sorted(mods))


def _type_ref(type_node) -> str:
    """Convert a javalang type node to a string like 'List<String>' or 'int'."""
    if type_node is None:
        return ""
    if isinstance(type_node, str):
        return type_node
    if isinstance(type_node, javalang.tree.BasicType):
        name = type_node.name
        dims = "[]" * (len(type_node.dimensions) if type_node.dimensions else 0)
        return f"{name}{dims}"
    if isinstance(type_node, javalang.tree.ReferenceType):
        name = type_node.name
        args = ""
        if type_node.arguments:
            arg_strs = []
            for a in type_node.arguments:
                if a is None:
                    arg_strs.append("?")
                elif isinstance(a, javalang.tree.TypeArgument):
                    if a.type:
                        arg_strs.append(_type_ref(a.type))
                    else:
                        arg_strs.append("?")
                else:
                    arg_strs.append(str(a))
            args = f"<{', '.join(arg_strs)}>"
        dims = "[]" * (len(type_node.dimensions) if type_node.dimensions else 0)
        sub = f".{_type_ref(type_node.sub_type)}" if type_node.sub_type else ""
        return f"{name}{args}{sub}{dims}"
    return str(type_node)


def _params(parameters) -> str:
    """Render method parameters as 'Type name, Type2 name2'."""
    if not parameters:
        return ""
    parts = []
    for p in parameters:
        ptype = _type_ref(p.type)
        parts.append(f"{ptype} {p.name}")
    return ", ".join(parts)
