import type {
  DetectResult,
  ForensicReport,
  ImageAgentExpert,
  ImageAgentResult,
  ProvenanceReport,
  VideoAgentResult,
} from "./api";

export type ImageAnalysisMode = "fast" | "swarm";

export type AgentOutcome =
  | {
      kind: "image";
      id: string;
      result: ImageAgentResult;
      file?: File;
      previewUrl?: string;
      forensics?: ForensicReport;
      provenance?: ProvenanceReport;
      analysisMode?: ImageAnalysisMode;
    }
  | {
      kind: "video";
      id: string;
      result: VideoAgentResult;
      file?: File;
      previewUrl?: string;
    }
  | {
      kind: "evidence";
      id: string;
      result: DetectResult;
      file?: File;
      previewUrl?: string;
      forensics?: ForensicReport;
      provenance?: ProvenanceReport;
      analysisMode?: ImageAnalysisMode;
      fallbackFromImage?: boolean;
    };

export type HistoryOrigin = "image" | "video" | "evidence";

export interface AgentHistoryEntry {
  key: string;
  origin: HistoryOrigin;
  recordId: string;
  title: string;
  typeLabel: string;
  verdictLabel: string;
  score: number;
  createdAt: string;
  thumbnail?: string | null;
}

export interface AgentProgress {
  title: string;
  detail: string;
  percent: number;
  stage: "validate" | "dispatch" | "evidence" | "report";
  experts?: ImageAgentExpert[];
  fallback?: boolean;
  analysisMode?: ImageAnalysisMode;
}

export interface PendingFile {
  name: string;
  size: number;
  typeLabel: string;
  previewUrl?: string;
  analysisMode?: ImageAnalysisMode;
}
