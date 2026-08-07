export const LEGAL_CONSENT = {
  version: "2026-08-07+2026-08-08",
  termsSha256: "619aee74677629f4f5e2c4ccbaa99c458671086de45c0a586e76c8c8c062d2c5",
  privacySha256: "f5e9e4ba233857667176949017d2f36964d47e0595e4b1d36d2c80254c3adc38",
} as const;

export function appendUploadConsent(body: FormData) {
  body.append("upload_consent", "1");
  body.append("consent_version", LEGAL_CONSENT.version);
  body.append("terms_sha256", LEGAL_CONSENT.termsSha256);
  body.append("privacy_sha256", LEGAL_CONSENT.privacySha256);
}
