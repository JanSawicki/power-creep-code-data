# Power-creep research code and data

This repository contains research scripts, job templates, and tests. The `data/` directory contains the frozen study inputs, results, annotations,
source audit, and reproduction scripts. Runtime logs, credentials, endpoint files,
and private author configuration are excluded.

## Private configuration

Keep configuration outside this checkout, by default in
`~/.config/power-creep/` (or `$XDG_CONFIG_HOME/power-creep/`).

Create `local.env` with shell exports appropriate to your machine:

```sh
export POWER_CREEP_SLURM_ACCOUNT=your_account
export POWER_CREEP_SLURM_PARTITION=your_partition
export POWER_CREEP_PYTHON=/path/to/python
export HF_HOME=/path/to/model/cache
```

Submit jobs from the repository root with
`bash scripts/submit_job.sh jobs/detect.job`. The wrapper passes account and
partition settings to SLURM. Jobs also load this private configuration at runtime.
Set `POWER_CREEP_CONFIG` to use another configuration file. Without configuration,
Python defaults to `python3` and scheduler settings use cluster defaults.
For direct Python commands, first source your private `local.env` as needed.

For `scripts/build_paper_latex.py --refresh-source`, create a private `author.json`
containing string fields `running_head`, `name`, `affiliation`, `correspondence`,
and `email`. Set `POWER_CREEP_AUTHOR_CONFIG` to override its location. Existing
LaTeX sources can be compiled without this file by omitting `--refresh-source`.

Runtime endpoints are discovered dynamically or supplied with `--llm-url` or
`VLLM_LLM_URL`; generated `vllm.endpoints` files remain untracked.

## Study data and reproduction

The publication snapshot includes the 7,760 retained card states, original and
retained detector outputs, the eligible 145-pair review queue and append-only
human decisions, 117 accepted pairs, 89 conditional acquisition scenarios,
and the source-linked acquisition audit. See [data/README.md](data/README.md)
for provenance, exclusions, assumptions, and limitations.

Raw source card definitions are available through [HearthstoneJSON](https://hearthstonejson.com/)
and [hsdata](https://github.com/HearthSim/hsdata). The frozen analyzed inputs
preserve the representation actually used, independently of future API changes.
Third-party card text and source excerpts remain attributable to their owners;
the acquisition audit includes links and hashes, rather than raw wiki HTML.

Install dependencies with `python -m pip install -r requirements.txt`, then run:

```sh
python data/human_review/recompute_review.py --output-dir /tmp/power-creep-human --no-plots
python data/reproduce_package.py --output-dir /tmp/power-creep-detector
```

This repository is published as a single-commit code-and-data snapshot.
