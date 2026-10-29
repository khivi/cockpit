# Ports — behavior spec

- [ports.holder~1] Given a repo declaring `ports`, `cockpit ports` names each
  port's holder. A port a docker container publishes names the container. A
  listener whose cwd is inside a configured worktree names that worktree,
  the deepest one when worktrees nest. Any other listener names its command
  and pid. A free port says free.
- [ports.exit~1] `cockpit ports` exits 0 when every declared port is free and
  1 when any is held. When lsof cannot be asked it exits 2 and never reports
  a port free.
- [ports.config~1] A repo `ports` that is not a list of port numbers fails
  config validation.
