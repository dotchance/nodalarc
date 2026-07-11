import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const CATALOG_DIR = join(process.cwd(), "src", "catalog");
const HOOKS_DIR = join(process.cwd(), "src", "hooks");

describe("Wizard backend authority", () => {
  it("does not keep protocol or extension inventories in production UI code", () => {
    const panel = readFileSync(join(CATALOG_DIR, "ProtocolPanel.tsx"), "utf-8");
    const hook = readFileSync(join(HOOKS_DIR, "useWizard.ts"), "utf-8");

    expect(panel).not.toContain("PROTOCOL_INFO");
    expect(panel).not.toContain("EXTENSION_INFO");
    expect(panel).not.toContain('protocol === "isis"');
    expect(panel).not.toContain('protocol === "ospf"');
    expect(panel).not.toContain("BFD (Bidirectional Forwarding Detection)");
    expect(panel).not.toContain("Enable BFD");
    expect(panel).not.toContain("bfd_detect_multiplier");
    expect(panel).not.toContain("bfd_rx_interval");
    expect(panel).not.toContain("bfd_tx_interval");
    expect(panel).toContain("rules.protocols.map");
    expect(panel).toContain("rules.extensions.map");
    expect(panel).toContain("rules.bfd.timer_fields.map");

    expect(hook).not.toContain("protocols.isis");
    expect(hook).not.toContain("protocols.ospf");
    expect(hook).not.toContain('protocol === "isis"');
    expect(hook).not.toContain('protocol === "ospf"');
    expect(hook).toContain("protocols.find");
  });

  it("does not hardcode selectable Walker pattern options", () => {
    const panel = readFileSync(join(CATALOG_DIR, "ConstellationPanel.tsx"), "utf-8");

    expect(panel).not.toContain('<option value="walker_delta"');
    expect(panel).not.toContain('<option value="walker_star"');
    expect(panel).toContain("patterns.map");
    expect(panel).toContain("selectedPattern.description");
  });

  it("accepts only the typed backend coverage-warning contract", () => {
    const preview = readFileSync(join(CATALOG_DIR, "CoveragePreview.tsx"), "utf-8");

    expect(preview).not.toContain('typeof item === "string"');
    expect(preview).not.toContain('typeof w === "string"');
    expect(preview).not.toContain("| string");
  });
});
