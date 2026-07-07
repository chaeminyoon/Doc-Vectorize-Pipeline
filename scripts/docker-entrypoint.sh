#!/bin/sh
set -eu

warn() {
    echo "[WARN] $*" >&2
}

die() {
    echo "[ERROR] $*" >&2
    exit 1
}

is_true() {
    case "${1:-}" in
        1|true|TRUE|yes|YES|on|ON)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

if is_true "${HF_HUB_OFFLINE:-1}"; then
    if [ -n "${EMBEDDING_MODEL:-}" ] && [ ! -e "${EMBEDDING_MODEL}" ]; then
        warn "EMBEDDING_MODEL '${EMBEDDING_MODEL}' was not found in the container. Offline mode requires a mounted local model path or a pre-populated Hugging Face cache."
    fi
fi

if is_true "${CONVERT_HWPX:-false}"; then
    if [ -z "${SOFFICE_PATH:-}" ]; then
        die "CONVERT_HWPX=true but SOFFICE_PATH is empty."
    fi
    if [ ! -f "${SOFFICE_PATH}" ]; then
        die "CONVERT_HWPX=true but SOFFICE_PATH '${SOFFICE_PATH}' does not exist."
    fi
fi

if [ -n "${METADATA_DOC_LIST:-}" ] && [ ! -e "${METADATA_DOC_LIST}" ]; then
    warn "METADATA_DOC_LIST '${METADATA_DOC_LIST}' was not found. Commands that load metadata may fail."
fi

exec python main.py "$@"
