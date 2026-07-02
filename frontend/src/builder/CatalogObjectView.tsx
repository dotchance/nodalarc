// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Read-only spec view of one catalog document — click-to-inspect from the
 *  library. Renders the grammar object as nested key/value rows; the grammar
 *  is the schema, so nothing is reshaped or hidden. */

import { KeyValueRow } from "../ui/KeyValueRow";

const MAX_DEPTH = 4;

function Rows({ value, depth }: { value: unknown; depth: number }) {
  if (depth > MAX_DEPTH) return <div className="builder-zone-empty">…</div>;
  if (Array.isArray(value)) {
    return (
      <>
        {value.map((item, index) => (
          <div className="builder-object-nested" key={index}>
            <div className="builder-outline-kind">[{index}]</div>
            <Rows value={item} depth={depth + 1} />
          </div>
        ))}
      </>
    );
  }
  if (typeof value === "object" && value !== null) {
    return (
      <>
        {Object.entries(value).map(([key, child]) =>
          typeof child === "object" && child !== null ? (
            <div className="builder-object-nested" key={key}>
              <div className="builder-outline-kind">{key}</div>
              <Rows value={child} depth={depth + 1} />
            </div>
          ) : (
            <KeyValueRow label={key} key={key}>
              {String(child)}
            </KeyValueRow>
          ),
        )}
      </>
    );
  }
  return <KeyValueRow label="value">{String(value)}</KeyValueRow>;
}

export function CatalogObjectView({
  refStr,
  document,
}: {
  refStr: string;
  document: Record<string, unknown>;
}) {
  return (
    <div className="builder-inspector-stack" data-testid="catalog-inspect">
      <div className="builder-inspector-name">{refStr}</div>
      <Rows value={document} depth={0} />
    </div>
  );
}
