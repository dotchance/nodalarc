// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
/** Session launcher: user and shipped catalog sessions as dense source-aware
 *  rows, deployed via the session switch, plus raw-YAML file deploy. Sessions
 *  are authored in the Session Builder, which the rail opens.
 */

import { useState, useCallback, useRef } from "react";
import type { SessionSwitchResult } from "../hooks/useSessionSwitcher";
import type { CatalogSessionSourceId, CatalogSessionSummary } from "../builder/generated/builderApi";
import { REST_URL, authHeaders } from "../config";
import { apiErrorMessage, apiErrorFromException } from "../ui/apiError";
import { RecordHistoryToggle } from "../ui/RecordHistoryToggle";
import { downloadBlob } from "../ui/downloadBlob";
import { Badge } from "../ui/Badge";
import { Button, IconButton } from "../ui/Button";

interface SessionLauncherProps {
  onDeployStarted: () => void;
  onClose: (() => void) | undefined;
  deploying: boolean;
  systemNotice?: string;
  /** Scoped user and shipped catalog sessions, deployable as-is via session switch. */
  sessions: CatalogSessionSummary[];
  /** Why the session list did not load from VS-API; null once it loaded. */
  sessionsError: string | null;
  /** Ask VS-API to switch to a session; resolves with its answer. */
  onLaunchSession: (session: CatalogSessionSummary, recordHistory: boolean) => Promise<SessionSwitchResult>;
  /** Session Builder entry: navigates to the builder view. Day-0 authoring
   *  needs no deployed session, so this bypasses the hasEverDeployed close
   *  gate — it goes somewhere, not to nothing. */
  onOpenBuilder?: () => void;
}

export function SessionLauncher({
  onDeployStarted,
  onClose,
  deploying,
  systemNotice,
  sessions,
  sessionsError,
  onLaunchSession,
  onOpenBuilder,
}: SessionLauncherProps) {
  // Why the last launch was refused; the launcher stays open to show it.
  const [launchError, setLaunchError] = useState<string | null>(null);
  // Any listed session's stored YAML is downloadable, including the running one.
  const [downloadingFile, setDownloadingFile] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const downloadSessionYaml = useCallback(async (source: CatalogSessionSourceId, name: string) => {
    setDownloadError(null);
    setDownloadingFile(source.session_ref);
    try {
      const response = await fetch(
        `${REST_URL}/api/v1/sessions/yaml?session_ref=${encodeURIComponent(source.session_ref)}`,
        { headers: authHeaders() },
      );
      if (!response.ok) throw new Error(await apiErrorMessage(response));
      downloadBlob(await response.text(), `${name}.yaml`);
    } catch (e) {
      setDownloadError(apiErrorFromException(e));
    } finally {
      setDownloadingFile(null);
    }
  }, []);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const sessionGroups = [
    {
      source: "user" as const,
      label: "User-created sessions",
      description: "Editable sessions saved in your user: catalog",
      sessions: sessions.filter((session) => session.source === "user"),
    },
    {
      source: "nodalarc" as const,
      label: "NodalArc defaults",
      description: "Shipped with NodalArc and read-only",
      sessions: sessions.filter((session) => session.source === "nodalarc"),
    },
  ];

  // Every deploy from this launcher records the session run's history when set.
  const [recordHistory, setRecordHistory] = useState(false);

  const handleUpload = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      setUploadError(null);
      setUploading(true);
      try {
        const resp = await fetch(`${REST_URL}/api/v1/session/deploy-from-yaml`, {
          method: "POST",
          headers: authHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ yaml: await file.text(), record_history: recordHistory }),
        });
        if (resp.ok) onDeployStarted();
        else setUploadError(await apiErrorMessage(resp));
      } catch (err) {
        setUploadError(apiErrorFromException(err));
      } finally {
        setUploading(false);
      }
      if (fileInputRef.current) fileInputRef.current.value = "";
    },
    [onDeployStarted, recordHistory],
  );

  return (
    <div className="launcher-overlay">
      <div className="launcher-shell">
        <header className="launcher-head">
          <h1>Sessions</h1>
          <RecordHistoryToggle
            className="launcher-record-history"
            checked={recordHistory}
            onChange={setRecordHistory}
          />
          {onClose && <IconButton icon="x" label="Close (Esc)" onClick={onClose} />}
        </header>
        {systemNotice && <div className="launcher-warning">{systemNotice}</div>}

        <div className="launcher-body">
          <nav className="launcher-rail" aria-label="Session source">
            <button className="launcher-rail-btn launcher-rail-btn--active">Launch</button>
            {onOpenBuilder && (
              <button className="launcher-rail-btn" onClick={onOpenBuilder}>
                Session Builder
              </button>
            )}
          </nav>

          <section className="launcher-content" aria-label="Launch a session">
            <p className="launcher-hint">
              Sessions in your current catalog scope, separated by ownership and deployed as
              authored. Use the Session Builder to create your own sessions.
            </p>
            {sessionsError && (
              <div className="launcher-error" role="alert">
                Sessions did not load from VS-API: {sessionsError}
              </div>
            )}
            {launchError && (
              <div className="launcher-error" role="alert">
                {launchError}
              </div>
            )}
            <div className="launcher-sessions">
              {sessionGroups.map((group) => group.sessions.length > 0 && (
                <section
                  key={group.source}
                  className="launcher-session-group"
                  aria-label={group.label}
                >
                  <header className="launcher-session-group-head">
                    <span>
                      <span className="launcher-session-group-title">{group.label}</span>
                      <span className="launcher-session-group-detail">{group.description}</span>
                    </span>
                    <Badge tone={group.source === "user" ? "accent" : "neutral"}>
                      {group.source === "user" ? "user: editable" : "nodalarc: read-only"}
                    </Badge>
                  </header>
                  <div className="launcher-session-group-rows">
                    {group.sessions.map((s) => (
                      <div key={s.source_id.session_ref} className="launcher-row-shell">
                        <button
                          className="launcher-row"
                          onClick={async () => {
                            if (s.active || deploying || !s.deploy_allowed) return;
                            setLaunchError(null);
                            const result = await onLaunchSession(s, recordHistory);
                            if (result.ok) {
                              onDeployStarted();
                            } else {
                              setLaunchError(result.message);
                            }
                          }}
                          disabled={s.active || deploying || !s.deploy_allowed}
                          title={
                            s.active
                              ? `${s.source_id.session_ref} is already running`
                              : s.deploy_allowed
                                ? `Deploy ${s.source_id.session_ref}`
                                : s.blockers?.[0]?.message ?? "This session cannot be deployed"
                          }
                        >
                          <span className="launcher-row-identity">
                            <span className="launcher-row-name">{s.name}</span>
                            <span className="launcher-row-ref">{s.source_id.session_ref}</span>
                          </span>
                          <span className="launcher-row-desc">{s.constellation}</span>
                          <span className="launcher-row-meta">
                            {s.routing_stack && <Badge>{s.routing_stack}</Badge>}
                            {s.active && <Badge tone="ok">running</Badge>}
                            {!s.deploy_allowed && <Badge>unavailable</Badge>}
                          </span>
                        </button>
                        <IconButton
                          icon="download"
                          label={`Download ${s.source_id.session_ref} YAML`}
                          disabled={downloadingFile !== null}
                          onClick={() => void downloadSessionYaml(s.source_id, s.name)}
                        />
                      </div>
                    ))}
                  </div>
                </section>
              ))}
              {downloadError && <div className="launcher-error">{downloadError}</div>}
              {sessions.length === 0 && (
                <div className="launcher-empty">No catalog sessions found.</div>
              )}
            </div>
            <div className="launcher-upload">
              <Button
                icon="download"
                onClick={() => fileInputRef.current?.click()}
                disabled={deploying || uploading}
              >
                Import and deploy session YAML…
              </Button>
              <input
                ref={fileInputRef}
                type="file"
                accept=".yaml,.yml"
                onChange={handleUpload}
                className="launcher-upload-input"
                aria-label="Session YAML file"
              />
              <span className="launcher-hint">
                Referenced components must already exist in your catalog. Import all referenced
                user: component YAML files in Session Builder.
              </span>
            </div>
            {uploadError && <div className="launcher-error">{uploadError}</div>}
          </section>
        </div>
      </div>
    </div>
  );
}
