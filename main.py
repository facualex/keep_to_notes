#!/usr/bin/env python3
"""
Google Keep → Apple Notes converter
------------------------------------
Convierte un export de Google Takeout (.zip) al formato .enex
que Apple Notes puede importar directamente.

Uso:
    python3 keep_to_enex.py takeout.zip
    python3 keep_to_enex.py takeout.zip --output mis_notas.enex
    python3 keep_to_enex.py carpeta_keep/   # carpeta ya extraída
"""

import argparse
import base64
import hashlib
import html
import mimetypes
import os
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

# ── Dependencias opcionales ──────────────────────────────────────────────────
try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

# ────────────────────────────────────────────────────────────────────────────

ENEX_HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE en-export SYSTEM "http://xml.evernote.com/pub/evernote-export3.dtd">
<en-export export-date="{export_date}" application="KeepToEnex" version="1.0">
"""

ENEX_FOOTER = "</en-export>\n"

NOTE_TEMPLATE = """  <note>
    <title>{title}</title>
    <created>{created}</created>
    <updated>{updated}</updated>
    <content><![CDATA[<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">
<en-note>{body}</en-note>
]]></content>
{tags}{resources}  </note>
"""

RESOURCE_TEMPLATE = """    <resource>
      <data encoding="base64">{data}</data>
      <mime>{mime}</mime>
      <resource-attributes>
        <file-name>{filename}</file-name>
      </resource-attributes>
    </resource>
"""


# ── Utilidades ───────────────────────────────────────────────────────────────

def now_enex() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_keep_date(text: str) -> str:
    """Intenta parsear la fecha del HTML de Keep y la convierte a formato ENEX."""
    text = text.strip()
    fmts = [
        "%b %d, %Y, %I:%M:%S\u202f%p",
        "%b %d, %Y, %I:%M:%S %p",
        "%B %d, %Y, %I:%M:%S\u202f%p",
        "%B %d, %Y, %I:%M:%S %p",
        "%b %d, %Y",
        "%B %d, %Y",
    ]
    for fmt in fmts:
        try:
            dt = datetime.strptime(text, fmt)
            return dt.strftime("%Y%m%dT%H%M%SZ")
        except ValueError:
            continue
    return now_enex()


def html_to_enml_simple(raw_html: str) -> str:
    """
    Convierte HTML de Google Keep a ENML sin BeautifulSoup.
    Conserva saltos de línea y texto plano; descarta tags no válidos en ENML.
    """
    # Quitar scripts y styles
    raw_html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw_html, flags=re.S | re.I)
    # <br> → newline placeholder
    raw_html = re.sub(r"<br\s*/?>", "\n", raw_html, flags=re.I)
    # <p>, <div> → newline
    raw_html = re.sub(r"</?(p|div)[^>]*>", "\n", raw_html, flags=re.I)
    # Quitar todos los tags restantes
    text = re.sub(r"<[^>]+>", "", raw_html)
    # Decodificar entidades HTML
    text = html.unescape(text)
    # Convertir saltos de línea a <br/>
    lines = text.split("\n")
    parts = []
    for line in lines:
        stripped = line.strip()
        if stripped:
            parts.append(escape(stripped))
        parts.append("<br/>")
    return "".join(parts).strip("<br/>")


def html_to_enml_bs4(soup_content) -> str:
    """Convierte el contenido de Keep a ENML usando BeautifulSoup."""
    parts = []

    def walk(node):
        from bs4 import NavigableString, Tag
        if isinstance(node, NavigableString):
            text = str(node)
            if text.strip():
                parts.append(escape(text))
        elif isinstance(node, Tag):
            name = node.name.lower() if node.name else ""
            if name in ("script", "style"):
                return
            if name == "br":
                parts.append("<br/>")
            elif name in ("p", "div"):
                for child in node.children:
                    walk(child)
                parts.append("<br/>")
            elif name in ("b", "strong"):
                parts.append("<b>")
                for child in node.children:
                    walk(child)
                parts.append("</b>")
            elif name in ("i", "em"):
                parts.append("<i>")
                for child in node.children:
                    walk(child)
                parts.append("</i>")
            elif name == "a":
                href = node.get("href", "")
                parts.append(f'<a href="{escape(href)}">')
                for child in node.children:
                    walk(child)
                parts.append("</a>")
            elif name == "li":
                parts.append("<br/>• ")
                for child in node.children:
                    walk(child)
            else:
                for child in node.children:
                    walk(child)

    for child in soup_content.children:
        walk(child)

    result = "".join(parts).strip()
    # Limpiar <br/> duplicados al principio/final
    result = re.sub(r"(<br/>)+", "<br/>", result)
    return result.strip("<br/>").strip()


# ── Parser principal de un archivo .html de Keep ────────────────────────────

def parse_keep_html(filepath: Path, zip_ref=None) -> dict:
    """
    Lee un archivo HTML exportado por Google Keep y extrae:
    title, created, updated, body (ENML), resources (adjuntos).
    """
    if zip_ref:
        raw = zip_ref.read(str(filepath)).decode("utf-8", errors="replace")
    else:
        raw = filepath.read_text(encoding="utf-8", errors="replace")

    note = {
        "title": filepath.stem,
        "created": now_enex(),
        "updated": now_enex(),
        "body": "",
        "tags": [],
        "resources": [],
    }

    if BS4_AVAILABLE:
        soup = BeautifulSoup(raw, "html.parser")

        # Título
        title_tag = soup.find(class_="title")
        if title_tag:
            note["title"] = title_tag.get_text(strip=True) or filepath.stem

        # Fechas
        created_tag = soup.find(class_="heading")
        if created_tag:
            note["created"] = parse_keep_date(created_tag.get_text(strip=True))
            note["updated"] = note["created"]

        # Cuerpo
        content_tag = soup.find(class_="content")
        if content_tag:
            note["body"] = html_to_enml_bs4(content_tag)
        else:
            # Fallback: todo el body
            body_tag = soup.find("body")
            if body_tag:
                note["body"] = html_to_enml_bs4(body_tag)

        # Checklists
        checklist_items = soup.find_all(class_=re.compile(r"list-item"))
        if checklist_items and not content_tag:
            items_html = []
            for item in checklist_items:
                checked = "checked" in item.get("class", [])
                text = escape(item.get_text(strip=True))
                mark = "✓ " if checked else "☐ "
                items_html.append(f"{mark}{text}")
            note["body"] = "<br/>".join(items_html)

        # Etiquetas (labels)
        # Keep las exporta como <span class="label-name">NombreEtiqueta</span>
        # o dentro de un div con class="labels"
        label_tags = soup.find_all(class_=re.compile(r"label"))
        seen = set()
        for tag in label_tags:
            label_text = tag.get_text(strip=True)
            if label_text and label_text not in seen:
                seen.add(label_text)
                note["tags"].append(label_text)

    else:
        # Sin BeautifulSoup: regex básico
        title_m = re.search(r'class="title"[^>]*>(.*?)<', raw, re.S)
        if title_m:
            note["title"] = html.unescape(title_m.group(1).strip()) or filepath.stem

        date_m = re.search(r'class="heading"[^>]*>(.*?)<', raw, re.S)
        if date_m:
            note["created"] = parse_keep_date(html.unescape(date_m.group(1).strip()))
            note["updated"] = note["created"]

        content_m = re.search(r'class="content"[^>]*>(.*?)</[^>]+>', raw, re.S)
        if content_m:
            note["body"] = html_to_enml_simple(content_m.group(1))
        else:
            body_m = re.search(r"<body[^>]*>(.*?)</body>", raw, re.S | re.I)
            if body_m:
                note["body"] = html_to_enml_simple(body_m.group(1))

        # Etiquetas — regex fallback
        label_matches = re.findall(r'class="[^"]*label[^"]*"[^>]*>(.*?)<', raw, re.S)
        seen = set()
        for lbl in label_matches:
            lbl = html.unescape(lbl.strip())
            if lbl and lbl not in seen:
                seen.add(lbl)
                note["tags"].append(lbl)

    # Limpiar título
    note["title"] = note["title"][:255] or "Sin título"

    return note


# ── Manejo de adjuntos ───────────────────────────────────────────────────────

def load_resource(path: str, zip_ref=None) -> dict | None:
    """Lee un archivo adjunto y lo devuelve listo para ENEX."""
    try:
        if zip_ref:
            data = zip_ref.read(path)
        else:
            data = Path(path).read_bytes()
    except Exception:
        return None

    mime, _ = mimetypes.guess_type(path)
    mime = mime or "application/octet-stream"
    filename = Path(path).name
    b64 = base64.b64encode(data).decode("ascii")
    return {"data": b64, "mime": mime, "filename": filename}


# ── Construcción del ENEX ────────────────────────────────────────────────────

def build_resource_xml(res: dict) -> str:
    return RESOURCE_TEMPLATE.format(
        data=res["data"],
        mime=res["mime"],
        filename=escape(res["filename"]),
    )


def build_note_xml(note: dict) -> str:
    resources_xml = "".join(build_resource_xml(r) for r in note["resources"])
    tags_xml = "".join(f"    <tag>{escape(t)}</tag>\n" for t in note.get("tags", []))
    return NOTE_TEMPLATE.format(
        title=escape(note["title"]),
        created=note["created"],
        updated=note["updated"],
        body=note["body"] or "",
        tags=tags_xml,
        resources=resources_xml,
    )


# ── Lógica principal ─────────────────────────────────────────────────────────

def collect_keep_files_from_zip(zip_path: Path):
    """Genera (html_path, zip_ref) para cada .html de Keep dentro del ZIP."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        # Buscar carpeta Keep (puede estar en Takeout/Keep/ o directamente)
        keep_htmls = [
            n for n in names
            if n.lower().endswith(".html")
            and "keep" in n.lower()
            and not Path(n).name.startswith(".")
        ]
        if not keep_htmls:
            # Intentar cualquier HTML dentro del zip
            keep_htmls = [
                n for n in names
                if n.lower().endswith(".html") and not Path(n).name.startswith(".")
            ]
        yield from [(Path(p), zf, names) for p in keep_htmls]


def process_zip(zip_path: Path, output_path: Path):
    notes = []
    skipped = 0

    with zipfile.ZipFile(zip_path, "r") as zf:
        all_names = set(zf.namelist())

        keep_htmls = [
            n for n in all_names
            if n.lower().endswith(".html")
            and "keep" in n.lower()
            and not Path(n).name.startswith(".")
        ]
        if not keep_htmls:
            keep_htmls = [
                n for n in all_names
                if n.lower().endswith(".html") and not Path(n).name.startswith(".")
            ]

        if not keep_htmls:
            print("⚠️  No se encontraron archivos .html de Google Keep en el ZIP.")
            print("   Asegúrate de haber exportado solo 'Keep' en Google Takeout.")
            sys.exit(1)

        print(f"📋 {len(keep_htmls)} notas encontradas...")

        for html_path_str in keep_htmls:
            html_path = Path(html_path_str)
            try:
                note = parse_keep_html(html_path, zip_ref=zf)
                # Buscar adjuntos con el mismo nombre base
                base = html_path.stem
                parent = str(html_path.parent)
                for candidate in all_names:
                    if (
                        candidate != html_path_str
                        and Path(candidate).stem == base
                        and not candidate.lower().endswith(".html")
                        and Path(candidate).parent == html_path.parent
                    ):
                        res = load_resource(candidate, zip_ref=zf)
                        if res:
                            note["resources"].append(res)
                notes.append(note)
            except Exception as e:
                print(f"  ⚠️  Saltando {html_path.name}: {e}")
                skipped += 1

    write_enex(notes, output_path)
    print(f"✅ {len(notes)} notas convertidas → {output_path}")
    if skipped:
        print(f"   ({skipped} archivos omitidos por errores)")


def process_folder(folder: Path, output_path: Path):
    notes = []
    skipped = 0
    html_files = list(folder.rglob("*.html"))

    if not html_files:
        print(f"⚠️  No se encontraron archivos .html en {folder}")
        sys.exit(1)

    print(f"📋 {len(html_files)} notas encontradas...")

    for html_file in html_files:
        if html_file.name.startswith("."):
            continue
        try:
            note = parse_keep_html(html_file)
            # Adjuntos con el mismo nombre base
            for sibling in html_file.parent.iterdir():
                if sibling.stem == html_file.stem and sibling.suffix.lower() != ".html":
                    res = load_resource(str(sibling))
                    if res:
                        note["resources"].append(res)
            notes.append(note)
        except Exception as e:
            print(f"  ⚠️  Saltando {html_file.name}: {e}")
            skipped += 1

    write_enex(notes, output_path)
    print(f"✅ {len(notes)} notas convertidas → {output_path}")
    if skipped:
        print(f"   ({skipped} archivos omitidos por errores)")


def write_enex(notes: list, output_path: Path):
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ENEX_HEADER.format(export_date=now_enex()))
        for note in notes:
            f.write(build_note_xml(note))
        f.write(ENEX_FOOTER)


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Convierte Google Keep Takeout (.zip o carpeta) a Apple Notes (.enex)"
    )
    parser.add_argument("input", help="Archivo .zip de Google Takeout o carpeta extraída")
    parser.add_argument(
        "--output", "-o", default=None,
        help="Nombre del archivo de salida (default: keep_export.enex)"
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"❌ No se encontró: {input_path}")
        sys.exit(1)

    output_path = Path(args.output) if args.output else Path("keep_export.enex")

    if not BS4_AVAILABLE:
        print("ℹ️  BeautifulSoup no está instalado. Usando parser básico.")
        print("   Para mejores resultados: pip install beautifulsoup4")
        print()

    if input_path.is_file() and input_path.suffix.lower() == ".zip":
        print(f"📦 Procesando ZIP: {input_path}")
        process_zip(input_path, output_path)
    elif input_path.is_dir():
        print(f"📁 Procesando carpeta: {input_path}")
        process_folder(input_path, output_path)
    else:
        print("❌ El input debe ser un .zip de Google Takeout o una carpeta con archivos .html")
        sys.exit(1)

    print()
    print("📥 Para importar en Apple Notes:")
    print("   Mac:         Notes → File → Import to Notes → selecciona keep_export.enex")
    print("   iPhone/iPad: Abre el .enex → compartir → Notes")


if __name__ == "__main__":
    main()
