import type { ConsoleStateSnapshot } from "../types";
import "../styles/bars.css";

interface Props {
    consoleState: ConsoleStateSnapshot | null;
    onRecompute: () => void;
    recomputing: boolean;
}

export function TopBar({ consoleState, onRecompute, recomputing }: Props) {
    const session = consoleState?.session_path?.split("/").pop() ?? "\u2014";
    const transport = consoleState?.transport ?? "\u2014";
    const dryRun = consoleState?.dry_run ?? false;

    return (
        <header className="top-bar">
            <span className="brand">NodalPath</span>
            <span className="top-bar-meta">{session}</span>
            <span className="top-bar-meta">{transport}{dryRun ? " \u00b7 dry-run" : ""}</span>
            <div className="spacer" />
            <span className="status-pill status-live">
                <span className="dot" />LIVE
            </span>
            <button
                className={`recompute-btn ${recomputing ? "working" : ""}`}
                onClick={onRecompute}
                disabled={recomputing}
            >
                {recomputing ? "Requesting\u2026" : "Recompute"}
            </button>
        </header>
    );
}
