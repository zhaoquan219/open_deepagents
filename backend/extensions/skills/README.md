# Sample Skills

This directory is the default project-local skill source for the backend.

DeepAgents discovers skills by scanning subdirectories under this source and
loading `SKILL.md` from each folder:

```text
extensions/skills/
  my-skill/
    SKILL.md
    helper.py
```

`DEEPAGENTS_SKILLS=extensions/skills` is resolved by the backend to the
DeepAgents-visible source path `/extensions/skills/`, and that source is routed
to this on-disk directory regardless of which sandbox backend is active.
