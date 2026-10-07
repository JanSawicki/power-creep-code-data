#!/bin/bash
# Load private scheduler settings before submitting (SBATCH directives cannot expand variables).
set -euo pipefail
CONFIG="${POWER_CREEP_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/power-creep/local.env}"
if [[ -f "$CONFIG" ]]; then source "$CONFIG"; fi
OPTIONS=()
if [[ -n "${POWER_CREEP_SLURM_ACCOUNT:-}" ]]; then
    OPTIONS+=(--account "$POWER_CREEP_SLURM_ACCOUNT")
fi
if [[ -n "${POWER_CREEP_SLURM_PARTITION:-}" ]]; then
    OPTIONS+=(--partition "$POWER_CREEP_SLURM_PARTITION")
fi
exec sbatch "${OPTIONS[@]}" "$@"
