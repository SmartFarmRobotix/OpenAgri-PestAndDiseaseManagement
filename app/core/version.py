"""
PDM's own application version. Independent of the BRVCBR ruleset version
(`app/pest_models/*.json`'s own "version" field, exposed via
`init.pest_model_seed.ruleset_version_by_name`) — this identifies the PDM
app/engine build itself, not any one ruleset it happens to be running.
Bumped by hand on notable PDM-side changes; not derived from git.
"""

PDM_APP_VERSION = "0.2.0"
