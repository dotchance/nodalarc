// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Browser transfer for ordinary NodalArc YAML files. */

import { downloadBlob } from "../ui/downloadBlob";
import type { CatalogYamlFile } from "./generated/builderApi";

interface WritableYamlFileHandle {
  createWritable(): Promise<{
    write(content: string): Promise<void>;
    close(): Promise<void>;
  }>;
}

interface WritableYamlDirectoryHandle {
  getDirectoryHandle(
    name: string,
    options: { create: boolean },
  ): Promise<WritableYamlDirectoryHandle>;
  getFileHandle(name: string, options: { create: true }): Promise<WritableYamlFileHandle>;
}

type DirectoryPicker = (options: { mode: "readwrite" }) => Promise<WritableYamlDirectoryHandle>;

function logicalPathParts(logicalPath: string): string[] {
  const parts = logicalPath.split("/");
  if (
    parts.length === 0 ||
    parts.some((part) => part.length === 0 || part === "." || part === "..") ||
    !parts[parts.length - 1]?.match(/\.ya?ml$/i)
  ) {
    throw new Error(`backend returned an invalid YAML path: ${logicalPath}`);
  }
  return parts;
}

async function writeYamlDirectory(
  root: WritableYamlDirectoryHandle,
  files: readonly CatalogYamlFile[],
): Promise<void> {
  for (const file of files) {
    const parts = logicalPathParts(file.logical_path);
    let directory = root;
    for (const name of parts.slice(0, -1)) {
      directory = await directory.getDirectoryHandle(name, { create: true });
    }
    const handle = await directory.getFileHandle(parts[parts.length - 1]!, { create: true });
    const writable = await handle.createWritable();
    await writable.write(file.yaml_text);
    await writable.close();
  }
}

async function freshYamlExportDirectory(
  root: WritableYamlDirectoryHandle,
  name: string,
): Promise<WritableYamlDirectoryHandle> {
  try {
    await root.getDirectoryHandle(name, { create: false });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "NotFoundError") {
      return root.getDirectoryHandle(name, { create: true });
    }
    throw cause;
  }
  throw new Error(`export directory already exists: ${name}`);
}

function fallbackYamlFilename(logicalPath: string): string {
  logicalPathParts(logicalPath);
  return encodeURIComponent(logicalPath);
}

/** Write one exact backend-provided YAML file set without a private carrier format. */
export async function writeSessionYamlExport(
  sessionRef: string,
  files: readonly CatalogYamlFile[],
): Promise<void> {
  const picker = (
    globalThis as typeof globalThis & { showDirectoryPicker?: DirectoryPicker }
  ).showDirectoryPicker;
  if (picker) {
    const selected = await picker({ mode: "readwrite" });
    const sessionName = sessionRef.split("/").pop()?.replace(/\.ya?ml$/, "") ?? "session";
    const directory = await freshYamlExportDirectory(
      selected,
      `${sessionName}-nodalarc-session`,
    );
    await writeYamlDirectory(directory, files);
    return;
  }
  for (const file of files) {
    downloadBlob(file.yaml_text, fallbackYamlFilename(file.logical_path));
  }
}
