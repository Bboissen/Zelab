# Map this server into the vault

This is the team's first ticket on this server. Make a map of the server that any member,
bot or person can use without logging in to it. Write it in the vault folder `Server/`.

## Split

- **sysadmin, the host:**
  - OS and kernel, CPU, memory, disks and their health;
  - mounts, NAS shares included, with options and who owns the files;
  - network interfaces and what each port is exposed to (host firewall and Docker's own rules);
  - systemd services and timers;
  - users with sudo;
  - backups: what is backed up, where, and when a restore was last tried.
- **devops, what runs:**
  - every container and Compose project: what it is for, where its compose file lives, the
    image and version;
  - published ports, volumes and bind mounts, and where each service keeps its data;
  - health checks and restart policy;
  - where its logs go.

  Use `docker compose ls`, `docker ps` and `docker inspect`.
- **pm:** split the work into two child tickets, check the results against the deliverables
  below, and write the overview.

## Rules

- **Read-only.** This ticket changes nothing on the server. Anything that looks wrong goes into
  `Server/Findings.md`, with the evidence; it is not fixed here.
- **No secrets in notes.** `docker inspect` shows environment values: copy variable names,
  never values. The same goes for tokens, passwords, keys and private URLs.
- **Every fact comes from a command you ran.** Name the command. Anything you could not check
  goes in a NOT VERIFIED list.

## Deliverables

- `Server/Infrastructure.md`:
  - host summary, storage and mounts, network and exposure;
  - a services table: name, purpose, where it is defined, ports, data location, owning role
    (devops or sysadmin).
- `Server/Runbooks/<service>.md`, one per service:
  - how to check it is healthy and where its logs are;
  - a safe restart and how to roll back;
  - known pitfalls (for example, SQLite databases must not live on network storage).
- `Server/Findings.md`: risks seen, ranked, each with its evidence and a suggested next step.
- `Server/Decisions.md`: an empty decision log with a short header.

Close the ticket with a summary for the owner: what was mapped, the top findings, and what was
NOT VERIFIED.
