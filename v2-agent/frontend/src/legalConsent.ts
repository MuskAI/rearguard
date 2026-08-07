export const LEGAL_CONSENT = {
  version: "2026-08-07+2026-08-08.1",
  termsSha256: "619aee74677629f4f5e2c4ccbaa99c458671086de45c0a586e76c8c8c062d2c5",
  privacySha256: "e2dd0904fbbccef7df74168ede051da7a93029f00b072d0a5f1bd41b7ebf826c",
} as const;

export function appendUploadConsent(body: FormData) {
  body.append("upload_consent", "1");
  body.append("consent_version", LEGAL_CONSENT.version);
  body.append("terms_sha256", LEGAL_CONSENT.termsSha256);
  body.append("privacy_sha256", LEGAL_CONSENT.privacySha256);
}
