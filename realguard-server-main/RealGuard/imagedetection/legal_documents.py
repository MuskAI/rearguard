import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path


_VERSION_RE = re.compile(r"版本[：:]\s*(\d{4}-\d{2}-\d{2}(?:\.\d+)?)")


@dataclass(frozen=True)
class LegalDocumentIdentity:
    version: str
    sha256: str


_PINNED_IDENTITIES = {
    "terms": LegalDocumentIdentity(
        version="2026-08-07",
        sha256="619aee74677629f4f5e2c4ccbaa99c458671086de45c0a586e76c8c8c062d2c5",
    ),
    "privacy": LegalDocumentIdentity(
        version="2026-08-08.1",
        sha256="e2dd0904fbbccef7df74168ede051da7a93029f00b072d0a5f1bd41b7ebf826c",
    ),
}


def _legal_document_candidates(name):
    # Production activation validates the pinned identities before the new
    # frontend is switched live. Only inspect an explicitly staged directory;
    # reading the currently live frontend would compare two different releases.
    configured = str(os.environ.get("REALGUARD_LEGAL_DOCS_DIR") or "").strip()
    if configured:
        yield Path(configured) / f"{name}.html"
    project_root = Path(__file__).resolve().parents[2]
    yield project_root / "frontend" / "public" / "legal" / f"{name}.html"


def identity_from_file(path):
    content = Path(path).read_bytes()
    text = content.decode("utf-8")
    match = _VERSION_RE.search(text)
    if not match:
        raise RuntimeError(f"法律文档缺少版本标识: {path}")
    return LegalDocumentIdentity(
        version=match.group(1),
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _resolve_identity(name):
    pinned = _PINNED_IDENTITIES[name]
    for candidate in _legal_document_candidates(name):
        if not candidate.is_file():
            continue
        actual = identity_from_file(candidate)
        if actual != pinned:
            raise RuntimeError(
                f"{name} 法律文档与后端身份清单不一致: "
                f"expected={pinned}, actual={actual}"
            )
        return actual
    return pinned


def _assert_deployment_override(name, actual):
    configured = str(os.environ.get(name) or "").strip()
    if configured and configured != actual:
        raise RuntimeError(f"{name} 与已发布法律文档不一致")


TERMS = _resolve_identity("terms")
PRIVACY = _resolve_identity("privacy")
CONSENT_VERSION = f"{TERMS.version}+{PRIVACY.version}"

_assert_deployment_override("REALGUARD_TERMS_VERSION", TERMS.version)
_assert_deployment_override("REALGUARD_TERMS_SHA256", TERMS.sha256)
_assert_deployment_override("REALGUARD_PRIVACY_VERSION", PRIVACY.version)
_assert_deployment_override("REALGUARD_PRIVACY_SHA256", PRIVACY.sha256)
