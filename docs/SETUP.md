# Zelab setup

On the Linux server, from the checkout: `rtk proxy make`.
From a Mac/Linux laptop (checkout already on the server):

```sh
rtk proxy make REMOTE_HOST=user@server REMOTE_DIR=/absolute/path/to/Zelab
```

### Server

x86-64 Debian/Ubuntu, KVM, Docker Compose, SSH, Python 3.12+, Make and
[RTK](https://github.com/rtk-ai/rtk); 24 GiB disk, 4 GiB available RAM.
Laptop: Python 3.9+, Make, RTK and SSH. Bash/zsh work.

### Administrator

Enter your sudo password when prompted. Configuration is created automatically.

### Tailscale

Enroll server and devices; enable MagicDNS and HTTPS certificates.

### Worker harness

Setup offers **Codex**, **Claude Code**, **both**, or **skip** and saves `worker_harness`
in `config/site.yaml`. Every interactive run asks again; Enter retains the saved
choice, or skips when no choice exists. Only the selected CLIs are installed. Noninteractive setup
skips unless the file selects `codex`, `claude` or `both`. Change `none` later and rerun
`rtk proxy make` to configure the paused placeholder workers.

With `both`, new workers stay paused until you choose each worker's **Codex** or
**Claude Code** adapter in Paperclip. Use the CLI engine and its installed command
(`/usr/local/bin/zelab-codex` or `/usr/local/bin/zelab-claude`), and select the
declared SSH environment (`Zelab Operations host` or `Zelab Coding host`). Inert
placeholders have no execution environment; select the SSH environment when
choosing the adapter. Claude Operations uses `CLAUDE_CONFIG_DIR` set to its
account's `.claude` directory and `DISABLE_UPDATES=1`, `DISABLE_AUTOUPDATER=1`.
After signing in and testing, enable wake on demand and resume Operations;
keep Coding paused.

For Codex, on the server as the operator:
`rtk proxy /usr/local/bin/zelab-codex login --device-auth`, then rerun make.
For Claude Code, sign in as the Operations worker (default account shown):
`rtk proxy sudo -u zelab-ops -H /usr/local/bin/zelab-claude auth login --claudeai`.
In Paperclip, use **Test Environment** before submitting a worker task.
Choose models in Paperclip; Coding stays paused until sandbox execution is supported.

**UI passwords**

On a fresh install, choose three separate UI passwords in the terminal: Hermes,
Paperclip and Obsidian. Input is hidden and each new password is entered twice;
paste unique passwords of at least 16 characters from your password manager.
Hermes retains only a scrypt hash. Paperclip's provisioning copy is temporary;
reapply asks for its current owner password. Obsidian needs a persistent native
root-owned password file for container startup. No password is printed or logged.

Existing sites reuse current password files without rotation. After a successful
apply, the terminal offers to remove redundant deployment copies once you confirm
the UI passwords are saved in your password manager. The required Obsidian runtime
file and machine keys remain. Old backups or snapshots may retain earlier copies.

### Hermes

Open the printed Hermes URL, use your saved password, and sign in to
your chosen provider and model on the default Operations profile using native setup.

### Discord

[Create and invite a bot](https://discord.com/developers/applications), enable
Message Content Intent, then paste its token in Hermes → Channels → Discord.
Set `discord_owner_id` and `discord_channel_id` in `config/site.yaml`.

### Test

After choosing and signing in to a worker harness, rerun the install command.
Ask Operations in Discord to report the host's CPU,
memory and Docker containers through Paperclip, returning its task ID.

Failed? Fix the cause and rerun `rtk proxy make`; it resumes and refreshes the guide.
`make clean` clears local deployment choices and generated files, retaining host
connections and resource locations. `make fclean` previews/confirms a project reset
and then clears those local choices;
`make re` resets and reinstalls (**deletes Zelab data**). `make backup` saves encrypted
state; `make restore` selects/verifies/restores it. `make help` lists the seven commands.

Fresh installs temporarily use up to 70% of spare CPU/RAM, keep host headroom,
then restart at normal limits. Running VMs stay unchanged. Disable with
`vm_install_resource_fraction: 0` in `config/site.yaml`. Timings stay in private logs.
