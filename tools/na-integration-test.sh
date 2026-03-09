#!/bin/bash
# Nodal Arc integration test: deploy-verify-teardown loop.
# 7-step verification per session, cycling through all sessions.
# After final cycle, deploys iridium-small-36-isis-flat and leaves it running.
#
# Usage: na-integration-test.sh [--target-cycles N] [--sessions path1,path2,...]
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
UV="${UV:-/home/chance/.local/bin/uv}"
WS_TMP="/tmp/na-integration-ws.json"
DEPLOY_LOG="/tmp/na-integration-deploy.log"

# Clean up temp files from prior runs
rm -f "$DEPLOY_LOG" "$WS_TMP"

# Kill any stale integration test instances
stale_pids=$(pgrep -f "na-integration-test" 2>/dev/null | grep -v "^$$\$" || true)
if [ -n "$stale_pids" ]; then
    echo "Killing stale integration test processes: $stale_pids"
    kill -9 $stale_pids 2>/dev/null || true
    sleep 1
fi

# --- Default sessions ---
DEFAULT_SESSIONS=(
    "configs/sessions/iridium-small-36-isis-flat.yaml"
    "configs/sessions/starlink-early-44-isis-flat.yaml"
    "configs/sessions/kuiper-50-ospf-flat.yaml"
    "configs/sessions/oneweb-60-isis-flat.yaml"
    "configs/sessions/iridium-66-isis-striped.yaml"
)

TARGET_CYCLES=2
SESSIONS=("${DEFAULT_SESSIONS[@]}")

# --- Parse args ---
while [[ $# -gt 0 ]]; do
    case "$1" in
        --target-cycles) TARGET_CYCLES="$2"; shift 2 ;;
        --sessions)
            IFS=',' read -ra SESSIONS <<< "$2"
            shift 2
            ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

NUM_SESSIONS=${#SESSIONS[@]}

# --- Setup logging ---
mkdir -p tests/integration
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
LOG_FILE="$PROJECT_DIR/tests/integration/deploy-loop-${TIMESTAMP}.log"

log() {
    echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

step_header() {
    local cycle="$1" total_cycles="$2" sess_idx="$3" sess_name="$4" step="$5" step_name="$6"
    log "=== CYCLE ${cycle}/${total_cycles} | SESSION ${sess_idx}/${NUM_SESSIONS}: ${sess_name} | STEP ${step}: ${step_name} ==="
}

# --- Host IP ---
HOST_IP=$(hostname -I | awk '{print $1}')
log "Host IP: $HOST_IP"
log "Target cycles: $TARGET_CYCLES"
log "Sessions: ${SESSIONS[*]}"
log "Log file: $LOG_FILE"

# --- Session metadata extraction ---
get_session_meta() {
    local session_file="$1"
    "$UV" run python -c "
import yaml, sys, os
from pathlib import Path
os.chdir('$PROJECT_DIR')
raw = yaml.safe_load(Path('$session_file').read_text())
session_name = raw['session']['name']
constellation_file = raw['constellation']
stack_path = raw['routing']['stack']
craw = yaml.safe_load(Path(constellation_file).read_text())
cname = craw.get('name', '')
mode = craw.get('mode', '')
if mode == 'parametric':
    sat_count = craw['planes']['count'] * craw['planes']['sats_per_plane']
elif mode == 'explicit':
    sat_count = len(craw.get('satellites', []))
else:
    sat_count = 0
gs_ref = raw.get('ground_stations', '')
gs_path = Path(gs_ref)
gs_count = 0
if gs_path.exists():
    graw = yaml.safe_load(gs_path.read_text())
    if 'ground_station_set' in graw:
        gs_count = len(graw['ground_station_set'].get('stations', []))
routing_type = 'isis' if 'isis' in stack_path else ('ospf' if 'ospf' in stack_path else 'unknown')
print(f'{session_name}|{cname}|{sat_count}|{gs_count}|{routing_type}')
" 2>/dev/null
}

# --- Precompute session metadata ---
declare -a SESSION_NAMES
declare -a CONSTELLATION_NAMES
declare -a SAT_COUNTS
declare -a GS_COUNTS
declare -a ROUTING_TYPES

for i in "${!SESSIONS[@]}"; do
    meta=$(get_session_meta "${SESSIONS[$i]}")
    IFS='|' read -r sname cname scount gcount rtype <<< "$meta"
    SESSION_NAMES[$i]="$sname"
    CONSTELLATION_NAMES[$i]="$cname"
    SAT_COUNTS[$i]="$scount"
    GS_COUNTS[$i]="$gcount"
    ROUTING_TYPES[$i]="$rtype"
    log "Session $((i+1)): ${SESSIONS[$i]}"
    log "  name=$sname constellation=$cname sats=$scount gs=$gcount routing=$rtype"
done

# --- Tracking ---
CONSECUTIVE_CLEAN=0
TOTAL_CYCLES=0
TOTAL_DEPLOYS=0
declare -A PASS_COUNT
declare -A FAIL_COUNT
declare -a FAILURES

for s in "${SESSION_NAMES[@]}"; do
    PASS_COUNT["$s"]=0
    FAIL_COUNT["$s"]=0
done

# ============================================================
# STEP FUNCTIONS — 7-step verification per session
# ============================================================

step1_teardown() {
    # Run na-teardown.sh
    "$SCRIPT_DIR/na-teardown.sh" >> "$LOG_FILE" 2>&1
    sleep 2

    # Verify zero nodalarc processes (retry-kill any survivors)
    local PROC_PATTERN="ome\.main|orchestrator\.main|vs_api\.main|measurement\.mi_main|tools\.deploy_daemon|tools\.na_deploy"
    local procs
    procs=$(pgrep -f "$PROC_PATTERN" 2>/dev/null || true)
    if [ -n "$procs" ]; then
        log "Survivors after teardown, force-killing: $procs"
        for p in $procs; do
            sudo kill -9 "$p" 2>/dev/null || true
        done
        sleep 2
        procs=$(pgrep -f "$PROC_PATTERN" 2>/dev/null || true)
        if [ -n "$procs" ]; then
            log "FAIL: Processes still running after double-kill: $procs"
            return 1
        fi
        log "Survivors cleaned up on retry"
    fi

    # Verify zero pods
    local pod_output
    pod_output=$(sudo KUBECONFIG="$KUBECONFIG" kubectl get pods -n nodalarc --no-headers 2>/dev/null || true)
    if [ -n "$pod_output" ]; then
        log "FAIL: Pods still running after teardown:"
        log "$pod_output"
        return 1
    fi

    # Verify ports 3000 and 8080 are free
    for port in 3000 8080; do
        if ss -tlnp 2>/dev/null | grep -q ":${port} "; then
            log "FAIL: Port $port still in use after teardown"
            ss -tlnp 2>/dev/null | grep ":${port} " >> "$LOG_FILE"
            return 1
        fi
    done

    log "Teardown verified: zero processes, zero pods, ports 3000+8080 free"
    return 0
}

step2_deploy() {
    local session_file="$1"
    log "Deploying: $session_file"
    rm -f "$DEPLOY_LOG"
    sudo KUBECONFIG="$KUBECONFIG" "$UV" run python -m tools.na_deploy \
        --session "$session_file" --skip-teardown \
        > "$DEPLOY_LOG" 2>&1
    local rc=$?
    if [ $rc -ne 0 ]; then
        log "FAIL: Deploy exited with code $rc"
        log "Last 50 lines of deploy log:"
        tail -50 "$DEPLOY_LOG" >> "$LOG_FILE"
        return 1
    fi
    log "Deploy completed (exit 0)"

    # Verify both :8080 and :3000 are reachable from HOST_IP
    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" "http://${HOST_IP}:8080/api/v1/health" 2>/dev/null || true)
    if [ "$http_code" != "200" ]; then
        log "FAIL: VS-API not reachable at http://${HOST_IP}:8080 (HTTP $http_code)"
        return 1
    fi
    log "VS-API reachable at http://${HOST_IP}:8080"

    # Wait briefly then check Vite
    sleep 2
    http_code=$(curl -s -o /dev/null -w "%{http_code}" "http://${HOST_IP}:3000/" 2>/dev/null || true)
    if [ "$http_code" != "200" ]; then
        log "FAIL: Vite not reachable at http://${HOST_IP}:3000 (HTTP $http_code)"
        return 1
    fi
    log "Vite reachable at http://${HOST_IP}:3000"
    return 0
}

step3_wait_ready() {
    local elapsed=0
    local max_wait=120
    while [ $elapsed -lt $max_wait ]; do
        local resp
        resp=$(curl -s -w "\n%{http_code}" "http://${HOST_IP}:8080/api/v1/health" 2>/dev/null || true)
        local http_code
        http_code=$(echo "$resp" | tail -1)
        local body
        body=$(echo "$resp" | sed '$d')

        if [ "$http_code" = "200" ]; then
            local session_status
            session_status=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_status',''))" 2>/dev/null || true)
            if [ "$session_status" = "ready" ]; then
                log "Health check passed: session_status=ready (${elapsed}s)"
                return 0
            fi
            if [ $((elapsed % 10)) -eq 0 ]; then
                log "  Health 200 but session_status=$session_status (${elapsed}s)..."
            fi
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done

    log "FAIL: session_status did not reach 'ready' within ${max_wait}s"
    return 1
}

step4_verify_data() {
    local expected_sat_count="$1"
    local expected_constellation="$2"

    # Wait for sim_time to advance before reading snapshots
    log "Waiting for sim_time to advance (up to 180s)..."
    local api_key
    api_key=$(curl -s "http://${HOST_IP}:8080/api/v1/auth/token" \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null || true)
    if [ -z "$api_key" ]; then
        log "FAIL: Could not get API key from token endpoint"
        return 1
    fi

    local stable_sim_time=""
    local prev_sim_time=""
    local elapsed=0
    while [ $elapsed -lt 180 ]; do
        local cur_sim_time
        cur_sim_time=$(curl -s -H "Authorization: Bearer $api_key" \
            "http://${HOST_IP}:8080/api/v1/state" 2>/dev/null \
            | python3 -c "import sys,json; print(json.load(sys.stdin).get('sim_time',''))" 2>/dev/null || true)
        if [ -z "$cur_sim_time" ]; then
            sleep 5
            elapsed=$((elapsed + 5))
            continue
        fi
        if [ -z "$stable_sim_time" ]; then
            if [ "$cur_sim_time" = "$prev_sim_time" ]; then
                stable_sim_time="$cur_sim_time"
                log "First stable sim_time after ${elapsed}s: $stable_sim_time"
            fi
        else
            if [ "$cur_sim_time" != "$stable_sim_time" ]; then
                log "sim_time advanced after ${elapsed}s ($stable_sim_time -> $cur_sim_time)"
                break
            fi
        fi
        prev_sim_time="$cur_sim_time"
        sleep 5
        elapsed=$((elapsed + 5))
    done
    if [ $elapsed -ge 180 ]; then
        log "WARNING: sim_time did not advance within 180s, proceeding anyway"
    fi

    # Read 10 WebSocket snapshots spaced 5s apart (total window ~50s)
    "$UV" run python -m tools.na_integration_verify read_ws \
        "$HOST_IP" 8080 "$api_key" 10 120 5.0 > "$WS_TMP" 2>/dev/null
    local rc=$?
    if [ $rc -ne 0 ]; then
        log "FAIL: WebSocket connection failed"
        log "Result: $(cat "$WS_TMP" 2>/dev/null)"
        return 1
    fi

    # Run 10-point verification on the snapshots
    local verify_output
    verify_output=$("$UV" run python -c "
import json, sys
with open('$WS_TMP') as f:
    data = json.load(f)
snaps = data.get('snapshots', [])
if not snaps:
    print('FAIL: No snapshots collected')
    sys.exit(1)

errors = []
expected_sats = $expected_sat_count
expected_cname = '$expected_constellation'

# Check 1: Each snapshot has nodes array with length > 0
for i, s in enumerate(snaps):
    if len(s.get('nodes', [])) == 0:
        errors.append(f'Check 1: Snapshot {i} has empty nodes array')
        break

# Check 2: Each snapshot has links array
for i, s in enumerate(snaps):
    if 'links' not in s:
        errors.append(f'Check 2: Snapshot {i} missing links array')
        break

# Check 3: sim_time present and ISO-format
for i, s in enumerate(snaps):
    st = s.get('sim_time', '')
    if not st:
        errors.append(f'Check 3: Snapshot {i} missing sim_time')
        break
    try:
        from datetime import datetime
        datetime.fromisoformat(st.replace('Z', '+00:00'))
    except Exception:
        errors.append(f'Check 3: Snapshot {i} sim_time not ISO format: {st}')
        break

# Check 4: sim_time advances across snapshots
sim_times = [s.get('sim_time', '') for s in snaps]
if len(set(sim_times)) < 2:
    errors.append(f'Check 4: sim_time frozen across all {len(snaps)} snapshots: {sim_times[0]}')

# Check 5: At least one node has lat_deg and lon_deg
found_latlon = False
for n in snaps[0].get('nodes', []):
    if n.get('lat_deg') is not None and n.get('lon_deg') is not None:
        found_latlon = True
        break
if not found_latlon:
    errors.append('Check 5: No node has lat_deg and lon_deg')

# Check 6: At least one node has non-zero position
found_nonzero = False
for n in snaps[0].get('nodes', []):
    if n.get('lat_deg', 0) != 0 or n.get('lon_deg', 0) != 0 or n.get('alt_km', 0) != 0:
        found_nonzero = True
        break
if not found_nonzero:
    errors.append('Check 6: All node positions are zero')

# Check 7: Satellite count matches expected
sat_nodes = [n for n in snaps[0].get('nodes', []) if n.get('node_type') == 'satellite']
if len(sat_nodes) != expected_sats:
    errors.append(f'Check 7: Satellite count {len(sat_nodes)} != expected {expected_sats}')

# Check 8: At least one link exists
found_link = False
for s in snaps:
    if len(s.get('links', [])) > 0:
        found_link = True
        break
if not found_link:
    errors.append('Check 8: No links found in any snapshot')

# Check 9: Nodes have node_id field
for n in snaps[0].get('nodes', []):
    if 'node_id' not in n:
        errors.append('Check 9: Node missing node_id field')
        break

# Check 10: Links have node_a and node_b fields
for s in snaps:
    for lnk in s.get('links', []):
        if 'node_a' not in lnk or 'node_b' not in lnk:
            errors.append('Check 10: Link missing node_a/node_b fields')
            break
    if errors and errors[-1].startswith('Check 10'):
        break

if errors:
    for e in errors:
        print(f'  - {e}')
    sys.exit(1)
else:
    print(f'OK: 10/10 checks passed ({len(snaps)} snapshots, {len(sat_nodes)} sats, sim_time advancing)')
    sys.exit(0)
" 2>/dev/null)
    rc=$?
    if [ $rc -ne 0 ]; then
        log "FAIL: Data flow verification:"
        log "$verify_output"
        return 1
    fi
    log "$verify_output"
    return 0
}

step5_introspect() {
    local routing_type="$1"

    local command
    if [ "$routing_type" = "isis" ]; then
        command="show isis neighbor"
    elif [ "$routing_type" = "ospf" ]; then
        command="show ip ospf neighbor"
    else
        log "FAIL: Unknown routing type: $routing_type"
        return 1
    fi

    local node_id="sat-p00s00"

    # Run vtysh directly via kubectl exec
    local output
    output=$(sudo KUBECONFIG="$KUBECONFIG" kubectl exec -n nodalarc "$node_id" -c frr -- \
        vtysh -c "$command" 2>/dev/null || true)

    if [ -z "$output" ]; then
        log "FAIL: vtysh '$command' on $node_id returned empty output"
        return 1
    fi

    # Check for at least one adjacency line (non-header line with content)
    local adj_lines
    adj_lines=$(echo "$output" | grep -cE "sat-|gs-|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+" || true)
    if [ "$adj_lines" -eq 0 ]; then
        log "FAIL: No adjacency found in '$command' output on $node_id"
        log "Output: $output"
        return 1
    fi

    log "Introspect OK: '$command' on $node_id shows $adj_lines adjacency line(s)"
    return 0
}

step6_browser_simulation() {
    # Sub-step 1: curl the VF at :3000 — verify HTML response
    local vf_body
    vf_body=$(curl -s "http://${HOST_IP}:3000/" 2>/dev/null || true)
    if ! echo "$vf_body" | grep -qi "<html\|<!doctype\|<head"; then
        log "FAIL: VF at http://${HOST_IP}:3000/ did not return HTML"
        log "Body (first 200 chars): ${vf_body:0:200}"
        return 1
    fi
    log "VF served HTML at http://${HOST_IP}:3000/"

    # Sub-step 2: Fetch API key from token endpoint (the way VF does it)
    local token_resp
    token_resp=$(curl -s "http://${HOST_IP}:8080/api/v1/auth/token" 2>/dev/null || true)
    local api_key
    api_key=$(echo "$token_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null || true)
    if [ -z "$api_key" ]; then
        log "FAIL: Could not get API key from token endpoint"
        log "Response: $token_resp"
        return 1
    fi
    log "Got API key from token endpoint (${#api_key} chars)"

    # Sub-step 3+4: Connect WS with Origin header, read 3 snapshots, verify
    local origin="http://${HOST_IP}:3000"
    "$UV" run python -m tools.na_integration_verify read_ws_with_origin \
        "$HOST_IP" 8080 "$api_key" 3 30 "$origin" 5.0 > "$WS_TMP" 2>/dev/null
    local rc=$?
    if [ $rc -ne 0 ]; then
        log "FAIL: WebSocket with Origin header failed"
        log "Result: $(cat "$WS_TMP" 2>/dev/null)"
        return 1
    fi

    # Verify snapshots contain nodes and advancing sim_time
    local verify_output
    verify_output=$("$UV" run python -c "
import json, sys
with open('$WS_TMP') as f:
    data = json.load(f)
snaps = data.get('snapshots', [])
if len(snaps) < 3:
    print(f'FAIL: Only got {len(snaps)} snapshots (expected 3)')
    sys.exit(1)
for i, s in enumerate(snaps):
    if len(s.get('nodes', [])) == 0:
        print(f'FAIL: Snapshot {i} from browser-simulated WS has no nodes')
        sys.exit(1)
sim_times = [s.get('sim_time', '') for s in snaps]
if sim_times[0] == sim_times[-1]:
    print(f'FAIL: sim_time frozen in browser-simulated WS: {sim_times[0]}')
    sys.exit(1)
node_count = len(snaps[0]['nodes'])
print(f'OK: Browser simulation passed — 3 snapshots via Origin-header WS, {node_count} nodes, sim_time advancing')
sys.exit(0)
" 2>/dev/null)
    rc=$?
    if [ $rc -ne 0 ]; then
        log "FAIL: Browser simulation verification:"
        log "$verify_output"
        return 1
    fi
    log "$verify_output"
    return 0
}

step7_teardown() {
    # Same as step1 — clean teardown and verify
    step1_teardown
}

# ============================================================
# MAIN LOOP
# ============================================================
log ""
log "=========================================="
log "  NODAL ARC INTEGRATION TEST"
log "=========================================="
log ""

while [ "$CONSECUTIVE_CLEAN" -lt "$TARGET_CYCLES" ]; do
    TOTAL_CYCLES=$((TOTAL_CYCLES + 1))
    CYCLE_DISPLAY=$((CONSECUTIVE_CLEAN + 1))
    cycle_failed=0

    log ""
    log "########## CYCLE $CYCLE_DISPLAY (attempt $TOTAL_CYCLES) ##########"
    log ""

    for i in "${!SESSIONS[@]}"; do
        session_file="${SESSIONS[$i]}"
        sname="${SESSION_NAMES[$i]}"
        cname="${CONSTELLATION_NAMES[$i]}"
        sat_count="${SAT_COUNTS[$i]}"
        gs_count="${GS_COUNTS[$i]}"
        rtype="${ROUTING_TYPES[$i]}"
        sess_idx=$((i + 1))

        TOTAL_DEPLOYS=$((TOTAL_DEPLOYS + 1))

        # --- Step 1: Clean teardown ---
        step_header "$CYCLE_DISPLAY" "$TARGET_CYCLES" "$sess_idx" "$sname" 1 "teardown"
        if ! step1_teardown; then
            log "FAIL: Step 1 teardown for $sname"
            FAIL_COUNT["$sname"]=$((FAIL_COUNT["$sname"] + 1))
            FAILURES+=("cycle=$TOTAL_CYCLES session=$sname step=1 reason=teardown_incomplete")
            cycle_failed=1
            break
        fi

        # --- Step 2: Deploy from scratch ---
        step_header "$CYCLE_DISPLAY" "$TARGET_CYCLES" "$sess_idx" "$sname" 2 "deploy"
        if ! step2_deploy "$session_file"; then
            log "FAIL: Step 2 deploy for $sname"
            FAIL_COUNT["$sname"]=$((FAIL_COUNT["$sname"] + 1))
            FAILURES+=("cycle=$TOTAL_CYCLES session=$sname step=2 reason=deploy_failed")
            cycle_failed=1
            break
        fi

        # --- Step 3: Wait for ready ---
        step_header "$CYCLE_DISPLAY" "$TARGET_CYCLES" "$sess_idx" "$sname" 3 "wait ready"
        if ! step3_wait_ready; then
            log "FAIL: Step 3 wait ready for $sname"
            FAIL_COUNT["$sname"]=$((FAIL_COUNT["$sname"] + 1))
            FAILURES+=("cycle=$TOTAL_CYCLES session=$sname step=3 reason=health_not_ready")
            cycle_failed=1
            break
        fi

        # --- Step 4: Verify data flow ---
        step_header "$CYCLE_DISPLAY" "$TARGET_CYCLES" "$sess_idx" "$sname" 4 "verify data"
        if ! step4_verify_data "$sat_count" "$cname"; then
            log "FAIL: Step 4 verify data for $sname"
            FAIL_COUNT["$sname"]=$((FAIL_COUNT["$sname"] + 1))
            FAILURES+=("cycle=$TOTAL_CYCLES session=$sname step=4 reason=data_flow_failed")
            cycle_failed=1
            break
        fi

        # --- Step 5: CLI introspect ---
        step_header "$CYCLE_DISPLAY" "$TARGET_CYCLES" "$sess_idx" "$sname" 5 "introspect"
        if ! step5_introspect "$rtype"; then
            log "FAIL: Step 5 introspect for $sname"
            FAIL_COUNT["$sname"]=$((FAIL_COUNT["$sname"] + 1))
            FAILURES+=("cycle=$TOTAL_CYCLES session=$sname step=5 reason=introspect_failed")
            cycle_failed=1
            break
        fi

        # --- Step 6: Browser simulation ---
        step_header "$CYCLE_DISPLAY" "$TARGET_CYCLES" "$sess_idx" "$sname" 6 "browser simulation"
        if ! step6_browser_simulation; then
            log "FAIL: Step 6 browser simulation for $sname"
            FAIL_COUNT["$sname"]=$((FAIL_COUNT["$sname"] + 1))
            FAILURES+=("cycle=$TOTAL_CYCLES session=$sname step=6 reason=browser_simulation_failed")
            cycle_failed=1
            break
        fi

        # --- Step 7: Clean teardown ---
        step_header "$CYCLE_DISPLAY" "$TARGET_CYCLES" "$sess_idx" "$sname" 7 "final teardown"
        if ! step7_teardown; then
            log "FAIL: Step 7 final teardown for $sname"
            FAIL_COUNT["$sname"]=$((FAIL_COUNT["$sname"] + 1))
            FAILURES+=("cycle=$TOTAL_CYCLES session=$sname step=7 reason=final_teardown_failed")
            cycle_failed=1
            break
        fi

        # --- Record PASS ---
        log "PASS: $sname (all 7 steps)"
        PASS_COUNT["$sname"]=$((PASS_COUNT["$sname"] + 1))
    done

    if [ $cycle_failed -eq 0 ]; then
        CONSECUTIVE_CLEAN=$((CONSECUTIVE_CLEAN + 1))
        log ""
        log "*** CYCLE $CYCLE_DISPLAY CLEAN ($CONSECUTIVE_CLEAN/$TARGET_CYCLES consecutive) ***"
    else
        CONSECUTIVE_CLEAN=0
        log ""
        log "*** CYCLE FAILED — resetting consecutive counter to 0 ***"
    fi
done

# ============================================================
# FINAL DEPLOY — leave iridium-small-36-isis-flat running
# ============================================================
log ""
log "=========================================="
log "  FINAL DEPLOY: iridium-small-36-isis-flat"
log "=========================================="
FINAL_SESSION="configs/sessions/iridium-small-36-isis-flat.yaml"
log "Deploying $FINAL_SESSION and leaving it running for browser verification..."

"$SCRIPT_DIR/na-teardown.sh" >> "$LOG_FILE" 2>&1
sleep 2

sudo KUBECONFIG="$KUBECONFIG" "$UV" run python -m tools.na_deploy \
    --session "$FINAL_SESSION" --skip-teardown \
    >> "$LOG_FILE" 2>&1
final_rc=$?
if [ $final_rc -ne 0 ]; then
    log "WARNING: Final deploy exited with code $final_rc"
else
    # Wait for ready
    local_elapsed=0
    while [ $local_elapsed -lt 120 ]; do
        local_status=$(curl -s "http://${HOST_IP}:8080/api/v1/health" 2>/dev/null \
            | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_status',''))" 2>/dev/null || true)
        if [ "$local_status" = "ready" ]; then
            break
        fi
        sleep 2
        local_elapsed=$((local_elapsed + 2))
    done
    log "Final deploy: session_status=$local_status"
    log "VF available at: http://${HOST_IP}:3000/"
    log "VS-API available at: http://${HOST_IP}:8080/"
fi

# ============================================================
# SUMMARY
# ============================================================
log ""
log "=========================================="
log "  INTEGRATION TEST SUMMARY"
log "=========================================="
log "Total cycles attempted: $TOTAL_CYCLES"
log "Total session deploys: $TOTAL_DEPLOYS"
log "Consecutive clean cycles achieved: $CONSECUTIVE_CLEAN / $TARGET_CYCLES"
log ""
log "Per-session results:"
for i in "${!SESSIONS[@]}"; do
    sname="${SESSION_NAMES[$i]}"
    log "  $sname: ${PASS_COUNT[$sname]} pass, ${FAIL_COUNT[$sname]} fail"
done
log ""
if [ "${#FAILURES[@]:-0}" -gt 0 ]; then
    log "Failures:"
    for f in "${FAILURES[@]}"; do
        log "  $f"
    done
fi
log ""

# Cleanup temp file
rm -f "$WS_TMP"

if [ "$CONSECUTIVE_CLEAN" -ge "$TARGET_CYCLES" ]; then
    log "RESULT: PASS ($TARGET_CYCLES consecutive clean cycles)"
    log "Final deploy (iridium-small-36-isis-flat) LEFT RUNNING for browser verification"
    exit 0
else
    log "RESULT: FAIL (only $CONSECUTIVE_CLEAN consecutive clean cycles)"
    exit 1
fi
