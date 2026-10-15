# Zelab setup

Run `rtk proxy make` on the server, then rerun it after each step below; it
resumes where it stopped and refreshes this guide. From a laptop:
`rtk proxy make REMOTE_HOST=user@server REMOTE_DIR=/absolute/path/to/Zelab`.

### Server

x86-64 Debian/Ubuntu with KVM, Docker Compose, SSH, Python 3.12+, Make and [RTK](https://github.com/rtk-ai/rtk).

- 28 GiB free disk and 6 GiB available RAM (the guest uses 2 vCPU, 4 GiB, 20 GiB).
- Laptop (optional): Python 3.9+, Make, RTK and SSH.
- Fresh installs briefly use up to 70% of spare CPU/RAM; set
  `vm_install_resource_fraction: 0` in `config/site.yaml` to disable.
- Optional: copy `config/projects.example.yaml` to `config/projects.yaml` before
  installing, so your projects, their vault folders and host accounts exist from
  the start. You can add them later and rerun make.

### Administrator

Enter your sudo password, then three UI passwords (Hermes, Paperclip, Obsidian) when make asks.

- Paste unique passwords of 16+ characters from your password manager. Input is
  hidden and nothing is printed or logged.
- Reapply reuses them; Paperclip asks for its current owner password.
- After a successful run, make offers to delete spare copies once you confirm
  they are saved. Obsidian keeps its runtime password file.

### Tailscale

Join the server and your devices to one tailnet; enable MagicDNS and HTTPS certificates.

### Hermes bots

At the end of a fresh install, make asks how many bots to set up and opens Hermes' own wizards for each.

- For each bot you give a name and a one-line role. make creates a Hermes
  profile and hires it in Paperclip, then runs four steps:
  - **Model sign-in**: Hermes' provider menu (`hermes setup model`). For a
    ChatGPT plan choose **OpenAI Codex** and approve the printed code.
  - **Chat app**: Hermes' platform menu (`hermes gateway setup`): Discord,
    Telegram, Slack and others. Paste the bot token there; it prints the
    invite link. Allow your own user ID when it asks. For Discord, prepare
    each bot's application first (see Discord below).
  - **Persona**: the bot's `SOUL.md`, which Hermes puts first in every
    conversation. A new bot has Hermes' generic persona and does not know its
    name or job; make offers a starter with its name, the Zelab way of working
    and the other bots, which you finish in nano: what it watches, decides
    and delivers, and when it asks you first. The one-line role is only a label.
  - **Task key**: pick the Paperclip projects this bot may file tickets in.
- Add, change or remove bots later with `rtk proxy make bot`. Removing one
  terminates its Paperclip agent (its keys are revoked) and archives its profile
  without logins or tokens before deleting it.
- Every step can be skipped and run again from `rtk proxy make bot`.

### Host agents

In Paperclip, hire a Codex or Claude Code agent on a project's SSH environment, named `<slug> host`.

- Host projects come from `config/projects.yaml`: each one with a `host_preset`
  other than `none` gets the account `zelab-p-<slug>` and that environment.
- make offers Paperclip's own subscription sign-in for host agents (option 4
  in `rtk proxy make bot`); Paperclip keeps the login and shares it with agents.
- Bots file host work as unassigned tickets; you assign them to a host agent.
  With `host_tickets: bots` on a project, bots assign its host agents
  themselves and answer in the chat when the work is done.
- After hiring a host agent, run `rtk proxy make` again so the bots know it.
- A `full-sudo` project's agent is root on the host. Choose the narrowest preset.

### Discord

Create one channel per project, named after its slug, and invite the bots that work on it.

- Before the chat app step, create one application per bot in the
  [Discord developer portal](https://discord.com/developers/applications). On
  its **Bot** page: **Username** set to the bot's name (Discord shows that,
  not the application name), **Public Bot** on and **Requires OAuth2 Code Grant** off;
  under Privileged Gateway Intents, **Server Members Intent** and **Message
  Content Intent** on (without the latter Discord refuses the bot), then Save
  Changes; **Reset Token** and copy the token for Hermes' wizard.
- Open the invite link Hermes prints, pick your server and keep the
  permissions it ticks: View Channels, Send Messages, Send Messages in
  Threads, Create Public Threads, Embed Links, Attach Files, Read Message
  History, Add Reactions, Connect and Speak. Then Authorize.
- Who can see a channel is set in Discord; Zelab does not manage channels. Add
  each bot to the private project channels it should answer in.
- To talk to one bot directly, give it a private home channel: paste that
  channel's ID when Hermes' wizard asks for the home channel, then let make
  have the bot answer there without an @mention. In other channels the bot
  answers only when @mentioned: pick the entry with the APP badge, not the
  role of the same name, which bots ignore. Bots connect when make or make
  bot finishes.

### Test

In a project's channel, ask a bot to research something; expect a Paperclip ticket and notes under Projects/<slug>/.

## Commands

- `rtk proxy make`: install or resume; keeps existing state.
- `rtk proxy make bot`: add, set up or remove Hermes bots (terminal only).
- `make clean`: clear local choices and generated files.
- `make fclean`: preview, confirm and reset this site.
- `make re`: reset and reinstall (**deletes Zelab data**).
- `make backup` / `make restore`: encrypted backup and restore.
- `make help`: list all eight commands.
