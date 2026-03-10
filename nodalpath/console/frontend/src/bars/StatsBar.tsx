import type { ConsoleStateSnapshot } from "../types";
import "../styles/bars.css";

interface Props {
    consoleState: ConsoleStateSnapshot | null;
}

export function StatsBar({ consoleState }: Props) {
    const s = consoleState;
    const devClass = (s?.deviation_count ?? 0) > 0 ? "warn" : "accent";
    const lastSim = s?.last_sim_time?.substring(11, 19) ?? "\u2014";

    return (
        <div className="stats-bar">
            <Stat label="Nodes"       value={s?.nodes_in_registry ?? "\u2014"} cls="accent" />
            <Stat label="Transitions" value={s?.transition_count ?? "\u2014"} />
            <Stat label="Pushes"      value={s?.push_history?.length ?? "\u2014"} cls="accent" />
            <Stat label="Deviations"  value={s?.deviation_count ?? "\u2014"} cls={devClass} />
            <Stat label="Recomputes"  value={s?.recomputation_count ?? "\u2014"} />
            <Stat label="Last Sim"    value={lastSim} cls="accent" small />
        </div>
    );
}

function Stat({ label, value, cls = "", small = false }: {
    label: string; value: unknown; cls?: string; small?: boolean;
}) {
    return (
        <div className="stat-cell">
            <div className="stat-label">{label}</div>
            <div className={`stat-value ${cls} ${small ? "stat-small" : ""}`}>{String(value)}</div>
        </div>
    );
}
