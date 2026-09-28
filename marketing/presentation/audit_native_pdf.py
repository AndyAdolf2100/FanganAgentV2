"""Audit an actual PowerPoint-rendered PDF against the exported slide outline.

This script never renders PPTX itself. Supply a PDF exported by PowerPoint;
Chromium PDFs cannot substitute for the native application check.
Run: uv run --with pymupdf --with pillow python audit_native_pdf.py PDF OUTLINE
"""
import argparse
import json
import re
import unicodedata
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw


def normalized(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def expected_texts(page):
    values = [page.get(key, "") for key in ("section", "title", "subtitle")]
    values.extend(item.get(key, "") for item in page.get("items", []) for key in ("value", "label", "text"))
    if page.get('chart'):
        chart=page['chart'];values.extend(chart['categories']);values.append('单位：'+chart['unit'])
        values.append(chart['source_note'])
    return [value for value in values if value]


def audit(pdf_path, outline_path, output):
    output.mkdir(parents=True, exist_ok=True)
    outline = json.loads(outline_path.read_text())
    document = pymupdf.open(pdf_path)
    issues, thumbnails = [], []
    if len(document) != len(outline["pages"]):
        issues.append({"rule": "page_count", "actual": len(document), "expected": len(outline["pages"])})
    cropped = pymupdf.open()
    for index, page in enumerate(document):
        images = page.get_image_info()
        if not images:
            issues.append({"rule": "missing_slide_background", "page": index + 1})
            continue
        frame = pymupdf.Rect(max(images, key=lambda item: pymupdf.Rect(item["bbox"]).get_area())["bbox"])
        tolerance = frame + (-1, -1, 1, 1)
        text = normalized(page.get_text())
        if index < len(outline["pages"]):
            for expected in expected_texts(outline["pages"][index]):
                if normalized(expected) not in text:
                    issues.append({"rule": "missing_visible_text", "page": index + 1, "text": expected})
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if not tolerance.contains(pymupdf.Rect(span["bbox"])):
                        issues.append({"rule": "outside_slide", "page": index + 1, "text": span["text"], "bbox": span["bbox"]})
                    if "\ufffd" in span["text"]:
                        issues.append({"rule": "replacement_glyph", "page": index + 1, "text": span["text"]})
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6), clip=frame)
        pixmap.save(output / f"native-{index+1}.png")
        thumbnail = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        thumbnail.thumbnail((400, 225))
        thumbnails.append((index + 1, thumbnail))
        # Remove printer margins without changing native application content.
        cropped_page = cropped.new_page(width=frame.width, height=frame.height)
        cropped_page.show_pdf_page(cropped_page.rect, document, index, clip=frame)
    if len(cropped):
        cropped.save(output / "presentation.pdf", garbage=4, deflate=True)
    for start in range(0, len(thumbnails), 8):
        montage = Image.new("RGB", (1600, 510), "#deded8")
        draw = ImageDraw.Draw(montage)
        for position, (number, thumbnail) in enumerate(thumbnails[start:start+8]):
            x, y = position % 4 * 400, position // 4 * 255
            montage.paste(thumbnail, (x, y))
            draw.text((x+10, y+232), str(number), fill="#173b2b")
        montage.save(output / f"native-montage-{start//8+1}.jpg", quality=92)
    report = {"passed": not issues, "pages": len(document), "issues": issues,
              "source_sha256": outline["source_sha256"],
              "input_pdf": pdf_path.name,
              "method": "Externally supplied PowerPoint PDF: every visible brief string, text bounds, glyphs and page count",
              "limitations": ["Caller must supply a real PowerPoint export; application provenance is not inferred from PDF bytes",
                              "Geometric and text checks do not certify aesthetics or the truth of manuscript claims"]}
    (output / "native-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("outline", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.pdf, args.outline, args.output or args.pdf.parent)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["passed"] else 1)
