# Zelab setup

Run `rtk proxy make` on the server, then rerun it after each step below; it
resumes where it stopped and refreshes this guide. From a laptop:
`rtk proxy make REMOTE_HOST=user@server REMOTE_DIR=/absolute/path/to/Zelab`.

### Server

x86-64 Debian/Ubuntu with KVM, Docker Compose, SSH, Python 3.12+, Make and [RTK](https://github.com/rtk-ai/rtk).

- 24 GiB free disk and 4 GiB available RAM.
- Laptop (optional): Python 3.9+, Make, RTK and SSH.
- Fresh installs briefly use up to 70% of spare CPU/RAM; set
  `vm_install_resource_fraction: 0` in `config/site.yaml` to disable.

### Administrator

Enter your sudo password, then three UI passwords (Hermes, Paperclip, Obsidian) when make asks.

- Paste unique passwords of 16+ characters from your password manager. Input is
  hidden and nothing is printed or logged.
- Reapply reuses them; Paperclip asks for its current owner password.
- After a successful run, make offers to delete spare copies once you confirm
  they are saved. Obsidian keeps its runtime password file.

### Tailscale

Join the server and your devices to one tailnet; enable MagicDNS and HTTPS certificates.

### Worker harness

Pick **Codex**, **Claude Code** or **skip** when make asks. A harness creates one
Paperclip worker, **Operations**, fully configured except its model; skip creates none.

- Codex: sign in, then rerun make: `rtk proxy /usr/local/bin/zelab-codex login --device-auth`.
  Claude Code: sign in after make, as the worker:
  `rtk proxy sudo -u zelab-ops -H /usr/local/bin/zelab-claude auth login --claudeai`
- In Paperclip, open Operations and choose its model. A name missing from the list
  (for example `gpt-6.1-sol`) can be typed and kept with **Use manual model**.
- Then move **Prove Operations host access and sudo** from Backlog to Todo. It
  runs a non-interactive `top` and `sudo -n` checks and reports the output.
  Start it only by moving the task: the agent's **Run now** button starts a run
  with no task, which logs `Connection requests require a task-bound heartbeat run`
  and ends with "No assigned Paperclip task was available". That run is harmless.
- Operations runs as `zelab-ops`, which may run any command as root without a
  password (`/etc/sudoers.d/zelab-worker-operations`); agents use `sudo -n`, which
  fails instead of prompting. Narrowing that grant is your decision.
- The choice is saved as `worker_harness` in `config/site.yaml`; Enter keeps it.
  Keep Operations on `Zelab Operations host`: without it the adapter runs as
  Paperclip's own account, and make refuses to continue.

### Hermes

Open Hermes with your saved password and connect a model provider.

- make offers the native picker in the terminal. For a ChatGPT plan choose
  **OpenAI Codex** and approve the printed code on any device.
- Skipped it? Run the picker on the server, then reload the dashboard. Default
  paths below; the generated guide shows your site's command.

```sh
rtk proxy ssh -t -F /dev/null -i ~/.local/state/zelab-deployment/deploy_ed25519 -o UserKnownHostsFile=~/.local/state/zelab-deployment/guest_known_hosts -p 12921 zelab-deploy@127.0.0.1 sudo -u zelab-hermes env HERMES_HOME=/var/lib/zelab/hermes/home TMPDIR=/var/lib/zelab/hermes/tmp /opt/hermes/agent/.hermes/bin/hermes model --no-browser
```

### Discord

[Create and invite a bot](https://discord.com/developers/applications) and paste its token in Hermes → Channels → Discord.

- Enable **Message Content Intent** for the bot.
- Set `discord_owner_id` and `discord_channel_id` in `config/site.yaml`, then
  rerun make.

### Test

In Discord, ask Operations for the host's CPU, memory and Docker containers through Paperclip, with its task ID.

## Commands

- `rtk proxy make`: install or resume; keeps existing state.
- `make clean`: clear local choices and generated files.
- `make fclean`: preview, confirm and reset this site.
- `make re`: reset and reinstall (**deletes Zelab data**).
- `make backup` / `make restore`: encrypted backup and restore.
- `make help`: list all seven commands.
