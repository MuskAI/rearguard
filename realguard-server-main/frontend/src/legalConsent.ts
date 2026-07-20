export const LEGAL_CONSENT = {
  version: "2026-07-15+2026-07-20",
  termsSha256: "09707ba3b915db9904cc6f8b4951b5c9bbfff7e768fd237c04eedf90fef89ff3",
  privacySha256: "5c505aaf82abe1af5cac83fef81c60ec66e89a76377110fba6348ed0567d8935",
} as const;

export function appendUploadConsent(body: FormData) {
  body.append("upload_consent", "1");
  body.append("consent_version", LEGAL_CONSENT.version);
  body.append("terms_sha256", LEGAL_CONSENT.termsSha256);
  body.append("privacy_sha256", LEGAL_CONSENT.privacySha256);
}
