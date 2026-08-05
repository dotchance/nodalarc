#!/usr/bin/env node
// Fail the build when npm reports a high or critical advisory against the
// locked frontend dependency tree.
//
// npm audit's --audit-level flag changes npm's own exit code; it does not
// filter the report it prints. Counting severity words in that report
// therefore measures text, not severity: a moderate advisory whose title
// contains the word "high" reads as a blocking finding, and the count itself
// is a line tally rather than a number of vulnerabilities. This reads the
// machine-readable report instead.
//
// The check never guesses. If the report cannot be run, cannot be parsed, or
// does not carry the fields the decision depends on, the check fails and says
// so. A gate that cannot measure must not report "clear".

import { spawnSync } from "node:child_process";

const BLOCKING_SEVERITIES = ["critical", "high"];
const directory = process.argv[2] ?? "frontend";

function die(message, detail) {
  console.error(`[audit] ERROR: ${message}`);
  if (detail) {
    console.error(detail.trim());
  }
  process.exit(1);
}

// npm audit exits non-zero when it finds vulnerabilities, so the exit status
// says nothing about whether the audit itself ran. Parseable output does.
function runAudit(extraArgs) {
  const args = ["audit", "--json", ...extraArgs];
  const result = spawnSync("npm", args, {
    cwd: directory,
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
  });

  if (result.error) {
    die(`could not run "npm ${args.join(" ")}" in ${directory}`, String(result.error));
  }
  if (!result.stdout || !result.stdout.trim()) {
    die(`"npm ${args.join(" ")}" produced no output, so the vulnerability state is unknown`, result.stderr);
  }

  let report;
  try {
    report = JSON.parse(result.stdout);
  } catch {
    die(`"npm ${args.join(" ")}" did not emit JSON, so the vulnerability state is unknown`, result.stdout);
  }
  if (report.error) {
    die(`npm audit reported an error, so the vulnerability state is unknown`, JSON.stringify(report.error, null, 2));
  }
  if (!report.vulnerabilities || typeof report.vulnerabilities !== "object") {
    die(`npm audit output has no vulnerabilities map, so the vulnerability state is unknown`);
  }
  if (!report.metadata?.vulnerabilities || typeof report.metadata.vulnerabilities !== "object") {
    die(`npm audit output has no severity totals, so the vulnerability state is unknown`);
  }
  return report;
}

const full = runAudit([]);
const production = runAudit(["--omit=dev"]);
const shipped = new Set(Object.keys(production.vulnerabilities));

const blocking = Object.values(full.vulnerabilities).filter((entry) =>
  BLOCKING_SEVERITIES.includes(entry.severity),
);

// The named entries and the severity totals come from the same report. If they
// disagree, this check cannot say which one describes the tree, so it stops
// rather than picking the answer that happens to pass.
const totals = full.metadata.vulnerabilities;
const countedBlocking = BLOCKING_SEVERITIES.reduce((sum, severity) => sum + (totals[severity] ?? 0), 0);
if (countedBlocking !== blocking.length) {
  die(
    `npm audit severity totals (${countedBlocking}) disagree with the named advisories ` +
      `(${blocking.length}); the vulnerability state cannot be determined`,
  );
}

if (blocking.length === 0) {
  const dependencies = full.metadata.dependencies?.total ?? "an unknown number of";
  console.log(`[audit] No high or critical advisories across ${dependencies} locked packages.`);
  process.exit(0);
}

console.error("");
console.error("========================================================");
console.error("  SECURITY: high-severity npm advisories in the lockfile");
console.error("  DO NOT deploy this build.");
console.error("========================================================");
console.error("");

for (const entry of blocking.sort((a, b) => a.name.localeCompare(b.name))) {
  const reach = shipped.has(entry.name) ? "SHIPPED to the browser bundle" : "build and test toolchain only";
  console.error(`  ${entry.name} ${entry.range} [${entry.severity}]: ${reach}`);
  const advisories = (entry.via ?? []).filter((via) => typeof via === "object");
  for (const advisory of advisories) {
    console.error(`      ${advisory.title}`);
    console.error(`      ${advisory.url}`);
  }
  const fix = entry.fixAvailable;
  if (fix === true) {
    console.error("      fix: npm audit fix");
  } else if (fix && typeof fix === "object") {
    const breaking = fix.isSemVerMajor ? ", semver-major" : "";
    console.error(`      fix: ${fix.name}@${fix.version}${breaking}`);
  } else {
    console.error("      fix: none published");
  }
  console.error("");
}

console.error(`  ${blocking.length} blocking advisor${blocking.length === 1 ? "y" : "ies"}.`);
console.error("  Pull the latest source: git pull origin main");
console.error("  If the issue persists, report it.");
console.error("");
process.exit(1);
