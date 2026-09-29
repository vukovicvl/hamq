#!/usr/bin/env bash
# Run the QGIS integration tests (tests/qgis) on the local QGIS and in Docker.
#
# Usage: scripts/test_qgis.sh <local|3.44|4.0|3.34|all> [extra pytest args]
#
#   local  host QGIS 4.x (Qt6); QGIS Python from $QGIS_PYTHONPATH
#          (default /usr/share/qgis/python:/usr/share/qgis/python/plugins)
#   3.44   Docker qgis/qgis:3.44-trixie        QGIS 3.44, Qt5
#   4.0    Docker qgis/qgis:4.0-trixie         QGIS 4.0, Qt6
#   3.34   Docker camptocamp/qgis-server:3.34  QGIS 3.34, Qt5 (minimum supported
#          version; the server image lacks pytest and the PyYAML / psycopg2
#          modules of the processing plugin: they are installed into /tmp/pylib
#          inside the container; tests that need a module the image lacks,
#          e.g. QtSvg, are skipped)
#   all    every target above, then a summary table; exit status 1 if any failed
#
# Docker targets mount the repository read-only, run as the calling user with
# HOME=/tmp, PYTHONDONTWRITEBYTECODE=1 and -p no:cacheprovider, so no files are
# written into the repository. Qt runs with the offscreen platform (no X server).
#
# Examples:
#   scripts/test_qgis.sh local -k compat
#   scripts/test_qgis.sh 3.44 tests/qgis/test_plugin_load.py -x

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
QGIS_PYTHONPATH="${QGIS_PYTHONPATH:-/usr/share/qgis/python:/usr/share/qgis/python/plugins}"
# pytest for the 3.34 server image, plus what its processing plugin imports
# (desktop QGIS ships them, the server image does not)
PIP_PACKAGES_3_34="${PIP_PACKAGES_3_34:-pytest==8.3.5 PyYAML==6.0.2 psycopg2-binary==2.9.10}"
TARGETS=(local 3.44 4.0 3.34)

usage() {
    # the comment block at the top of this file
    awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
}

image_for() {
    case "$1" in
        3.44) echo "qgis/qgis:3.44-trixie" ;;
        4.0) echo "qgis/qgis:4.0-trixie" ;;
        3.34) echo "camptocamp/qgis-server:3.34" ;;
        *) return 1 ;;
    esac
}

run_local() {
    if ! PYTHONPATH="$QGIS_PYTHONPATH" "$PYTHON" -c "import qgis.core" >/dev/null 2>&1; then
        echo "test_qgis.sh: no QGIS Python bindings in $QGIS_PYTHONPATH (set QGIS_PYTHONPATH)" >&2
        return 2
    fi
    (
        cd "$ROOT" || exit 2
        PYTHONPATH="$QGIS_PYTHONPATH${PYTHONPATH:+:$PYTHONPATH}" QT_QPA_PLATFORM=offscreen \
            "$PYTHON" -m pytest tests/qgis "$@"
    )
}

run_docker() {
    local target="$1"
    shift
    local image
    image="$(image_for "$target")" || return 2
    if ! command -v docker >/dev/null 2>&1; then
        echo "test_qgis.sh: docker is not installed" >&2
        return 2
    fi
    local setup=""
    if [ "$target" = "3.34" ]; then
        # Install what the server image lacks into a throw-away directory.
        setup="python3 -m pip install --quiet --disable-pip-version-check --no-warn-script-location \
--target /tmp/pylib $PIP_PACKAGES_3_34 && export PYTHONPATH=/tmp/pylib:\${PYTHONPATH:-} && "
    fi
    docker run --rm \
        --user "$(id -u):$(id -g)" \
        -e HOME=/tmp \
        -e PYTHONDONTWRITEBYTECODE=1 \
        -e QT_QPA_PLATFORM=offscreen \
        -e XDG_RUNTIME_DIR=/tmp/xdg \
        -v "$ROOT":/app:ro \
        -w /app \
        "$image" \
        sh -c "mkdir -p /tmp/xdg && chmod 700 /tmp/xdg && ${setup}exec python3 -m pytest -p no:cacheprovider tests/qgis \"\$@\"" \
        sh "$@"
}

run_target() {
    local target="$1"
    shift
    case "$target" in
        local) run_local "$@" ;;
        3.44 | 4.0 | 3.34) run_docker "$target" "$@" ;;
        *)
            echo "test_qgis.sh: unknown target '$target'" >&2
            usage >&2
            return 2
            ;;
    esac
}

run_all() {
    local log_dir
    log_dir="$(mktemp -d "${TMPDIR:-/tmp}/hamq-test-qgis.XXXXXX")"
    local -a results=() summaries=() durations=()
    local failed=0 target status start summary
    for target in "${TARGETS[@]}"; do
        echo "=== $target ==="
        start=$(date +%s)
        run_target "$target" "$@" 2>&1 | tee "$log_dir/$target.log"
        status=${PIPESTATUS[0]}
        durations+=("$(($(date +%s) - start))s")
        summary="$(grep -E '(passed|failed|error|skipped|no tests ran|deselected)' "$log_dir/$target.log" \
            | grep -E ' in [0-9.]+s' | tail -n 1 | sed -E 's/=+//g; s/^ +| +$//g')"
        summaries+=("${summary:-no pytest summary (see $log_dir/$target.log)}")
        if [ "$status" -eq 0 ]; then
            results+=("PASS")
        else
            results+=("FAIL($status)")
            failed=1
        fi
    done
    echo
    echo "=== summary ==="
    printf '%-7s %-30s %-9s %-7s %s\n' "target" "environment" "result" "time" "pytest"
    local i environment
    for i in "${!TARGETS[@]}"; do
        target="${TARGETS[$i]}"
        if [ "$target" = "local" ]; then
            environment="host QGIS"
        else
            environment="$(image_for "$target")"
        fi
        printf '%-7s %-30s %-9s %-7s %s\n' "$target" "$environment" "${results[$i]}" \
            "${durations[$i]}" "${summaries[$i]}"
    done
    if [ "$failed" -eq 0 ]; then
        rm -rf "$log_dir"
    else
        echo "logs: $log_dir"
    fi
    return "$failed"
}

main() {
    if [ $# -lt 1 ]; then
        usage >&2
        exit 2
    fi
    local target="$1"
    shift
    case "$target" in
        -h | --help) usage ;;
        all) run_all "$@" ;;
        *) run_target "$target" "$@" ;;
    esac
}

main "$@"
