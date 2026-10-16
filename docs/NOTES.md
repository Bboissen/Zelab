# Notes: the server vault and desktop Obsidian

The agents and bots work in one vault on the server, `/var/lib/zelab-vault`. The guest
sees it at `/var/lib/zelab-notes` through the notes share. You read and write the same
notes in desktop Obsidian on your laptop.

Notes move between the two only when you sync them with Git, through a private GitHub
repository:

- on the server: `make notes`;
- on the laptop: `git pull` and `git push`.

Nothing syncs on its own. The root-only vault history on the server
(`/var/lib/zelab-vault-history`, a snapshot every five minutes) is separate: it is
still your undo for anything an agent does, synced or not.

Before indexing notes, `make notes` restores the deployment operator's and installed
team tiers' native read/write ACLs in the vault. Agent tools can create or atomically
replace a note with mode `0600`, masking the inherited ACLs even when its parent is
configured correctly. This repair runs on every sync using the operator's existing
unattended sudo rights; keep running `make notes` as the operator, not root. No manual
permission repair or full reapply is needed after updating Zelab.

The repair never reads note contents or changes ownership. It refuses symlinked vault
paths, skips symlinks, hard links, special files and entries on another device, and
uses open descriptors so a renamed path cannot redirect its ACL change. Git, credentials
and network sync continue to run as the operator. A skipped unreadable entry can still
stop Git; replace such a link with a regular note before retrying.

## Two sections, two direct writers

- `Hermes/<bot>/`: personal notes, research, ideas and preparation before a project exists.
- `Paperclip/Projects/<slug>/`: project plans, findings and decisions, written directly by
  Hermes bots or Paperclip's workers. `Paperclip/Server/` holds the team's server map.

Hermes uses its bundled `obsidian` skill and native file tools. It needs no Paperclip
key or project to save a note. Paperclip coordinates work when tracking, delegation or
commands are needed; a quick project note can still be written directly from chat.
Hermes' own memory stays in its profile; notes are readable knowledge, not an automatic
copy of every memory or external database.

The folders organize one shared vault; they are not permission boundaries. Bot folders
appear on their first write. On upgrade, existing root `Projects/` and `Server/` notes
stay in place. Bots are told to search them and update existing notes at their current
paths. No notes are moved, copied or deleted by this layout change.

## Check that a bot really wrote a note

Bots edit Markdown files; a reply saying "saved" is not proof. To check the direct path:

1. Choose a new filename and unique marker; replace `your-bot` and `unique` below.
   Send your bot this message in its usual chat:

   > Use your Obsidian skill to create `Hermes/your-bot/notes-check-unique.md`
   > containing the exact text `NOTES-CHECK-unique`. Do not overwrite an existing
   > note. Read it back and reply with its vault-relative path. Do this directly,
   > without creating a Paperclip ticket.

2. On the server, check the actual file (use your configured vault root if different):

   ```sh
   sudo cat /var/lib/zelab-vault/Hermes/your-bot/notes-check-unique.md
   ```

   Confirm the exact marker, a successful read-back and that no ticket was created.
   Repeat with a new filename under `Paperclip/Projects/<slug>/` to check a direct
   project note. A bot without a task key can perform the personal-note check too.

3. To check desktop visibility, run `make notes` on the server, then `git pull --no-rebase`
   in the laptop clone. Open the same note in Obsidian and check the marker.

For the separate tracked path, explicitly ask for a Paperclip task and follow its ticket
through completion. Direct note-writing does not prove ticket dispatch or laptop sync.
Root-only snapshots run every five minutes, so a fresh note may not yet appear in history.

## Which Obsidian app runs where?

Desktop Obsidian is an optional application on your laptop. Zelab does not
install or uninstall that app. Bots can write notes with no Obsidian app open.

Browser Obsidian is the optional server container. `obsidian_enabled: false`
stops and removes that container and its own Tailscale route on the next
`make`; the vault, notes share, history and saved browser settings remain.
Removing the browser container does not remove the notes or the laptop app.

## What never travels

- **`.obsidian/`.** Your laptop keeps its own Obsidian settings. Agents can write
  anywhere in the server vault, and Obsidian plugins and snippets are code that runs on
  your laptop, so `make notes` never commits `.obsidian/`. It also refuses a remote that
  contains it.
- **Git files (`.gitignore`, `.gitattributes`...) and symlinks.** Agents could use them
  to change what syncs, or to point at files outside the vault.
- **Credentials and settings.** `make notes` keeps its Git folder, deploy key, SSH host
  keys and configuration in `private_dir/notes-sync`, outside the vault and the guest.
  Your own Git config does not apply there.
- **Your notes and the public Zelab repository.** The notes repository is a separate
  private repository. Never add it to the Zelab checkout, and never as a submodule.

## Set up once

**On the server:** plain `make` offers this once, after the bots. `make notes`
runs the same steps any time sync is not set up yet.

1. Choose a repository:
   - **Create a new private repository.** The server's GitHub CLI (`gh`), if it is signed
     in, creates `OWNER/zelab-notes` as private after you confirm.
   - **Use an existing private repository.** Pick it from your private repositories or type
     `OWNER/REPO`. Its notes join the vault on the first sync, and the vault's notes join
     it. A repository that tracks `.obsidian/` is refused until you stop tracking it
     (`git rm -r --cached .obsidian` on the laptop, then push).
2. make checks the repository is private: your `gh` login must see it as private, and
   anonymous GitHub must not see it at all.
3. It creates the server's deploy key and adds it with write access after you confirm. Without
   `gh`, it shows the key and the GitHub page to paste it into, and waits.
4. It saves `notes_sync_remote` in `config/site.yaml` and runs the first sync.

Without `gh`, create an empty private repository at https://github.com/new (no README)
first, then choose **Use an existing private repository**.

**On the laptop:** make prints these commands with your repository filled in.

5. Clone the repository with your own GitHub login, and keep Obsidian's files out of it:

       git clone git@github.com:OWNER/zelab-notes.git ~/Notes/zelab
       cd ~/Notes/zelab
       printf '.obsidian/\n.trash/\n.DS_Store\n' >> .git/info/exclude

6. In desktop Obsidian, choose **Open folder as vault** and pick `~/Notes/zelab`.
7. Leave **Restricted mode** on (Settings, Community plugins), or at least enable no
   plugin that runs code from notes: Dataview JavaScript queries, Templater and similar.
   Agents write these notes.

The browser Obsidian container is off by default (`obsidian_enabled: false`). Turning it
off on an existing site stops and removes the container and its own Tailscale route on
the next `make`. The vault, the share, the history and the browser's saved settings stay.

## Sync routine

Always in this order, so the server saves the agents' work before taking yours:

1. **Server:** `make notes`. This commits what agents changed since the last
   sync, takes anything you pushed, and pushes the result.
2. **Laptop:** in `~/Notes/zelab`, run `git pull --no-rebase`.
3. Work in Obsidian.
4. **Laptop:**

       git add --all
       git commit -m "Notes from the laptop"
       git push

   If `git push` is refused, run `git pull --no-rebase` and push again.
5. **Server:** `make notes`. Your edits land in the vault, where the agents see
   them.

A `git pull` alone on the laptop never sees the agents' latest edits. They are plain
file changes on the server until a `make notes` commits them.

### When agents are writing

`make notes` saves the agents' edits first. It then updates the vault only by
fast-forward, and Git refuses to overwrite a file that changed since. So if an agent
edits a note your laptop also changed, while `make notes` is running, nothing is lost:
it stops with "Nothing was overwritten". Run it again.

## Conflicts

Sometimes the laptop and the server change the same lines of a note. Then `make notes`
stops with a message naming the branch `server-pending`:

- the vault is unchanged;
- the server's version is saved in `server-pending` on GitHub;
- your version stays on `main`.

The merge always happens on the laptop, where you see both versions:

    git pull --no-rebase
    git merge origin/server-pending
    # each conflicted note shows both versions between <<<<<<< and >>>>>>> markers:
    # keep what you want (often both), remove the markers, then
    git add --all
    git commit --no-edit
    git push

Then run `make notes` on the server. It takes the merged notes and deletes
`server-pending`. If a new conflict appears before you merged the last one,
`make notes` asks you to merge the waiting one first.

## Never

- **`git push --force`, `git reset --hard`, `git checkout -- .` or `git clean`** in the
  notes repository. They can delete notes without a trace in Git.
- **Making the repository public**, or pushing it anywhere else. `make notes` checks
  that the repository is private on every run, before pushing.
- **Editing files in `private_dir/notes-sync`** by hand.
