"""Cross-tool orchestrators.

`lib/` holds wrappers around a single tool (git, cmux, gh, …). Modules in
this package compose those wrappers into multi-step pipelines that span
several tools and where step ordering matters.

  - orchestrators.teardown — workspace close → worktree remove → cache delete
"""
