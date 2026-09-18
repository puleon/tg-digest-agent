# Prompts

Every prompt is a versioned file: `<name>.v<N>.md` (system/user templates, `{placeholders}`
filled with `str.format`) and, for few-shot prompts, `<name>.examples.v<N>.yaml`. The version is
part of `enrichment.model_version`, so two ingest runs are comparable. Prompts are never edited
in place — bump the version. From D9 on they are also registered in Langfuse prompt management.
