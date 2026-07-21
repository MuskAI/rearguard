const state = { file: null, objectUrl: null, payload: null, mediaMode: "image", pipelineTrace: null, pipelineStageId: null, videoFrames: [] };

const $ = (id) => document.getElementById(id);
const fileInput = $("file-input");
const dropzone = $("dropzone");
const preview = $("preview");
const videoPreview = $("video-preview");
const overlay = $("overlay");

function score(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${(number * 100).toFixed(1)}%` : "—";
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  })[character]);
}

function sizeLabel(bytes) {
  if (!bytes) return "";
  return bytes > 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`;
}

function typeLabel(type) {
  return ({ text: "文字水印", logo: "Logo 水印", unknown: "未知水印", none: "未检出" })[type] || "待判读";
}

function verdictLabel(verdict) {
  return ({ yes: "是", no: "否", inconclusive: "不确定" })[verdict] || "不确定";
}

function durationLabel(value) {
  const milliseconds = Number(value || 0);
  if (!Number.isFinite(milliseconds)) return "—";
  return milliseconds >= 1000 ? `${(milliseconds / 1000).toFixed(2)} s` : `${Math.round(milliseconds)} ms`;
}

function pipelineStatusLabel(status) {
  return ({ success: "完成", hit: "命中", clean: "无命中", warning: "需复核", error: "失败", skipped: "跳过" })[status] || "未运行";
}

function setStatus(text, mode = "idle") {
  const pill = $("status-pill");
  pill.textContent = text;
  pill.className = `status-pill ${mode}`;
}

function setMediaMode(mode) {
  const modeChanged = state.mediaMode !== mode;
  state.mediaMode = mode;
  document.querySelectorAll("[data-media-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.mediaMode === mode);
  });
  const isVideo = mode === "video";
  fileInput.accept = isVideo ? "video/mp4,video/quicktime,video/webm,video/x-matroska,.mp4,.mov,.webm,.mkv" : "image/*";
  $("sample-control").hidden = !isVideo;
  $("drop-title").textContent = isVideo ? "拖拽视频到这里" : "拖拽图片到这里";
  $("drop-subtitle").textContent = isVideo ? "或点击选择 · MP4 / MOV / WEBM" : "或点击选择 · JPG / PNG / WEBP";
  if (!state.file) {
    $("empty-stage-title").textContent = isVideo ? "等待视频证据" : "等待图像证据";
    $("empty-stage-subtitle").textContent = isVideo ? "选择视频后，可以预览并回看抽取帧" : "选择图片后，定位框会叠加在原图上";
  } else if (modeChanged) {
    // A selected image must not accidentally be submitted to the video endpoint, or vice versa.
    clearAll();
  }
}

function chooseFile(file) {
  const isVideo = state.mediaMode === "video";
  if (!file || (isVideo ? !file.type.startsWith("video/") && !/\.(mp4|mov|webm|mkv|avi|m4v)$/i.test(file.name) : !file.type.startsWith("image/"))) return;
  state.file = file;
  state.payload = null;
  if (state.objectUrl) URL.revokeObjectURL(state.objectUrl);
  state.objectUrl = URL.createObjectURL(file);
  preview.hidden = isVideo;
  videoPreview.hidden = !isVideo;
  if (isVideo) {
    videoPreview.src = state.objectUrl;
    videoPreview.load();
  } else {
    preview.src = state.objectUrl;
  }
  $("empty-stage").hidden = true;
  $("stage-caption").hidden = false;
  $("stage-caption").textContent = `${file.name} · ${sizeLabel(file.size)}`;
  $("drop-title").textContent = file.name;
  $("drop-subtitle").textContent = "文件已载入，可开始分析";
  $("file-meta").textContent = `${file.type || (isVideo ? "video" : "image")}  /  ${sizeLabel(file.size)}`;
  $("analyze-button").disabled = false;
  $("analyze-button").innerHTML = isVideo ? "开始抽帧分析 <span>→</span>" : "开始分析 <span>→</span>";
  $("frame-review").hidden = true;
  setStatus("待分析", "idle");
}

function clearAll() {
  state.file = null;
  state.payload = null;
  if (state.objectUrl) URL.revokeObjectURL(state.objectUrl);
  state.objectUrl = null;
  state.pipelineTrace = null;
  state.pipelineStageId = null;
  state.videoFrames = [];
  fileInput.value = "";
  preview.hidden = true;
  preview.removeAttribute("src");
  videoPreview.hidden = true;
  videoPreview.removeAttribute("src");
  videoPreview.load();
  $("empty-stage").hidden = false;
  $("empty-stage-title").textContent = state.mediaMode === "video" ? "等待视频证据" : "等待图像证据";
  $("empty-stage-subtitle").textContent = state.mediaMode === "video" ? "选择视频后，可以预览并回看抽取帧" : "选择图片后，定位框会叠加在原图上";
  $("stage-caption").hidden = true;
  overlay.innerHTML = "";
  $("frame-review").hidden = true;
  $("frame-grid").innerHTML = "";
  $("frame-summary").textContent = "—";
  $("drop-title").textContent = state.mediaMode === "video" ? "拖拽视频到这里" : "拖拽图片到这里";
  $("drop-subtitle").textContent = state.mediaMode === "video" ? "或点击选择 · MP4 / MOV / WEBM" : "或点击选择 · JPG / PNG / WEBP";
  $("file-meta").textContent = "尚未选择文件";
  $("analyze-button").disabled = true;
  $("analyze-button").innerHTML = state.mediaMode === "video" ? "开始抽帧分析 <span>→</span>" : "开始分析 <span>→</span>";
  $("verdict-main").textContent = "—";
  $("verdict-detail").textContent = "上传文件后显示 AI 生成水印判断";
  $("verdict-block").className = "verdict-block";
  $("text-signal").className = "text-signal";
  $("text-signal-tag").textContent = "等待 OCR";
  $("text-signal-title").textContent = "尚未形成文字语义证据";
  $("text-signal-detail").textContent = "检测到文字后，会判断其是否包含 AI 生成或平台水印语义。";
  $("confidence").textContent = "—";
  $("hit-count").textContent = "—";
  $("platform").textContent = "—";
  $("engine-time").textContent = "—";
  $("evidence-list").innerHTML = '<div class="empty-list">分析完成后，这里会列出每一处候选的证据。</div>';
  $("raw-json").textContent = "尚未产生结果";
  resetPipeline();
  setStatus("待开始", "idle");
}

function renderBoxes(hits, target = overlay, className = "bbox") {
  target.innerHTML = "";
  hits.forEach((hit, index) => {
    const box = hit.bbox || {};
    const element = document.createElement("div");
    element.className = `${className} ${hit.type || "unknown"}`;
    element.style.left = `${Number(box.x || 0) * 100}%`;
    element.style.top = `${Number(box.y || 0) * 100}%`;
    element.style.width = `${Number(box.w || 0) * 100}%`;
    element.style.height = `${Number(box.h || 0) * 100}%`;
    element.innerHTML = `<span>${String(index + 1).padStart(2, "0")} · ${typeLabel(hit.type)}</span>`;
    target.appendChild(element);
  });
}

function relevantHits(hits) {
  return hits.filter((hit) => !(hit.type === "text" && hit.textAnalysis && hit.textAnalysis.verdict === "not_supported"));
}

function renderEvidence(hits) {
  const list = $("evidence-list");
  if (!hits.length) {
    list.innerHTML = '<div class="empty-list positive">没有形成可用的显式水印候选。</div>';
    return;
  }
  list.innerHTML = hits.map((hit, index) => {
    const reasons = (hit.reason || []).map((reason) => `<li>${escapeHtml(reason)}</li>`).join("");
    return `<article class="evidence-card ${hit.type || "unknown"}">
      <div class="evidence-top"><span class="evidence-index">${String(index + 1).padStart(2, "0")}</span><strong>${typeLabel(hit.type)}</strong><b>${score(hit.confidence)}</b></div>
      <div class="evidence-source">${escapeHtml(hit.sourcePlatform || "来源未确认")}${hit.text ? ` · “${escapeHtml(hit.text)}”` : ""}</div>
      <div class="evidence-facts"><span>OCR ${score(hit.ocrConfidence)}</span><span>检索 ${Number(hit.retrievalSimilarity || 0).toFixed(3)}</span><span>${hit.position || "位置未知"}</span></div>
      <ul>${reasons}</ul>
    </article>`;
  }).join("");
}

function renderTextSignal(textSignal, prefix = "") {
  const signalLabels = {
    supports_ai_generation: "支持 AI 生成",
    inconclusive: "证据不充分",
    not_supported: "不支持 AI 判断",
    unavailable: "无 OCR 证据",
  };
  const signalVerdict = textSignal.verdict || "unavailable";
  $("text-signal").className = `text-signal ${signalVerdict}`;
  $("text-signal-tag").textContent = signalLabels[signalVerdict] || "待判读";
  $("text-signal-title").textContent = textSignal.text ? `${prefix}“${textSignal.text}”` : "尚未形成文字语义证据";
  const keywords = Array.isArray(textSignal.matchedKeywords) && textSignal.matchedKeywords.length ? `匹配：${textSignal.matchedKeywords.join("、")}。` : "";
  $("text-signal-detail").textContent = `${textSignal.interpretation || "没有可用文字语义解释。"} ${keywords} ${textSignal.caveat || ""}`.trim();
}

function resetPipeline(message = "完成一次分析后显示各阶段结果") {
  $("trace-scope").textContent = "等待任务";
  $("trace-total").textContent = "— ms";
  $("pipeline-stage-list").innerHTML = `<div class="pipeline-placeholder">${escapeHtml(message)}</div>`;
  $("pipeline-waterfall").innerHTML = '<div class="pipeline-placeholder">尚无耗时数据</div>';
  $("stage-sequence").textContent = "STAGE —";
  $("stage-detail-title").textContent = "等待流水线结果";
  $("stage-detail-status").textContent = "未运行";
  $("stage-detail-status").className = "stage-status idle";
  $("stage-detail-summary").textContent = "选择一个阶段后查看输入、输出、阈值与拒绝原因。";
  $("stage-detail-body").innerHTML = "";
  $("stage-json").textContent = "尚未产生结果";
}

function setPipelineLoading(scope) {
  state.pipelineTrace = null;
  state.pipelineStageId = null;
  $("trace-scope").textContent = scope;
  $("trace-total").textContent = "计时中";
  $("pipeline-stage-list").innerHTML = '<div class="pipeline-placeholder active"><span class="trace-pulse"></span>上游流水线正在执行</div>';
  $("pipeline-waterfall").innerHTML = '<div class="pipeline-placeholder">等待真实阶段耗时</div>';
  $("stage-detail-title").textContent = "任务执行中";
  $("stage-detail-summary").textContent = "结果返回后将显示每个阶段的真实输入、输出和耗时。";
}

function factItem(label, value, tone = "") {
  return `<div class="trace-fact ${tone}"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value ?? "—")}</strong></div>`;
}

function boxText(box) {
  if (!box || typeof box !== "object") return "—";
  return `x ${Number(box.x || 0).toFixed(3)} · y ${Number(box.y || 0).toFixed(3)} · w ${Number(box.w || 0).toFixed(3)} · h ${Number(box.h || 0).toFixed(3)}`;
}

function candidateRows(items, kind) {
  if (!Array.isArray(items) || !items.length) return '<div class="trace-empty">该阶段没有产生候选。</div>';
  return `<div class="trace-result-list">${items.map((item, index) => `<div class="trace-result-row">
    <span class="trace-row-index">${String(index + 1).padStart(2, "0")}</span>
    <div><strong>${escapeHtml(item.label || item.provider || `${kind} 候选`)}</strong><small>${escapeHtml(boxText(item.bbox))}</small></div>
    <b>${score(item.confidence)}</b>
  </div>`).join("")}</div>`;
}

function retrievalResultHtml(item) {
  const similarity = Number(item.similarity || 0);
  const threshold = Number(item.threshold || 0);
  const margin = Number(item.margin || 0);
  const minimumMargin = Number(item.minimumMargin || 0);
  const matches = Array.isArray(item.topMatches) ? item.topMatches : [];
  const topMatches = matches.length ? `<div class="retrieval-ranking">${matches.map((match, index) => {
    const value = Math.max(0, Math.min(1, Number(match.similarity || 0)));
    return `<div class="rank-row"><span>${index + 1}</span><strong>${escapeHtml(match.platform || "未知")}</strong><div class="rank-track"><i style="width:${value * 100}%"></i></div><b>${value.toFixed(4)}</b></div>`;
  }).join("")}</div>` : '<div class="trace-empty compact">没有返回 Top-K 近邻。</div>';
  return `<article class="retrieval-result ${item.accepted ? "accepted" : "rejected"}">
    <div class="retrieval-result-head"><strong>候选 ${item.candidate || "—"} · ${escapeHtml(item.sourcePlatform || item.candidatePlatform || "平台未确认")}</strong><span>${item.accepted ? "通过" : "拒绝"}</span></div>
    <div class="threshold-plot"><div class="threshold-fill" style="width:${Math.max(0, Math.min(1, similarity)) * 100}%"></div><i style="left:${Math.max(0, Math.min(1, threshold)) * 100}%"></i></div>
    <div class="threshold-labels"><span>相似度 ${similarity.toFixed(4)}</span><span>阈值 ${threshold.toFixed(4)}</span></div>
    <div class="trace-fact-grid compact">${factItem("平台间距", margin.toFixed(4), margin >= minimumMargin ? "good" : "warn")}${factItem("最小间距", minimumMargin.toFixed(4))}${factItem("决策原因", item.reason || "—", item.accepted ? "good" : "warn")}${factItem("参考样本", item.referenceId || "—")}</div>
    ${topMatches}
  </article>`;
}

function stageBodyHtml(stage) {
  const details = stage.details || {};
  if (stage.id === "decode") {
    const input = details.input || {};
    const encoded = details.encodedSize || {};
    const display = details.displaySize || {};
    return `<div class="trace-fact-grid">${factItem("文件", input.filename || "—")}${factItem("大小", sizeLabel(input.bytes || 0) || "—")}${factItem("编码尺寸", `${encoded.width || 0}×${encoded.height || 0}`)}${factItem("显示尺寸", `${display.width || 0}×${display.height || 0}`)}${factItem("EXIF 方向", details.sourceOrientation || 1)}${factItem("标准化", durationLabel(details.normalizeMs))}</div>`;
  }
  if (stage.id === "metadata") {
    const report = details.report || {};
    const signals = Array.isArray(report.signals) ? report.signals : [];
    return `<div class="trace-fact-grid">${factItem("AI 来源", report.isAiGenerated === true ? "是" : report.isAiGenerated === false ? "否" : "未知", report.isAiGenerated ? "warn" : "")}${factItem("平台", report.platform || "未确认")}${factItem("置信等级", report.confidence || "—")}${factItem("来源类型", report.aiSourceKind || "—")}</div>${signals.length ? `<div class="signal-list">${signals.map((signal) => `<div><strong>${escapeHtml(signal.name || "信号")}</strong><span>${escapeHtml(signal.detail || "")}</span><b>${escapeHtml(signal.confidence || "")}</b></div>`).join("")}</div>` : '<div class="trace-empty">没有读取到来源元数据。</div>'}`;
  }
  if (stage.id === "registry") return candidateRows(details.hits, "注册表");
  if (stage.id === "yolo") {
    const runtime = details.runtime || {};
    return `<div class="trace-fact-grid">${factItem("模型", runtime.model || "—")}${factItem("设备", runtime.gpu || runtime.device || "—")}${factItem("模型耗时", durationLabel(runtime.elapsedMs))}${factItem("往返耗时", durationLabel(runtime.roundTripMs))}</div>${candidateRows(details.candidates, "YOLO")}`;
  }
  if (stage.id === "ocr") {
    const results = Array.isArray(details.results) ? details.results : [];
    if (!results.length) return '<div class="trace-empty">没有候选区域，因此 OCR 未运行。</div>';
    return `<div class="ocr-result-list">${results.map((item) => `<article><div><strong>候选 ${item.candidate} · ${escapeHtml(item.text || "未识别到文字")}</strong><span>${durationLabel(item.elapsedMs)}</span></div><p>OCR ${score(item.confidence)} · ${escapeHtml((item.analysis || {}).verdict || "unavailable")}</p>${(item.items || []).map((part) => `<small>${escapeHtml(part.text)} · ${score(part.confidence)}</small>`).join("")}</article>`).join("")}</div>`;
  }
  if (stage.id === "retrieval") {
    const results = Array.isArray(details.results) ? details.results : [];
    return `<div class="trace-fact-grid slim">${factItem("后端", details.backend || "—")}${factItem("向量模型", details.model || "—")}${factItem("参考数量", details.galleryCount ?? "—")}</div>${results.length ? results.map(retrievalResultHtml).join("") : '<div class="trace-empty">没有候选区域，因此向量检索未运行。</div>'}`;
  }
  if (stage.id === "fusion") {
    return `<div class="trace-fact-grid">${factItem("定位候选", details.candidateCount ?? 0)}${factItem("注册表候选", details.registryCount ?? 0)}${factItem("融合证据", Array.isArray(details.hits) ? details.hits.length : 0)}${factItem("总耗时", durationLabel((details.timings || {}).totalMs))}</div><div class="fusion-rule"><span>当前规则</span><p>${escapeHtml(details.rule || "—")}</p></div>${candidateRows(details.hits, "融合")}`;
  }
  if (stage.id === "verdict") {
    const verdict = details.verdict || {};
    return `<div class="trace-fact-grid">${factItem("判断", verdictLabel(verdict.verdict), verdict.verdict === "yes" ? "warn" : "good")}${factItem("置信度", score(verdict.confidence))}${factItem("来源平台", details.sourcePlatform || "未确认")}${factItem("相关证据", verdict.relevantHitCount ?? 0)}</div><div class="verdict-rationale">${escapeHtml(verdict.reason || "尚未形成解释")}</div>`;
  }
  return '<div class="trace-empty">没有可展示的结构化结果。</div>';
}

function renderStageDetail(stage, index) {
  if (!stage) return;
  state.pipelineStageId = stage.id;
  $("stage-sequence").textContent = `STAGE ${String(index + 1).padStart(2, "0")}`;
  $("stage-detail-title").textContent = stage.label || stage.id;
  $("stage-detail-status").textContent = pipelineStatusLabel(stage.status);
  $("stage-detail-status").className = `stage-status ${stage.status || "idle"}`;
  $("stage-detail-summary").textContent = stage.summary || "—";
  $("stage-detail-body").innerHTML = stageBodyHtml(stage);
  $("stage-json").textContent = JSON.stringify(stage, null, 2);
  document.querySelectorAll(".pipeline-stage").forEach((button) => {
    const selected = button.dataset.stageId === stage.id;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-selected", selected ? "true" : "false");
  });
}

function renderPipeline(trace, scope = "当前图片") {
  const stages = Array.isArray(trace?.stages) ? trace.stages : [];
  if (!stages.length) {
    resetPipeline("当前响应没有流水线 trace");
    return;
  }
  state.pipelineTrace = trace;
  $("trace-scope").textContent = scope;
  $("trace-total").textContent = durationLabel(trace.totalElapsedMs);
  $("pipeline-stage-list").innerHTML = stages.map((stage, index) => `<button class="pipeline-stage ${stage.status || "idle"}" type="button" role="tab" data-stage-id="${escapeHtml(stage.id)}" aria-selected="false">
    <span class="stage-number">${String(index + 1).padStart(2, "0")}</span>
    <i></i>
    <strong>${escapeHtml(stage.label)}</strong>
    <small>${durationLabel(stage.elapsedMs)}</small>
  </button>`).join("");
  const maxElapsed = Math.max(1, ...stages.map((stage) => Number(stage.elapsedMs || 0)));
  $("pipeline-waterfall").innerHTML = stages.map((stage) => `<button class="waterfall-row" type="button" data-stage-id="${escapeHtml(stage.id)}">
    <span>${escapeHtml(stage.label)}</span><div><i class="${stage.status || "idle"}" style="width:${Math.max(2, Number(stage.elapsedMs || 0) / maxElapsed * 100)}%"></i></div><b>${durationLabel(stage.elapsedMs)}</b>
  </button>`).join("");
  document.querySelectorAll("[data-stage-id]").forEach((button) => button.addEventListener("click", () => {
    const index = stages.findIndex((stage) => stage.id === button.dataset.stageId);
    if (index >= 0) renderStageDetail(stages[index], index);
  }));
  const existingIndex = stages.findIndex((stage) => stage.id === state.pipelineStageId);
  const interestingIndex = stages.findIndex((stage) => stage.status === "warning" || stage.status === "error");
  const selectedIndex = existingIndex >= 0 ? existingIndex : interestingIndex >= 0 ? interestingIndex : stages.length - 1;
  renderStageDetail(stages[selectedIndex], selectedIndex);
}

function renderResult(apiPayload) {
  state.payload = apiPayload;
  const result = apiPayload.result || {};
  if (result.mediaType === "video") return renderVideoResult(apiPayload);
  const explicit = result.explicitWatermark || {};
  const hits = Array.isArray(explicit.hits) ? explicit.hits : [];
  const aiVerdict = explicit.aiWatermarkVerdict || {};
  const visibleHits = relevantHits(hits);
  const textSignal = explicit.aiGenerationTextSignal || {};
  const verdict = aiVerdict.verdict || "inconclusive";
  $("verdict-main").textContent = verdictLabel(verdict);
  $("verdict-detail").textContent = aiVerdict.reason || (verdict === "yes" ? (explicit.sourcePlatform || "命中 AI 水印证据") : "当前证据不足");
  $("confidence").textContent = score(aiVerdict.confidence);
  $("hit-count").textContent = String(visibleHits.length);
  $("platform").textContent = explicit.sourcePlatform || "未确认";
  $("engine-time").textContent = `${result.elapsedMs || "—"} ms · OCR + FAISS 检索`;
  $("verdict-block").className = `verdict-block ${verdict}`;
  renderTextSignal(textSignal);
  setStatus(verdict === "yes" ? "发现 AI 水印" : verdict === "no" ? "无 AI 水印" : "需要复核", verdict === "yes" ? "found" : verdict === "no" ? "clean" : "working");
  renderBoxes(visibleHits);
  renderEvidence(visibleHits);
  renderPipeline(result.pipelineTrace || {}, "当前图片");
  $("frame-review").hidden = true;
  $("raw-json").textContent = JSON.stringify(apiPayload, null, 2);
}

function frameVerdictClass(verdict) {
  return verdict === "yes" ? "found" : verdict === "no" ? "clean" : "review";
}

function renderVideoFrames(frames) {
  state.videoFrames = frames;
  const grid = $("frame-grid");
  grid.innerHTML = frames.map((frame, frameIndex) => {
    const explicit = frame.explicitWatermark || {};
    const jimeng = frame.jimengEvidence || {};
    const isJimeng = jimeng.matched === true || frame.sourcePlatform === "即梦AI";
    const hits = relevantHits(Array.isArray(explicit.hits) ? explicit.hits : []);
    const boxHtml = hits.map((hit, index) => {
      const box = hit.bbox || {};
      const hitIsJimeng = hit.sourcePlatform === "即梦AI" || (
        hit.registryMatched === true && (hit.providerHint === "jimeng" || hit.providerHint === "jimeng_pill")
      );
      return `<div class="frame-bbox ${hitIsJimeng ? "jimeng" : hit.type || "unknown"}" style="left:${Number(box.x || 0) * 100}%;top:${Number(box.y || 0) * 100}%;width:${Number(box.w || 0) * 100}%;height:${Number(box.h || 0) * 100}%"><span>${index + 1}</span></div>`;
    }).join("");
    const registry = Array.isArray(jimeng.registryEntries) && jimeng.registryEntries.length ? `平台注册表：${jimeng.registryEntries.map(escapeHtml).join("、")}` : "";
    const ocr = Array.isArray(jimeng.ocrTexts) && jimeng.ocrTexts.length ? `OCR：${jimeng.ocrTexts.map((text) => `“${escapeHtml(text)}”`).join("、")}` : "";
    const retrieval = Array.isArray(jimeng.retrievalScores) && jimeng.retrievalScores.length ? `图库相似度：${jimeng.retrievalScores.map((value) => Number(value).toFixed(3)).join("、")}` : "";
    const evidence = [registry, ocr, retrieval].filter(Boolean).join(" · ");
    return `<article class="frame-card ${frameVerdictClass(frame.verdict)}${isJimeng ? " jimeng" : ""}" data-frame-index="${frameIndex}" tabindex="0">
      <div class="frame-thumb"><img src="${frame.preview}" alt="${escapeHtml(frame.timestamp || `第 ${frameIndex + 1} 帧`)}" />${boxHtml}</div>
      <div class="frame-card-meta"><strong>${escapeHtml(frame.timestamp || "未知时间")}</strong><span>${verdictLabel(frame.verdict)} · ${score(frame.confidence)}</span></div>
      <div class="frame-card-source">${isJimeng ? "即梦AI" : escapeHtml(frame.sourcePlatform || (frame.error ? "帧检测失败" : "未确认来源"))}</div>
      ${isJimeng ? `<div class="frame-platform-evidence">即梦AI证据 · ${escapeHtml(evidence || "平台归因待复核")}</div>` : ""}
    </article>`;
  }).join("");
  grid.querySelectorAll("[data-frame-index]").forEach((card) => {
    const selectFrame = () => {
      const frame = frames[Number(card.dataset.frameIndex)];
      grid.querySelectorAll("[data-frame-index]").forEach((item) => item.classList.toggle("active", item === card));
      renderPipeline(frame?.pipelineTrace || {}, `视频帧 ${frame?.timestamp || "—"}`);
    };
    card.addEventListener("click", selectFrame);
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectFrame(); }
    });
  });
}

function renderVideoResult(apiPayload) {
  const result = apiPayload.result || {};
  const aggregate = result.aiWatermarkVerdict || {};
  const frames = Array.isArray(result.frames) ? result.frames : [];
  const bestFrame = frames.find((frame) => frame.verdict === "yes")
    || frames.find((frame) => frame.jimengEvidence && frame.jimengEvidence.matched)
    || frames.find((frame) => frame.explicitWatermark && frame.explicitWatermark.aiGenerationTextSignal);
  const verdict = aggregate.verdict || "inconclusive";
  const video = result.video || {};
  $("verdict-main").textContent = verdictLabel(verdict);
  $("verdict-detail").textContent = aggregate.reason || "视频抽帧结果已汇总";
  $("confidence").textContent = score(aggregate.confidence);
  $("hit-count").textContent = `${aggregate.positiveFrames || 0}/${frames.length}`;
  $("platform").textContent = (bestFrame && bestFrame.sourcePlatform) || "未确认";
  $("engine-time").textContent = `${result.elapsedMs || "—"} ms · ${frames.length} 帧并行复核`;
  $("verdict-block").className = `verdict-block ${verdict}`;
  const textSignal = bestFrame?.explicitWatermark?.aiGenerationTextSignal || {};
  renderTextSignal(textSignal, "最佳帧文字：");
  setStatus(verdict === "yes" ? "发现 AI 水印" : verdict === "no" ? "无 AI 水印" : "需要复核", verdict === "yes" ? "found" : verdict === "no" ? "clean" : "working");
  overlay.innerHTML = "";
  $("frame-review").hidden = false;
  const jimengFrameCount = frames.filter((frame) => frame.jimengEvidence && frame.jimengEvidence.matched).length;
  $("frame-summary").textContent = `${video.width || "—"}×${video.height || "—"} · ${video.fps || "—"} fps · ${frames.length} / ${video.requestedSampleCount || frames.length} 帧${jimengFrameCount ? ` · 红框标记 ${jimengFrameCount} 帧` : " · 未命中即梦AI"}`;
  renderVideoFrames(frames);
  renderPipeline(result.pipelineTrace || bestFrame?.pipelineTrace || {}, `视频帧 ${bestFrame?.timestamp || "—"}`);
  renderEvidence([]);
  $("raw-json").textContent = JSON.stringify(apiPayload, null, 2);
}

async function analyze() {
  if (!state.file) return;
  const button = $("analyze-button");
  const isVideo = state.mediaMode === "video";
  button.disabled = true;
  button.innerHTML = isVideo ? "抽帧分析中 <span class=\"spinner\"></span>" : "分析中 <span class=\"spinner\"></span>";
  setStatus(isVideo ? "正在抽帧" : "正在分析", "working");
  setPipelineLoading(isVideo ? "视频抽帧任务" : "图片检测任务");
  const form = new FormData();
  form.append("file", state.file);
  if (isVideo) form.append("sample_count", $("sample-count").value || "8");
  try {
    const endpoint = isVideo ? "/api/analyze-video" : "/api/analyze";
    const response = await fetch(endpoint, { method: "POST", body: form });
    const payload = await response.json();
    if (!response.ok || payload.status !== "ok") throw new Error(payload.message || "检测失败");
    renderResult(payload);
  } catch (error) {
    setStatus("调用失败", "error");
    $("verdict-main").textContent = "调用失败";
    $("verdict-detail").textContent = error.message || "请稍后重试";
    $("evidence-list").innerHTML = `<div class="empty-list error-text">${escapeHtml(error.message || "检测服务不可用")}</div>`;
    resetPipeline(error.message || "流水线调用失败");
  } finally {
    button.disabled = false;
    button.innerHTML = isVideo ? "开始抽帧分析 <span>→</span>" : "开始分析 <span>→</span>";
  }
}

fileInput.addEventListener("change", () => chooseFile(fileInput.files[0]));
$("analyze-button").addEventListener("click", analyze);
$("clear-button").addEventListener("click", clearAll);
document.querySelectorAll("[data-media-mode]").forEach((button) => button.addEventListener("click", () => setMediaMode(button.dataset.mediaMode)));
["dragenter", "dragover"].forEach((eventName) => dropzone.addEventListener(eventName, (event) => {
  event.preventDefault();
  dropzone.classList.add("dragging");
}));
["dragleave", "drop"].forEach((eventName) => dropzone.addEventListener(eventName, (event) => {
  event.preventDefault();
  dropzone.classList.remove("dragging");
}));
dropzone.addEventListener("drop", (event) => chooseFile(event.dataTransfer.files[0]));
setMediaMode("image");
