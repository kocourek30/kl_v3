from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.http import Http404
from django.shortcuts import render


BASE_DIR = Path(settings.BASE_DIR)
DOC_ROOT = BASE_DIR / "docs"
ROOT_README = BASE_DIR / "README.md"


@dataclass(frozen=True)
class DocEntry:
    rel_path: str
    title: str
    category: str

    @property
    def url_path(self) -> str:
        return self.rel_path.removesuffix(".md")

    @property
    def display_path(self) -> str:
        return self.rel_path.replace("\\", "/")


def _discover_docs() -> list[DocEntry]:
    entries: list[DocEntry] = []
    sources = []

    if ROOT_README.exists():
        sources.append(ROOT_README)
    if DOC_ROOT.exists():
        sources.extend(sorted(DOC_ROOT.rglob("*.md")))

    for source in sources:
        rel = source.relative_to(BASE_DIR).as_posix()
        category = "Project"
        if rel.startswith("docs/"):
            remainder = rel[len("docs/") :]
            category = remainder.split("/", 1)[0].replace(".md", "").title()
            if "/" not in remainder:
                category = "Docs"
            if remainder.startswith("apps/"):
                category = "Apps"
            elif remainder.startswith("licencovani/"):
                category = "Licencovani"
            elif remainder.startswith("deployment") or remainder.startswith("DEPLOY"):
                category = "Deployment"
        title = _read_title(source) or source.stem.replace("_", " ").replace("-", " ").title()
        entries.append(DocEntry(rel_path=rel, title=title, category=category))

    return sorted(entries, key=lambda item: (item.category, item.title.lower()))


def _resolve_doc(doc_path: str) -> Path:
    candidate = (BASE_DIR / f"{doc_path}.md").resolve()
    if candidate == ROOT_README.resolve():
        return candidate
    if DOC_ROOT.exists():
        try:
            candidate.relative_to(DOC_ROOT.resolve())
            return candidate
        except ValueError:
            pass
    raise Http404("Documentation page not found.")


def _read_title(path: Path) -> str | None:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#"):
                return line.lstrip("# ").strip()
    except OSError:
        return None
    return None


def _slugify_doc_path(rel_path: str) -> str:
    return rel_path.removesuffix(".md")


def _markdown_to_html(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    in_list = False
    in_code = False
    code_lines: list[str] = []

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    def emit_paragraph(buffer: list[str]) -> None:
        if not buffer:
            return
        paragraph = " ".join(part.strip() for part in buffer if part.strip())
        if paragraph:
            out.append(f"<p>{_inline_format(paragraph)}</p>")

    paragraph_buffer: list[str] = []

    for raw in lines:
        line = raw.rstrip("\n")
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_code:
                out.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
                code_lines = []
                in_code = False
            else:
                close_list()
                emit_paragraph(paragraph_buffer)
                paragraph_buffer = []
                in_code = True
            continue

        if in_code:
            code_lines.append(line)
            continue

        if not stripped:
            close_list()
            emit_paragraph(paragraph_buffer)
            paragraph_buffer = []
            continue

        heading_match = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        if heading_match:
            close_list()
            emit_paragraph(paragraph_buffer)
            paragraph_buffer = []
            level = len(heading_match.group(1))
            text_value = _inline_format(heading_match.group(2).strip())
            out.append(f"<h{level}>{text_value}</h{level}>")
            continue

        if stripped in {"---", "***"}:
            close_list()
            emit_paragraph(paragraph_buffer)
            paragraph_buffer = []
            out.append("<hr />")
            continue

        if stripped.startswith(("- ", "* ")):
            emit_paragraph(paragraph_buffer)
            paragraph_buffer = []
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline_format(stripped[2:].strip())}</li>")
            continue

        close_list()
        paragraph_buffer.append(stripped)

    if in_code:
        out.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")

    close_list()
    emit_paragraph(paragraph_buffer)
    return "\n".join(out)


def _inline_format(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^\*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', escaped)
    return escaped


def index(request):
    docs = []
    for entry in _discover_docs():
        docs.append(
            {
                "title": entry.title,
                "category": entry.category,
                "display_path": entry.display_path,
                "url_path": entry.url_path,
            }
        )

    categories = sorted({doc["category"] for doc in docs})
    return render(
        request,
        "knowledge_base/index.html",
        {
            "docs": docs,
            "categories": categories,
            "doc_count": len(docs),
        },
    )


def document(request, doc_path: str):
    source = _resolve_doc(doc_path)
    raw = source.read_text(encoding="utf-8")
    title = _read_title(source) or source.stem.replace("_", " ").replace("-", " ").title()
    html_body = _markdown_to_html(raw)
    rel_path = source.relative_to(BASE_DIR).as_posix()

    return render(
        request,
        "knowledge_base/document.html",
        {
            "title": title,
            "html_body": html_body,
            "rel_path": rel_path,
        },
    )

