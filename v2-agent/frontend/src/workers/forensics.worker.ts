/// <reference lib="webworker" />

import FFT from "fft.js";

type ForensicStatus = "ok" | "warn" | "danger";

interface WorkerRequest {
  buffer: ArrayBuffer;
  mime: string;
  maxSide: number;
  maxSourcePixels: number;
}

interface PreviewItem {
  key: string;
  title: string;
  explanation: string;
  status: ForensicStatus;
  finding: string;
  image: string;
}

interface JpegPoint {
  quality: number;
  error: number;
}

const workerScope = self as unknown as DedicatedWorkerGlobalScope;
const MAX_SOURCE_PIXELS = 24_000_000;
const FFT_SIDE = 256;

const SUITE_META = {
  ela: {
    title: "压缩对齐分析",
    explanation: "按固定质量重新压缩并计算误差，用于观察局部压缩历史是否一致。",
  },
  noise: {
    title: "噪声成分分析",
    explanation: "提取高频残差，用于观察相机噪声和局部纹理是否连续。",
  },
  noise_consistency: {
    title: "噪声一致性分析",
    explanation: "将噪声强度映射为伪彩色，突出不连续的区块和色带。",
  },
  fft: {
    title: "频域分析",
    explanation: "显示傅里叶幅度谱，用于观察规则网格和异常高频结构。",
  },
  light_gradient: {
    title: "光照梯度分析",
    explanation: "将亮度梯度映射为法线色彩，用于观察突变和不自然的光照过渡。",
  },
  light_consistency: {
    title: "光照一致性分析",
    explanation: "估计整图主光方向并叠加箭头，辅助比较局部高光方向。",
  },
  jpeg_curve: {
    title: "多次 JPEG 压缩检测",
    explanation: "比较多个压缩质量下的平均误差，用于交叉验证压缩历史。",
  },
} as const;

function context2d(canvas: OffscreenCanvas): OffscreenCanvasRenderingContext2D {
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) throw new Error("浏览器无法创建离屏画布");
  return context;
}

function makeCanvas(width: number, height: number): OffscreenCanvas {
  return new OffscreenCanvas(Math.max(1, width), Math.max(1, height));
}

async function dataUrl(canvas: OffscreenCanvas): Promise<string> {
  const blob = await canvas.convertToBlob({ type: "image/png" });
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = "";
  const chunkSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return `data:${blob.type};base64,${btoa(binary)}`;
}

function ascii(bytes: Uint8Array, offset: number, length: number): string {
  return String.fromCharCode(...bytes.subarray(offset, offset + length));
}

function imageDimensions(bytes: Uint8Array): { width: number; height: number } | null {
  if (bytes.length >= 24 && bytes[0] === 0x89 && ascii(bytes, 1, 3) === "PNG") {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    return { width: view.getUint32(16), height: view.getUint32(20) };
  }
  if (bytes.length >= 10 && (ascii(bytes, 0, 6) === "GIF87a" || ascii(bytes, 0, 6) === "GIF89a")) {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    return { width: view.getUint16(6, true), height: view.getUint16(8, true) };
  }
  if (bytes.length >= 26 && ascii(bytes, 0, 2) === "BM") {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    return { width: Math.abs(view.getInt32(18, true)), height: Math.abs(view.getInt32(22, true)) };
  }
  if (bytes.length >= 30 && ascii(bytes, 0, 4) === "RIFF" && ascii(bytes, 8, 4) === "WEBP") {
    const chunk = ascii(bytes, 12, 4);
    if (chunk === "VP8X") {
      const width = 1 + bytes[24] + (bytes[25] << 8) + (bytes[26] << 16);
      const height = 1 + bytes[27] + (bytes[28] << 8) + (bytes[29] << 16);
      return { width, height };
    }
    if (chunk === "VP8L" && bytes[20] === 0x2f) {
      const width = 1 + bytes[21] + ((bytes[22] & 0x3f) << 8);
      const height = 1 + ((bytes[22] & 0xc0) >> 6) + (bytes[23] << 2) + ((bytes[24] & 0x0f) << 10);
      return { width, height };
    }
    if (chunk === "VP8 " && bytes[23] === 0x9d && bytes[24] === 0x01 && bytes[25] === 0x2a) {
      const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
      return { width: view.getUint16(26, true) & 0x3fff, height: view.getUint16(28, true) & 0x3fff };
    }
  }
  if (bytes.length >= 10 && bytes[0] === 0xff && bytes[1] === 0xd8) {
    const startOfFrame = new Set([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf]);
    let offset = 2;
    while (offset + 8 < bytes.length) {
      if (bytes[offset] !== 0xff) {
        offset += 1;
        continue;
      }
      const marker = bytes[offset + 1];
      if (marker === 0xd9 || marker === 0xda) break;
      if (marker === 0x00 || marker === 0x01 || marker === 0xff || (marker >= 0xd0 && marker <= 0xd8)) {
        offset += 2;
        continue;
      }
      const segmentLength = (bytes[offset + 2] << 8) | bytes[offset + 3];
      if (segmentLength < 2 || offset + segmentLength + 2 > bytes.length) break;
      if (startOfFrame.has(marker)) {
        return {
          width: (bytes[offset + 7] << 8) | bytes[offset + 8],
          height: (bytes[offset + 5] << 8) | bytes[offset + 6],
        };
      }
      offset += segmentLength + 2;
    }
  }
  return null;
}

function jpegOrientation(bytes: Uint8Array): number {
  if (bytes.length < 14 || bytes[0] !== 0xff || bytes[1] !== 0xd8) return 1;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let offset = 2;
  while (offset + 10 < bytes.length) {
    if (bytes[offset] !== 0xff) {
      offset += 1;
      continue;
    }
    const marker = bytes[offset + 1];
    if (marker === 0xd9 || marker === 0xda) break;
    if (marker === 0x00 || marker === 0x01 || marker === 0xff || (marker >= 0xd0 && marker <= 0xd8)) {
      offset += 2;
      continue;
    }
    const segmentLength = view.getUint16(offset + 2);
    if (segmentLength < 2 || offset + segmentLength + 2 > bytes.length) break;
    const payload = offset + 4;
    if (marker === 0xe1 && segmentLength >= 16 && ascii(bytes, payload, 6) === "Exif\0\0") {
      const tiff = payload + 6;
      const byteOrder = ascii(bytes, tiff, 2);
      const littleEndian = byteOrder === "II";
      if (!littleEndian && byteOrder !== "MM") return 1;
      if (view.getUint16(tiff + 2, littleEndian) !== 42) return 1;
      const ifdOffset = view.getUint32(tiff + 4, littleEndian);
      const directory = tiff + ifdOffset;
      if (directory + 2 > bytes.length) return 1;
      const entries = view.getUint16(directory, littleEndian);
      for (let index = 0; index < entries; index += 1) {
        const entry = directory + 2 + index * 12;
        if (entry + 12 > bytes.length) break;
        if (view.getUint16(entry, littleEndian) !== 0x0112) continue;
        const orientation = view.getUint16(entry + 8, littleEndian);
        return orientation >= 1 && orientation <= 8 ? orientation : 1;
      }
      return 1;
    }
    offset += segmentLength + 2;
  }
  return 1;
}

function drawWithOrientation(
  context: OffscreenCanvasRenderingContext2D,
  bitmap: ImageBitmap,
  orientation: number,
): void {
  const width = bitmap.width;
  const height = bitmap.height;
  if (orientation === 2) context.setTransform(-1, 0, 0, 1, width, 0);
  else if (orientation === 3) context.setTransform(-1, 0, 0, -1, width, height);
  else if (orientation === 4) context.setTransform(1, 0, 0, -1, 0, height);
  else if (orientation === 5) context.setTransform(0, 1, 1, 0, 0, 0);
  else if (orientation === 6) context.setTransform(0, 1, -1, 0, height, 0);
  else if (orientation === 7) context.setTransform(0, -1, -1, 0, height, width);
  else if (orientation === 8) context.setTransform(0, -1, 1, 0, 0, width);
  context.drawImage(bitmap, 0, 0);
  context.setTransform(1, 0, 0, 1, 0, 0);
}

async function sourceCanvas(buffer: ArrayBuffer, mime: string, maxSide: number, maxSourcePixels: number): Promise<OffscreenCanvas> {
  const bytes = new Uint8Array(buffer);
  const dimensions = imageDimensions(bytes);
  if (!dimensions?.width || !dimensions.height) throw new Error("无法安全读取图片尺寸，本地预览已跳过");
  if (dimensions.width * dimensions.height > Math.min(MAX_SOURCE_PIXELS, maxSourcePixels || MAX_SOURCE_PIXELS)) {
    throw new Error("图片像素过高，本地预览已跳过，服务端仍会继续判读");
  }
  const scale = Math.min(1, maxSide / Math.max(dimensions.width, dimensions.height));
  const width = Math.max(1, Math.round(dimensions.width * scale));
  const height = Math.max(1, Math.round(dimensions.height * scale));
  const orientation = jpegOrientation(bytes);
  const bitmap = await createImageBitmap(new Blob([buffer], { type: mime || "image/jpeg" }), {
    imageOrientation: "none",
    resizeWidth: width,
    resizeHeight: height,
    resizeQuality: "high",
  });
  const swapsAxes = orientation >= 5 && orientation <= 8;
  const canvas = makeCanvas(swapsAxes ? bitmap.height : bitmap.width, swapsAxes ? bitmap.width : bitmap.height);
  const context = context2d(canvas);
  context.imageSmoothingEnabled = true;
  context.imageSmoothingQuality = "high";
  drawWithOrientation(context, bitmap, orientation);
  bitmap.close();
  return canvas;
}

function imageData(canvas: OffscreenCanvas): ImageData {
  return context2d(canvas).getImageData(0, 0, canvas.width, canvas.height);
}

function grayscale(pixels: Uint8ClampedArray): Float32Array {
  const result = new Float32Array(pixels.length / 4);
  for (let source = 0, target = 0; source < pixels.length; source += 4, target += 1) {
    result[target] = pixels[source] * 0.299 + pixels[source + 1] * 0.587 + pixels[source + 2] * 0.114;
  }
  return result;
}

function separableBlur(gray: Float32Array, width: number, height: number): Float32Array {
  const weights = [1, 4, 6, 4, 1];
  const horizontal = new Float32Array(gray.length);
  const result = new Float32Array(gray.length);

  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      let value = 0;
      for (let k = -2; k <= 2; k += 1) {
        const sampleX = Math.max(0, Math.min(width - 1, x + k));
        value += gray[y * width + sampleX] * weights[k + 2];
      }
      horizontal[y * width + x] = value / 16;
    }
  }

  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      let value = 0;
      for (let k = -2; k <= 2; k += 1) {
        const sampleY = Math.max(0, Math.min(height - 1, y + k));
        value += horizontal[sampleY * width + x] * weights[k + 2];
      }
      result[y * width + x] = value / 16;
    }
  }
  return result;
}

function range(values: Float32Array): { min: number; max: number } {
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  for (const value of values) {
    if (value < min) min = value;
    if (value > max) max = value;
  }
  return { min, max: max <= min ? min + 1 : max };
}

function normalize(value: number, min: number, max: number): number {
  return Math.max(0, Math.min(1, (value - min) / (max - min)));
}

function jet(value: number): [number, number, number] {
  const v = Math.max(0, Math.min(1, value));
  const red = Math.max(0, Math.min(1, 1.5 - Math.abs(4 * v - 3)));
  const green = Math.max(0, Math.min(1, 1.5 - Math.abs(4 * v - 2)));
  const blue = Math.max(0, Math.min(1, 1.5 - Math.abs(4 * v - 1)));
  return [Math.round(red * 255), Math.round(green * 255), Math.round(blue * 255)];
}

function residualCanvases(source: OffscreenCanvas): { noise: OffscreenCanvas; consistency: OffscreenCanvas } {
  const sourceData = imageData(source);
  const gray = grayscale(sourceData.data);
  const blurred = separableBlur(gray, source.width, source.height);
  const residual = new Float32Array(gray.length);
  for (let index = 0; index < gray.length; index += 1) residual[index] = Math.abs(gray[index] - blurred[index]);
  const limits = range(residual);

  const noise = makeCanvas(source.width, source.height);
  const noiseData = context2d(noise).createImageData(source.width, source.height);
  const consistency = makeCanvas(source.width, source.height);
  const consistencyData = context2d(consistency).createImageData(source.width, source.height);
  for (let index = 0; index < residual.length; index += 1) {
    const value = normalize(residual[index], limits.min, limits.max);
    const grayValue = Math.round(value * 255);
    const [red, green, blue] = jet(value);
    const target = index * 4;
    noiseData.data[target] = grayValue;
    noiseData.data[target + 1] = grayValue;
    noiseData.data[target + 2] = grayValue;
    noiseData.data[target + 3] = 255;
    consistencyData.data[target] = red;
    consistencyData.data[target + 1] = green;
    consistencyData.data[target + 2] = blue;
    consistencyData.data[target + 3] = 255;
  }
  context2d(noise).putImageData(noiseData, 0, 0);
  context2d(consistency).putImageData(consistencyData, 0, 0);
  return { noise, consistency };
}

async function elaCanvas(source: OffscreenCanvas): Promise<OffscreenCanvas> {
  const compressed = await source.convertToBlob({ type: "image/jpeg", quality: 0.9 });
  const bitmap = await createImageBitmap(compressed);
  const comparison = makeCanvas(source.width, source.height);
  context2d(comparison).drawImage(bitmap, 0, 0, source.width, source.height);
  bitmap.close();

  const original = imageData(source).data;
  const resaved = imageData(comparison).data;
  let maxDifference = 1;
  for (let index = 0; index < original.length; index += 4) {
    maxDifference = Math.max(
      maxDifference,
      Math.abs(original[index] - resaved[index]),
      Math.abs(original[index + 1] - resaved[index + 1]),
      Math.abs(original[index + 2] - resaved[index + 2]),
    );
  }

  const canvas = makeCanvas(source.width, source.height);
  const output = context2d(canvas).createImageData(source.width, source.height);
  const gain = 255 / maxDifference;
  for (let index = 0; index < original.length; index += 4) {
    output.data[index] = Math.min(255, Math.abs(original[index] - resaved[index]) * gain);
    output.data[index + 1] = Math.min(255, Math.abs(original[index + 1] - resaved[index + 1]) * gain);
    output.data[index + 2] = Math.min(255, Math.abs(original[index + 2] - resaved[index + 2]) * gain);
    output.data[index + 3] = 255;
  }
  context2d(canvas).putImageData(output, 0, 0);
  return canvas;
}

function fftCanvas(source: OffscreenCanvas): OffscreenCanvas {
  const resized = makeCanvas(FFT_SIDE, FFT_SIDE);
  const resizedContext = context2d(resized);
  resizedContext.imageSmoothingEnabled = true;
  resizedContext.imageSmoothingQuality = "high";
  const cropSide = Math.min(source.width, source.height);
  const cropX = (source.width - cropSide) / 2;
  const cropY = (source.height - cropSide) / 2;
  resizedContext.drawImage(source, cropX, cropY, cropSide, cropSide, 0, 0, FFT_SIDE, FFT_SIDE);
  const gray = grayscale(imageData(resized).data);
  let mean = 0;
  for (const value of gray) mean += value;
  mean /= gray.length;

  const fft = new FFT(FFT_SIDE);
  const real = new Float64Array(FFT_SIDE * FFT_SIDE);
  const imaginary = new Float64Array(FFT_SIDE * FFT_SIDE);
  const input = new Float64Array(FFT_SIDE * 2);
  const output = new Float64Array(FFT_SIDE * 2);

  for (let y = 0; y < FFT_SIDE; y += 1) {
    const windowY = 0.5 - 0.5 * Math.cos((2 * Math.PI * y) / (FFT_SIDE - 1));
    for (let x = 0; x < FFT_SIDE; x += 1) {
      const windowX = 0.5 - 0.5 * Math.cos((2 * Math.PI * x) / (FFT_SIDE - 1));
      input[x * 2] = (gray[y * FFT_SIDE + x] - mean) * windowX * windowY;
      input[x * 2 + 1] = 0;
    }
    fft.transform(output, input);
    for (let x = 0; x < FFT_SIDE; x += 1) {
      real[y * FFT_SIDE + x] = output[x * 2];
      imaginary[y * FFT_SIDE + x] = output[x * 2 + 1];
    }
  }

  for (let x = 0; x < FFT_SIDE; x += 1) {
    for (let y = 0; y < FFT_SIDE; y += 1) {
      input[y * 2] = real[y * FFT_SIDE + x];
      input[y * 2 + 1] = imaginary[y * FFT_SIDE + x];
    }
    fft.transform(output, input);
    for (let y = 0; y < FFT_SIDE; y += 1) {
      real[y * FFT_SIDE + x] = output[y * 2];
      imaginary[y * FFT_SIDE + x] = output[y * 2 + 1];
    }
  }

  const magnitude = new Float32Array(FFT_SIDE * FFT_SIDE);
  for (let y = 0; y < FFT_SIDE; y += 1) {
    for (let x = 0; x < FFT_SIDE; x += 1) {
      const shiftedX = (x + FFT_SIDE / 2) % FFT_SIDE;
      const shiftedY = (y + FFT_SIDE / 2) % FFT_SIDE;
      const index = y * FFT_SIDE + x;
      magnitude[shiftedY * FFT_SIDE + shiftedX] = Math.log1p(Math.hypot(real[index], imaginary[index]));
    }
  }

  const sorted = Array.from(magnitude).sort((a, b) => a - b);
  const min = sorted[Math.floor(sorted.length * 0.02)];
  const max = sorted[Math.floor(sorted.length * 0.995)] || min + 1;
  const canvas = makeCanvas(FFT_SIDE, FFT_SIDE);
  const result = context2d(canvas).createImageData(FFT_SIDE, FFT_SIDE);
  for (let index = 0; index < magnitude.length; index += 1) {
    const value = Math.round(normalize(magnitude[index], min, max) * 255);
    const target = index * 4;
    result.data[target] = value;
    result.data[target + 1] = value;
    result.data[target + 2] = value;
    result.data[target + 3] = 255;
  }
  context2d(canvas).putImageData(result, 0, 0);
  return canvas;
}

function gradientCanvases(source: OffscreenCanvas): { gradient: OffscreenCanvas; consistency: OffscreenCanvas } {
  const gray = grayscale(imageData(source).data);
  const gx = new Float32Array(gray.length);
  const gy = new Float32Array(gray.length);
  let meanX = 0;
  let meanY = 0;

  for (let y = 0; y < source.height; y += 1) {
    const up = Math.max(0, y - 1);
    const down = Math.min(source.height - 1, y + 1);
    for (let x = 0; x < source.width; x += 1) {
      const left = Math.max(0, x - 1);
      const right = Math.min(source.width - 1, x + 1);
      const index = y * source.width + x;
      gx[index] = (gray[y * source.width + right] - gray[y * source.width + left]) / 2;
      gy[index] = (gray[down * source.width + x] - gray[up * source.width + x]) / 2;
      meanX += gx[index];
      meanY += gy[index];
    }
  }

  const limitsX = range(gx);
  const limitsY = range(gy);
  const gradient = makeCanvas(source.width, source.height);
  const gradientData = context2d(gradient).createImageData(source.width, source.height);
  for (let index = 0; index < gray.length; index += 1) {
    const target = index * 4;
    gradientData.data[target] = Math.round(normalize(gx[index], limitsX.min, limitsX.max) * 255);
    gradientData.data[target + 1] = Math.round(normalize(gy[index], limitsY.min, limitsY.max) * 255);
    gradientData.data[target + 2] = 128;
    gradientData.data[target + 3] = 255;
  }
  context2d(gradient).putImageData(gradientData, 0, 0);

  const consistency = makeCanvas(source.width, source.height);
  const consistencyContext = context2d(consistency);
  consistencyContext.drawImage(source, 0, 0);
  let directionX = -meanX;
  let directionY = -meanY;
  const directionLength = Math.hypot(directionX, directionY) || 1;
  directionX /= directionLength;
  directionY /= directionLength;
  const centerX = source.width / 2;
  const centerY = source.height / 2;
  const arrowLength = Math.min(source.width, source.height) * 0.28;
  const endX = centerX + directionX * arrowLength;
  const endY = centerY + directionY * arrowLength;
  const angle = Math.atan2(endY - centerY, endX - centerX);
  const lineWidth = Math.max(3, Math.round(source.width / 200));
  consistencyContext.strokeStyle = "#39ff14";
  consistencyContext.lineWidth = lineWidth;
  consistencyContext.lineCap = "round";
  consistencyContext.beginPath();
  consistencyContext.moveTo(centerX, centerY);
  consistencyContext.lineTo(endX, endY);
  for (const delta of [Math.PI * 0.83, -Math.PI * 0.83]) {
    consistencyContext.moveTo(endX, endY);
    consistencyContext.lineTo(
      endX + arrowLength * 0.25 * Math.cos(angle + delta),
      endY + arrowLength * 0.25 * Math.sin(angle + delta),
    );
  }
  consistencyContext.stroke();
  return { gradient, consistency };
}

async function jpegCurveCanvas(source: OffscreenCanvas): Promise<{ canvas: OffscreenCanvas; points: JpegPoint[] }> {
  const original = imageData(source).data;
  const points: JpegPoint[] = [];
  for (let quality = 50; quality < 100; quality += 5) {
    const blob = await source.convertToBlob({ type: "image/jpeg", quality: quality / 100 });
    const bitmap = await createImageBitmap(blob);
    const comparison = makeCanvas(source.width, source.height);
    context2d(comparison).drawImage(bitmap, 0, 0, source.width, source.height);
    bitmap.close();
    const compressed = imageData(comparison).data;
    let error = 0;
    for (let index = 0; index < original.length; index += 4) {
      error += Math.abs(original[index] - compressed[index]);
      error += Math.abs(original[index + 1] - compressed[index + 1]);
      error += Math.abs(original[index + 2] - compressed[index + 2]);
    }
    points.push({ quality, error: Math.round((error / (source.width * source.height * 3)) * 100) / 100 });
  }

  const canvas = makeCanvas(480, 330);
  const context = context2d(canvas);
  context.fillStyle = "#f8fbfc";
  context.fillRect(0, 0, canvas.width, canvas.height);
  const left = 58;
  const right = 22;
  const top = 42;
  const bottom = 46;
  const chartWidth = canvas.width - left - right;
  const chartHeight = canvas.height - top - bottom;
  const values = points.map((point) => point.error);
  const minError = Math.min(...values);
  const maxError = Math.max(...values);
  const errorRange = Math.max(0.1, maxError - minError);

  context.strokeStyle = "#d6e0e3";
  context.lineWidth = 1;
  for (let step = 0; step <= 4; step += 1) {
    const y = top + (chartHeight * step) / 4;
    context.beginPath();
    context.moveTo(left, y);
    context.lineTo(left + chartWidth, y);
    context.stroke();
  }
  context.strokeStyle = "#73848a";
  context.beginPath();
  context.moveTo(left, top);
  context.lineTo(left, top + chartHeight);
  context.lineTo(left + chartWidth, top + chartHeight);
  context.stroke();

  context.fillStyle = "#263b42";
  context.font = "600 16px system-ui, sans-serif";
  context.fillText("多次 JPEG 压缩误差", left, 25);
  context.font = "12px system-ui, sans-serif";
  context.fillStyle = "#687b82";
  context.fillText("压缩质量", left + chartWidth / 2 - 24, canvas.height - 14);
  context.fillText("50", left - 6, top + chartHeight + 20);
  context.fillText("95", left + chartWidth - 8, top + chartHeight + 20);
  context.save();
  context.translate(16, top + chartHeight / 2 + 28);
  context.rotate(-Math.PI / 2);
  context.fillText("平均误差", 0, 0);
  context.restore();

  context.strokeStyle = "#196b8f";
  context.lineWidth = 3;
  context.lineJoin = "round";
  context.beginPath();
  points.forEach((point, index) => {
    const x = left + (chartWidth * index) / (points.length - 1);
    const y = top + chartHeight - ((point.error - minError) / errorRange) * chartHeight;
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
  context.stroke();
  return { canvas, points };
}

async function emitItem(
  key: keyof typeof SUITE_META,
  canvas: OffscreenCanvas,
  started: number,
  jpegPoints?: JpegPoint[],
): Promise<PreviewItem> {
  const meta = SUITE_META[key];
  const item: PreviewItem = {
    key,
    title: meta.title,
    explanation: meta.explanation,
    status: "ok",
    finding: "低分辨率预览已在本机生成，不作为结论；等待服务端无损图谱判读。",
    image: await dataUrl(canvas),
  };
  workerScope.postMessage({ type: "item", item, jpegPoints, elapsedMs: Math.round(performance.now() - started) });
  return item;
}

async function generate(request: WorkerRequest): Promise<void> {
  const started = performance.now();
  const source = await sourceCanvas(
    request.buffer,
    request.mime,
    request.maxSide || 640,
    request.maxSourcePixels || MAX_SOURCE_PIXELS,
  );

  await emitItem("ela", await elaCanvas(source), started);
  const residual = residualCanvases(source);
  await emitItem("noise", residual.noise, started);
  await emitItem("noise_consistency", residual.consistency, started);
  await emitItem("fft", fftCanvas(source), started);
  const gradient = gradientCanvases(source);
  await emitItem("light_gradient", gradient.gradient, started);
  await emitItem("light_consistency", gradient.consistency, started);
  const jpeg = await jpegCurveCanvas(source);
  await emitItem("jpeg_curve", jpeg.canvas, started, jpeg.points);

  workerScope.postMessage({
    type: "complete",
    jpegPoints: jpeg.points,
    elapsedMs: Math.round(performance.now() - started),
  });
}

workerScope.onmessage = (event: MessageEvent<WorkerRequest>) => {
  void generate(event.data).catch((error: unknown) => {
    workerScope.postMessage({ type: "error", message: error instanceof Error ? error.message : "本地图谱生成失败" });
  });
};

export {};
