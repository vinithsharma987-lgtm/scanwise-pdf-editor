# ScanWise PDF Editor — prototype

A free/open-source Streamlit prototype for PDF text extraction, OCR-assisted line editing, review, and export.

## Features

- Upload PDF (up to 25 MB / 30 pages in this prototype)
- Reads embedded PDF text and runs OCR on scanned pages
- English and Tamil OCR (Tesseract language packs required)
- Edit detected lines
- Approve individual changes
- Original / edited-preview comparison
- Export a separate edited PDF only after the review confirmation
- Original upload is never overwritten

## Run locally

### 1. Install Tesseract and language packs

Ubuntu/Debian:
```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng tesseract-ocr-tam fonts-noto-core fonts-noto-extra
```

Windows:
1. Install Tesseract OCR for Windows.
2. Install the English and Tamil language data (`eng`, `tam`).
3. If Tesseract is not in PATH, set `pytesseract.pytesseract.tesseract_cmd` in `app.py` to the installed `tesseract.exe` path.

### 2. Create a virtual environment and install dependencies

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Start the website

```bash
streamlit run app.py
```

Open the local URL printed in the terminal (usually http://localhost:8501).

## Free online hosting: Streamlit Community Cloud

1. Create a GitHub repository and upload `app.py`, `requirements.txt`, `packages.txt`, and this README.
2. Open https://share.streamlit.io/ and sign in with GitHub.
3. Choose the repository, branch, and `app.py` as the main file.
4. Deploy. Streamlit installs Python dependencies from `requirements.txt` and Debian packages from `packages.txt`.
5. Test English and Tamil OCR with non-sensitive sample PDFs.

Free hosting availability, resource limits, sleep behavior, and terms can change. A public deployment can be accessible to anyone with the link unless you configure access controls. Do not upload sensitive documents to a public demo.

## Architecture

```text
Browser
  └── Streamlit UI
      ├── PyMuPDF: PDF parsing, rendering, text extraction, export
      ├── Tesseract OCR: English/Tamil recognition on scanned pages
      ├── Pillow: side-by-side visual preview
      └── Session state: edits + per-line approvals
              └── Export gate: explicit confirmation required
                  └── Separate edited PDF download
```

## Important limitations

- A scan stores pixels rather than original font metadata. The app estimates text size and uses fallback fonts; exact font matching cannot be guaranteed.
- The prototype edits whole detected lines, not individual characters.
- White-background redaction is best for ordinary white paper; textured backgrounds, signatures, seals, and complex layouts need more sophisticated image inpainting.
- Longer replacement text may overflow. Inspect the preview and exported PDF before using it.
- For Tamil, ensure Noto Tamil font and the Tesseract `tam` language pack are installed.
- This prototype is not a certified document-authentication or document-forensics tool.
