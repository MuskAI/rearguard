export interface SelectedBatchFile {
  file: File;
  relativePath: string;
}

interface LegacyFileEntry {
  isFile: true;
  isDirectory: false;
  name: string;
  file: (success: (file: File) => void, failure?: (error: DOMException) => void) => void;
}

interface LegacyDirectoryReader {
  readEntries: (success: (entries: LegacyEntry[]) => void, failure?: (error: DOMException) => void) => void;
}

interface LegacyDirectoryEntry {
  isFile: false;
  isDirectory: true;
  name: string;
  createReader: () => LegacyDirectoryReader;
}

type LegacyEntry = LegacyFileEntry | LegacyDirectoryEntry;

function fileFromEntry(entry: LegacyFileEntry): Promise<File> {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

async function directoryEntries(entry: LegacyDirectoryEntry): Promise<LegacyEntry[]> {
  const reader = entry.createReader();
  const entries: LegacyEntry[] = [];
  while (true) {
    const page = await new Promise<LegacyEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
    if (page.length === 0) return entries;
    entries.push(...page);
  }
}

async function flattenEntry(entry: LegacyEntry, parent = ""): Promise<SelectedBatchFile[]> {
  const relativePath = parent ? `${parent}/${entry.name}` : entry.name;
  if (entry.isFile) return [{ file: await fileFromEntry(entry), relativePath }];
  const children = await directoryEntries(entry);
  const nested = await Promise.all(children.map((child) => flattenEntry(child, relativePath)));
  return nested.flat();
}

function deduplicateAndSort(files: SelectedBatchFile[]): SelectedBatchFile[] {
  const seen = new Set<string>();
  return files.filter(({ file, relativePath }) => {
    const key = `${relativePath}\u0000${file.size}\u0000${file.lastModified}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).sort((left, right) => left.relativePath.localeCompare(right.relativePath, "zh-CN", { numeric: true }));
}

export function batchFilesFromList(files: FileList | File[]): SelectedBatchFile[] {
  return deduplicateAndSort(Array.from(files).map((file) => ({
    file,
    relativePath: file.webkitRelativePath || file.name,
  })));
}

export function dropContainsDirectory(dataTransfer: DataTransfer): boolean {
  return Array.from(dataTransfer.items || []).some((item) => {
    const legacyItem = item as unknown as { webkitGetAsEntry?: () => LegacyEntry | null };
    return legacyItem.webkitGetAsEntry?.()?.isDirectory === true;
  });
}

export async function batchFilesFromDrop(dataTransfer: DataTransfer): Promise<SelectedBatchFile[]> {
  const entries = Array.from(dataTransfer.items || []).map((item) => {
    const legacyItem = item as unknown as { webkitGetAsEntry?: () => LegacyEntry | null };
    return legacyItem.webkitGetAsEntry?.() || null;
  }).filter((entry): entry is LegacyEntry => Boolean(entry));
  if (entries.length === 0) return batchFilesFromList(dataTransfer.files);
  const nested = await Promise.all(entries.map((entry) => flattenEntry(entry)));
  return deduplicateAndSort(nested.flat());
}
