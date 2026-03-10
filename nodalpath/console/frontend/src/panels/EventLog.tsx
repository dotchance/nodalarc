import type { EventRecord } from "../types";
import "../styles/panels.css";

const TYPE_CLASS: Record<string, string> = {
    TRANSITION: "ev-transition",
    PUSH:       "ev-push",
    DEVIATE:    "ev-deviate",
    RECOMPUTE:  "ev-recompute",
};

interface Props {
    events: EventRecord[];
    maxRows?: number;
}

export function EventLog({ events, maxRows = 60 }: Props) {
    return (
        <div className="event-log">
            <div className="event-log-header">Event Log</div>
            <div className="event-log-body">
                {events.length === 0
                    ? <div className="event-log-empty">No events yet.</div>
                    : events.slice(0, maxRows).map((ev, i) => (
                        <div key={i} className="event-row">
                            <span className="ev-ts">{ev.wall_time.substring(11, 19)}</span>
                            <span className={`ev-badge ${TYPE_CLASS[ev.event_type] ?? ""}`}>
                                {ev.event_type}
                            </span>
                            <span className="ev-summary">{ev.summary}</span>
                        </div>
                    ))
                }
            </div>
        </div>
    );
}
