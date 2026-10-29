import io
import os
import re
import shutil
import tempfile
from dataclasses import dataclass

import fitz  # PyMuPDF
import pytesseract
import streamlit as st
from PIL import Image, ImageDraw, ImageFont
from pytesseract import Output

APP_NAME = "ScanWise PDF Editor"
MAX_UPLOAD_MB = 25
DPI = 180

st.set_page_config(page_title=APP_NAME, page_icon="📄", layout="wide")

st.markdown("""
<style>
.block-container {padding-top: 1.5rem; max-width: 1400px;}
.small-note {color: #64748b; font-size: .9rem;}
div[data-testid="stMetric"] {border: 1px solid #e2e8f0; padding: .8rem; border-radius: 12px;}
</style>
""", unsafe_allow_html=True)

st.title("📄 ScanWise")
st.caption("Free prototype • Scanned PDF OCR • Review edits before export")
st.info("Your original upload is never overwritten. This prototype processes files in the app session; do not upload confidential documents to a public demo.")

def pil_font(size=16, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    bold_candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansTamil-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in (bold_candidates if bold else candidates) + candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, max(8, int(size)))
            except Exception:
                pass
    return ImageFont.load_default()

def page_to_image(page, dpi=DPI):
    pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
    return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")

def group_ocr_lines(image, lang):
    data = pytesseract.image_to_data(image, lang=lang, output_type=Output.DICT, config="--psm 3")
    lines = {}
    n = len(data["text"])
    for i in range(n):
        txt = (data["text"][i] or "").strip()
        try:
            conf = float(data["conf"][i])
        except Exception:
            conf = -1
        if not txt or conf < 0:
            continue
        key = (data["page_num"][i], data["block_num"][i], data["par_num"][i], data["line_num"][i])
        x, y, w, h = [int(data[k][i]) for k in ("left", "top", "width", "height")]
        if key not in lines:
            lines[key] = {"words": [], "box": [x, y, x + w, y + h], "conf": []}
        lines[key]["words"].append((x, txt))
        b = lines[key]["box"]
        b[0], b[1], b[2], b[3] = min(b[0], x), min(b[1], y), max(b[2], x+w), max(b[3], y+h)
        lines[key]["conf"].append(conf)
    result = []
    for value in lines.values():
        text = " ".join(t for _, t in sorted(value["words"]))
        result.append({"text": text, "box": value["box"], "confidence": round(sum(value["conf"])/max(1,len(value["conf"])), 1)})
    result.sort(key=lambda item: (item["box"][1], item["box"][0]))
    return result

def extract_page_lines(page, image, lang):
    # Prefer actual PDF text when available; fall back to OCR for scanned/image pages.
    words = page.get_text("words")
    if len(words) >= 3:
        blocks = page.get_text("dict")["blocks"]
        lines = []
        for block in blocks:
            if "lines" not in block:
                continue
            for line in block["lines"]:
                spans = line.get("spans", [])
                text = "".join(s.get("text", "") for s in spans).strip()
                if not text:
                    continue
                bbox = line.get("bbox")
                if not bbox:
                    continue
                sizes = [s.get("size", 11) for s in spans if s.get("text", "").strip()]
                scale = DPI / 72
                lines.append({
                    "text": text,
                    "pdf_box": list(map(float, bbox)),
                    # Preview coordinates are pixels; PDF text boxes are points.
                    "box": [int(v * scale) for v in bbox],
                    "font_size": float(sum(sizes)/len(sizes)) if sizes else 11.0,
                    "source": "pdf",
                    "confidence": 100.0,
                })
        if lines:
            return lines
    result = group_ocr_lines(image, lang)
    scale = 72 / DPI
    for line in result:
        x0, y0, x1, y1 = line["box"]
        line["pdf_box"] = [x0*scale, y0*scale, x1*scale, y1*scale]
        line["font_size"] = max(8, (y1-y0)*scale*0.78)
        line["source"] = "ocr"
    return result

def render_edit_preview(original_image, lines, edits, approved_ids):
    image = original_image.copy()
    draw = ImageDraw.Draw(image)
    for idx, line in enumerate(lines):
        if idx not in approved_ids:
            continue
        old = line["text"]
        new = edits.get(idx, old)
        if new == old:
            continue
        x0, y0, x1, y1 = line["box"]
        pad = max(2, int((y1-y0)*0.12))
        # White out the line's text region. This is a visual preview, not a perfect
        # reconstruction of textured/coloured backgrounds.
        draw.rectangle((max(0,x0-1), max(0,y0-1), min(image.width,x1+2), min(image.height,y1+pad+1)), fill="white")
        font = pil_font(max(10, int((y1-y0)*0.76)))
        draw.text((x0, y0), new, fill="black", font=font)
    return image

def build_edited_pdf(pdf_bytes, page_records, edits_by_page, approved_by_page):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    for page_index, page in enumerate(doc):
        lines = page_records[page_index]["lines"]
        edits = edits_by_page.get(page_index, {})
        approved = approved_by_page.get(page_index, set())
        for idx in sorted(approved):
            if idx >= len(lines):
                continue
            line = lines[idx]
            old = line["text"]
            new = edits.get(idx, old)
            if not new.strip() or new == old:
                continue
            rect_data = line.get("pdf_box")
            if not rect_data:
                continue
            rect = fitz.Rect(rect_data)
            # Add a small margin to remove anti-aliased edges, while keeping nearby lines safe.
            margin = min(1.3, max(0.25, rect.height * 0.04))
            redaction = fitz.Rect(rect.x0-margin, rect.y0-margin, rect.x1+margin, rect.y1+margin)
            # White fill is suitable for ordinary white paper. Non-white backgrounds may need manual cleanup.
            page.add_redact_annot(redaction, fill=(1, 1, 1), cross_out=False)
        page.apply_redactions(images=0, graphics=0)
        for idx in sorted(approved):
            if idx >= len(lines):
                continue
            line = lines[idx]
            old = line["text"]
            new = edits.get(idx, old)
            if not new.strip() or new == old:
                continue
            rect_data = line.get("pdf_box")
            if not rect_data:
                continue
            rect = fitz.Rect(rect_data)
            fontsize = max(6, min(36, float(line.get("font_size", 11))))
            fontname = "helv"
            # Keep inserted text within the original line's rectangle where possible.
            # For Tamil, embed a Unicode font if installed; otherwise use a fallback.
            tamil_font = "/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf"
            if re.search(r"[\u0B80-\u0BFF]", new) and os.path.exists(tamil_font):
                fontname = "tamilfont"
                page.insert_font(fontname=fontname, fontfile=tamil_font)
            try:
                page.insert_textbox(
                    fitz.Rect(rect.x0, rect.y0-0.5, max(rect.x1+40, rect.x0+60), rect.y1+max(2, fontsize*0.45)),
                    new, fontsize=fontsize, fontname=fontname, color=(0,0,0),
                    overlay=True, lineheight=1.0
                )
            except Exception:
                page.insert_text((rect.x0, rect.y1-1), new, fontsize=fontsize, fontname="helv", color=(0,0,0), overlay=True)
    output = io.BytesIO()
    doc.save(output, garbage=4, deflate=True)
    doc.close()
    return output.getvalue()

with st.sidebar:
    st.header("Document settings")
    language_choice = st.selectbox("OCR language", ["English + Tamil", "English only", "Tamil only"], index=0)
    language = {"English + Tamil": "eng+tam", "English only": "eng", "Tamil only": "tam"}[language_choice]
    st.caption("OCR needs Tesseract language data installed by the host.")
    st.divider()
    st.markdown("**Workflow**")
    st.markdown("1. Upload a PDF\n2. Analyse each page\n3. Edit detected lines\n4. Preview and approve changes\n5. Download the approved copy")

uploaded = st.file_uploader("Upload a PDF document", type=["pdf"])
if uploaded:
    if uploaded.size > MAX_UPLOAD_MB * 1024 * 1024:
        st.error(f"File is too large. The prototype limit is {MAX_UPLOAD_MB} MB.")
        st.stop()
    pdf_bytes = uploaded.getvalue()
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if doc.page_count > 30:
            st.warning("For this prototype, documents are limited to 30 pages.")
            st.stop()
        st.success(f"Loaded **{uploaded.name}** — {doc.page_count} page(s). The original upload remains unchanged.")
        if st.button("Analyse document", type="primary"):
            records = []
            with st.spinner("Reading PDF text and running OCR where needed..."):
                for i, page in enumerate(doc):
                    image = page_to_image(page)
                    lines = extract_page_lines(page, image, language)
                    records.append({"image": image, "lines": lines, "width": image.width, "height": image.height})
            st.session_state["scanwise_pdf"] = pdf_bytes
            st.session_state["scanwise_name"] = uploaded.name
            st.session_state["scanwise_records"] = records
            st.session_state["scanwise_edits"] = {}
            st.session_state["scanwise_approved"] = {}
            st.session_state["scanwise_analyzed"] = True
        doc.close()
    except Exception as exc:
        st.error(f"Could not open this PDF: {exc}")
        st.stop()

if st.session_state.get("scanwise_analyzed"):
    records = st.session_state["scanwise_records"]
    pdf_bytes = st.session_state["scanwise_pdf"]
    st.divider()
    st.subheader("Edit and review")
    page_index = st.selectbox("Select page", options=list(range(len(records))), format_func=lambda n: f"Page {n+1}")
    record = records[page_index]
    lines = record["lines"]
    if not lines:
        st.warning("No text was detected on this page. Try a clearer scan or check that the required OCR language is installed.")
    else:
        st.caption(f"{len(lines)} text line(s) detected. OCR text and font matching are best-effort; please verify every change.")
        edits = st.session_state["scanwise_edits"].setdefault(page_index, {})
        approved = st.session_state["scanwise_approved"].setdefault(page_index, set())
        with st.form(f"edit_form_{page_index}"):
            st.markdown("**Edit detected lines**")
            st.caption("Change only the lines you want to replace. Tick Approve for each line you want included in the export.")
            for idx, line in enumerate(lines):
                label = f"Line {idx+1} · {line.get('source','ocr').upper()} · confidence {line.get('confidence',0)}%"
                with st.container(border=True):
                    st.caption(label)
                    current = edits.get(idx, line["text"])
                    new_value = st.text_input("Text", value=current, key=f"text_{page_index}_{idx}", label_visibility="collapsed")
                    is_approved = st.checkbox("Approve this edit", value=(idx in approved), key=f"approve_{page_index}_{idx}")
                    st.caption(f"Detected: {line['text'][:180]}")
                    edits[idx] = new_value
                    if is_approved and new_value != line["text"]:
                        approved.add(idx)
                    else:
                        approved.discard(idx)
            submitted = st.form_submit_button("Update preview", type="primary")
        if submitted:
            st.session_state["scanwise_edits"][page_index] = edits
            st.session_state["scanwise_approved"][page_index] = approved
            st.rerun()

        edits = st.session_state["scanwise_edits"].get(page_index, {})
        approved = st.session_state["scanwise_approved"].get(page_index, set())
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Original page**")
            st.image(record["image"], use_container_width=True)
        with col2:
            st.markdown("**Preview of approved edits**")
            preview = render_edit_preview(record["image"], lines, edits, approved)
            st.image(preview, use_container_width=True)
            st.caption("Preview uses a simplified white background and a fallback font. The final PDF may differ slightly.")
        changed = sum(1 for i, line in enumerate(lines) if edits.get(i, line["text"]) != line["text"] and i in approved)
        st.metric("Approved changes on this page", changed)

    st.divider()
    st.subheader("Export")
    all_edits = st.session_state["scanwise_edits"]
    all_approved = st.session_state["scanwise_approved"]
    total_approved = sum(len(v) for v in all_approved.values())
    st.write(f"**{total_approved}** line edit(s) approved across the document.")
    confirm = st.checkbox("I reviewed the preview and want to export only the approved edits.", key="confirm_export")
    if st.button("Prepare approved PDF", type="primary", disabled=not confirm):
        with st.spinner("Applying approved edits to a separate copy..."):
            try:
                result = build_edited_pdf(pdf_bytes, records, all_edits, all_approved)
                st.session_state["scanwise_export"] = result
                st.session_state["scanwise_export_ready"] = True
            except Exception as exc:
                st.error(f"Could not prepare the edited PDF: {exc}")
                st.session_state["scanwise_export_ready"] = False
    if st.session_state.get("scanwise_export_ready"):
        out_name = os.path.splitext(st.session_state.get("scanwise_name", "document.pdf"))[0] + "_edited.pdf"
        st.download_button("Download approved PDF", data=st.session_state["scanwise_export"], file_name=out_name, mime="application/pdf")
        st.success("Edited copy prepared. Your original PDF has not been overwritten.")
    with st.expander("Prototype limitations and privacy"):
        st.markdown("""
- Font matching is approximate. Exact font recovery from a scan is generally impossible because the scan contains pixels, not original font metadata.
- The first version edits detected text lines, not individual glyphs. Longer replacement text may overflow or shift nearby content.
- White redaction works best on white paper. Coloured, patterned, stamped, or textured backgrounds need more advanced inpainting.
- OCR quality depends on scan clarity and the installed English/Tamil language packs.
- This app does not store uploads in a database, but a hosted provider may have logs or temporary infrastructure storage. Avoid sensitive IDs, bank records, or confidential files on a public deployment.
- Use this tool for legitimate corrections to documents you are authorised to edit. Keep originals and clearly label modified copies where appropriate.
""")
else:
    st.markdown("### What you can do")
    c1, c2, c3 = st.columns(3)
    c1.metric("OCR languages", "English + Tamil")
    c2.metric("Review gate", "Required")
    c3.metric("Original file", "Preserved")
    st.markdown("Upload a PDF above and select **Analyse document** to begin.")
