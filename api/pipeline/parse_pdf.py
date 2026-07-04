"""Docling 기반 PDF 파싱 (스펙 4.1 Stage 1: TableFormer ACCURATE + EasyOCR ko/en)."""
import json
from pathlib import Path


def parse_pdf(src: Path, out: Path) -> None:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        EasyOcrOptions,
        PdfPipelineOptions,
        TableFormerMode,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc import PictureItem, TableItem, TextItem

    opts = PdfPipelineOptions()
    opts.do_table_structure = True
    opts.table_structure_options.mode = TableFormerMode.ACCURATE
    opts.do_ocr = True
    opts.ocr_options = EasyOcrOptions(lang=["ko", "en"])
    opts.generate_picture_images = True
    opts.images_scale = 2.0
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    doc = converter.convert(str(src)).document

    (out / "document.md").write_text(doc.export_to_markdown(), encoding="utf-8")
    (out / "document.json").write_text(
        json.dumps(doc.export_to_dict(), ensure_ascii=False), encoding="utf-8")
    (out / "tables").mkdir(exist_ok=True)
    (out / "figures").mkdir(exist_ok=True)

    chunks: list[dict] = []
    n_table = n_fig = 0
    for item, _level in doc.iterate_items():
        page = item.prov[0].page_no if getattr(item, "prov", None) else None
        cid = f"c{len(chunks) + 1:03d}"
        if isinstance(item, TableItem):
            n_table += 1
            ref = f"tables/table_{n_table:03d}.json"
            df = item.export_to_dataframe(doc=doc)
            payload = {
                "table_title": "",
                "columns": [str(c) for c in df.columns],
                "rows": df.astype(object).where(df.notna(), None).values.tolist(),
            }
            (out / ref).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            chunks.append({"id": cid, "type": "table", "page": page, "ref": ref})
        elif isinstance(item, PictureItem):
            img = item.get_image(doc)
            if img is None:
                continue
            n_fig += 1
            ref = f"figures/fig_{n_fig:03d}.png"
            img.save(out / ref)
            chunks.append({"id": cid, "type": "picture", "page": page, "ref": ref})
        elif isinstance(item, TextItem) and item.text.strip():
            chunks.append({"id": cid, "type": "text", "page": page, "text": item.text})
    (out / "chunks.json").write_text(
        json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
