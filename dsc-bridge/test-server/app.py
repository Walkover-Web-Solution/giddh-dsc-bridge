#!/usr/bin/env python3
"""
Giddh DSC Bridge — Standalone Full-Flow Test Server
=====================================================
A ~self-contained Flask app that exercises the COMPLETE DSC signing chain on
any machine, WITHOUT the Giddh backend:

    browser page  ──▶  window.GiddhBridge (extension)  ──▶  native host  ──▶  token
         │                                                                        │
         └── POST /dsc/prepare (hash) ◀── dsc_signing engine ──▶ /dsc/finish (embed CMS)

It reuses the portable ``dsc_signing`` package with the provided in-memory ports
(no DB, no cloud storage). A successful run produces a real PAdES-signed PDF you
can download and open in **Adobe Acrobat** to confirm the signature panel.

Run:
    pip install -r requirements.txt
    python app.py            # http://127.0.0.1:5055

The bundle you hand to a partner must include BOTH this folder (dsc-bridge/) and
the engine package (dsc_signing/). This server adds the repo root to sys.path so
`import dsc_signing` works when run from inside the repo.
"""
from __future__ import annotations

import base64
import io
import os
import sys

from flask import Flask, jsonify, request, send_file, send_from_directory

# Make the portable engine importable whether run from inside the repo or from a
# handoff bundle laid out as  <bundle>/dsc-bridge/test-server/app.py  +  <bundle>/dsc_signing/.
_HERE = os.path.dirname(os.path.abspath(__file__))
# Locate the `dsc_signing` engine regardless of layout:
#   • handoff bundle:  <bundle>/test-server/app.py      + <bundle>/dsc_signing/     (one level up)
#   • source repo:     dsc-bridge/test-server/app.py    + <root>/dsc_signing/       (two levels up)
for _cand in (os.path.join(_HERE, ".."), os.path.join(_HERE, "..", "..")):
    _cand = os.path.abspath(_cand)
    if os.path.isdir(os.path.join(_cand, "dsc_signing")) and _cand not in sys.path:
        sys.path.insert(0, _cand)
        break

from dsc_signing import DscConfig, DscError, DscSigningService, SignerContext  # noqa: E402
from dsc_signing.ports import (  # noqa: E402
    InMemoryStateStore,
    InMemoryWorkingPdfStore,
    NullAuditSink,
    ProvenanceSink,
)

# ── A base PDF to sign (generated once, in-memory) ───────────────────────────

def _build_base_pdf(_doc_id: str) -> bytes:
    """One-page PDF with a labelled area where the visible signature lands."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    c.setFont("Helvetica-Bold", 18)
    c.drawString(72, height - 100, "Giddh DSC Bridge — Test Document")
    c.setFont("Helvetica", 11)
    c.drawString(72, height - 130, "This PDF was signed by a hardware DSC token via the bridge.")
    c.drawString(72, height - 148, "Open it in Adobe Acrobat to view the signature panel.")
    # Draw the box that matches SIG_FIELD_BOX below so the appearance is visible.
    x1, y1, x2, y2 = SIG_FIELD_BOX
    c.setStrokeColorRGB(0.55, 0.62, 0.69)
    c.rect(x1, y1, x2 - x1, y2 - y1)
    c.setFont("Helvetica", 8)
    c.drawString(x1 + 4, y2 - 12, "Signature")
    c.showPage()
    c.save()
    return buf.getvalue()


# Visible signature location (PDF points, bottom-left origin), page 0.
SIG_FIELD_BOX = (72, 120, 342, 240)


# ── Provenance sink that just remembers the last signature (for display) ─────

class MemoryProvenance(ProvenanceSink):
    def __init__(self):
        self.last = None

    def record(self, ctx, result):
        self.last = {
            "signer": ctx.signer_name,
            "field": result.pdf_field_name,
            "cert_cn": result.cert_info.subject_cn,
            "pades_level": result.pades_level,
        }


# ── Wire the service with in-memory ports ────────────────────────────────────

working_store = InMemoryWorkingPdfStore(base_builder=_build_base_pdf)
state_store = InMemoryStateStore()
provenance = MemoryProvenance()

config = DscConfig(
    md_algorithm="sha256",
    verify_signer_name=False,   # test tokens carry arbitrary CNs; don't block
    allowed_issuers=[],         # accept any CA for testing
    location="Test Server",
    bridge="giddh_bridge",
)

service = DscSigningService(
    config=config,
    working_store=working_store,
    state_store=state_store,
    provenance=provenance,
    audit=NullAuditSink(),
)

DOC_ID = "testdoc"
SIGNER = SignerContext(
    doc_id=DOC_ID,
    signer_id="signer-1",
    signer_name="Test Signer",
    signer_email="test@example.com",
    field_name_prefix="DSC",
)

app = Flask(__name__, static_folder=None)


@app.get("/")
def index():
    return send_from_directory(_HERE, "index.html")


@app.post("/dsc/prepare")
def dsc_prepare():
    body = request.get_json(force=True, silent=True) or {}
    cert_b64 = body.get("certificate")
    if not cert_b64:
        return jsonify({"success": False, "error": "certificate is required"}), 400
    handwritten = None
    if body.get("signature_image"):
        try:
            raw = body["signature_image"].split(",", 1)[-1]
            handwritten = base64.b64decode(raw)
        except Exception:
            handwritten = None
    try:
        res = service.prepare(
            SIGNER,
            base64.b64decode(cert_b64),
            field_box=SIG_FIELD_BOX,
            on_page=0,
            handwritten_png=handwritten,
            chain=body.get("chain"),
        )
        return jsonify(res.to_dict())
    except DscError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:  # pragma: no cover - surface engine errors verbatim
        return jsonify({"success": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.post("/dsc/finish")
def dsc_finish():
    body = request.get_json(force=True, silent=True) or {}
    nonce = body.get("nonce")
    signature = body.get("signature")
    if not nonce or not signature:
        return jsonify({"success": False, "error": "nonce and signature are required"}), 400
    try:
        res = service.finish(SIGNER, nonce, signature, chain=body.get("chain"))
        out = res.to_dict()
        out["download"] = "/download"
        out["provenance"] = provenance.last
        return jsonify(out)
    except DscError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:  # pragma: no cover
        return jsonify({"success": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.get("/download")
def download():
    pdf = working_store.get_working_pdf(DOC_ID)
    return send_file(
        io.BytesIO(pdf),
        mimetype="application/pdf",
        as_attachment=True,
        download_name="dsc-signed-test.pdf",
    )


@app.post("/reset")
def reset():
    """Rebuild a fresh unsigned base PDF so you can test again."""
    working_store._pdfs.pop(DOC_ID, None)  # type: ignore[attr-defined]
    return jsonify({"success": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5055"))
    print(f"DSC test server on http://127.0.0.1:{port}  (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=port, debug=False)
