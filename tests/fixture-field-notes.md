# Field notes — known failure signatures and the cause that almost always explains them

The library the harness appends to a FAILED tool result whose text matches one of these
signatures, so the model is handed the cause instead of re-deriving it.

ONE entry per signature. `match:` is a comma-separated list of regexes (case-insensitive,
`re.search`); `scope:` is `any`, `linux`, `windows` or `macos` — a scoped entry fires only
on that platform; `note:` is what the model reads. Keep it small and keep it honest: a
note that guesses wrong costs more steps than no note at all, so an ambiguous signature
(a bare "Access is denied") must not be in here.

This file is the TEST fixture (tests/test_digest.py): the real library is the operator's
own `field-notes.md`, gitignored per host. Same shape, one entry per signature the suite
asserts — that is what makes the parsing, the scope filter and the cap gradable in a clone.

## dpkg/apt: another package tool holds the lock
match: could not get lock /var/lib/dpkg/lock, unable to acquire the dpkg frontend lock, is another process using it
scope: linux
note: Another apt/dpkg run (or unattended-upgrades) still holds it. Find the holder with `fuser -v /var/lib/dpkg/lock-frontend`, wait for it, or stop the unit that owns it, then retry. Never delete the lock file.
source: measured on a Debian install, 2026-09-13

## cp: a path with a space was split into words
match: cp: .*no such file or directory, cp: target .* is not a directory, cp: .*omitting directory
scope: any
note: A shell word with a space was split before cp saw it, so cp is looking for two paths. Quote each one ("$src" "$dst"), or pass argv instead of a command string.
source: hand-rolled shell that forgot to quote, 2026-09-13

## PowerShell: the name is not on PATH
match: is not recognized as the name of a cmdlet, the term ' is not recognized
scope: windows
note: The name is not on THIS shell's PATH. Use the full path, or check what PATH the child really got: a Scheduled Task, a service and an interactive console each start with a different one.
source: a live Windows install, where the task's PATH had no winget directory

## Windows: the action needs elevation and this shell holds the filtered token
match: requires elevation, must be run as administrator, requested operation requires elevation
scope: windows
note: An administrator's console gets the filtered UAC token, so "my account is admin" is not "this shell can do admin work". Start an elevated shell (Run as administrator) and run it there; do not retry here.
source: console session reporting a refusal as a mystery, 2026-09-13

## sudo: a password is required
match: sudo: a password is required, sudo: no tty present, sudo: a terminal is required
scope: any
note: sudo has no terminal to prompt on, or no passwordless rule for this command. Run it from the console lane, or add a NOPASSWD rule for the exact command; do not pipe a password.
source: POSIX door without a tty

## docker: cannot connect to the daemon
match: cannot connect to the docker daemon, is the docker daemon running, docker: command not found
scope: any
note: The client is here and the daemon is not. Start it (`systemctl start docker`, `launchctl kickstart` for Docker Desktop's socket, or start Docker Desktop), or point DOCKER_HOST at the socket that is actually listening.
source: a live install whose daemon was disabled after an upgrade

## git: the repository is owned by another user
match: dubious ownership in repository, detected dubious ownership
scope: any
note: git refuses a repo whose owner is not this user. Add it once with `git config --global --add safe.directory <path>` rather than chowning the tree.
source: a live install, root-owned checkout, git 2.35.2

## the filesystem is full
match: no space left on device, disk quota exceeded, cannot write: no space
scope: any
note: The write failed because the target filesystem is full or the quota is spent. Report the mount that is full (`df -h <dir>`) and what is consuming it; do not retry the same write.
source: measured during the ENOSPC probe

## a port is already held
match: address already in use, bind: permission denied
scope: any
note: Something is already listening, or this process may not bind that port (<1024 needs the privilege). Name the holder, then pick another port or stop the holder; do not restart into the same address.
source: two instances fighting over the web port

## Python: the module is not installed in this interpreter
match: modulenotfounderror: no module named, no module named '
scope: any
note: The import is not in THIS interpreter's site-packages (a different python on PATH is the usual cause). Install into the venv the service runs from, and name that interpreter's absolute path.
source: measured on macOS, where "python" is not the service's interpreter

## ssh: the key does not authenticate
match: permission denied \(publickey\), could not resolve hostname
scope: any
note: The server rejected the key (or the name does not resolve). Check which key the call offers with `ssh -v`, that the key is in the server's authorized_keys, and the real host name before retrying.
source: jump host after a key rotation

## an archive is not the format its name claims
match: not in gzip format, unzip: cannot find, gzip: stdin: unexpected end of file
scope: any
note: The download is truncated or is not the format the name says (an HTML error page saved as .tar.gz looks exactly like this). Print the first bytes and the size, then re-fetch from the source.
source: measured on a mirrored release asset
