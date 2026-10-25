# User guide: moving Cursor to another computer

CursorPortability is an unofficial command-line tool. It is not made by, endorsed by, or affiliated with Cursor or Anysphere.

Cursor does not publish the format of its local files. The tool only changes data in layouts it has been tested on, and it stops without writing anything when it finds something it does not recognise.

## Pick a route

| You want | Route |
|----------|-------|
| Your chat history, per-project state and settings, with each project opening from its new folder and keeping its chats | [Route A](#route-a-copy-the-profile-then-re-point-projects): copy the profile by hand, then re-point projects with the tool |
| Only your settings, keybindings and snippets, merged into a Cursor install you already use | [Route B](#route-b-settings-keybindings-and-snippets-only) |

## What works where

| From → to | Route A: re-point projects and chats | Route B: settings, keybindings, snippets |
|-----------|--------------------------------------|------------------------------------------|
| Linux → macOS | Verified on one real profile: two rehearsals on copies, then the real run | Automated tests on synthetic profiles |
| macOS → macOS (for example, a different user name) | Untested. Add `--source-os darwin` and rehearse on a copy first | Automated tests on synthetic profiles |
| Windows → macOS | Not supported: apply refuses Windows paths | Automated tests on synthetic profiles |
| Any → Linux or Windows | Not supported: the new workspace IDs can only be computed on macOS | Automated tests on synthetic profiles |

Messages and plan fields say "Ubuntu" and "mac" for the old and new machine, whichever Linux distribution you came from.

## What the tool never does

- Copy a profile from one machine to another. Route A has you do that by hand.
- Write anything while Cursor is running. It checks for running Cursor processes and stops.
- Connect to the network. It has no runtime dependencies beyond Python's standard library.
- Change the text of chat messages. Old messages keep mentioning old paths.
- Delete the old projects' entries. They stay in the profile, unused.

## Before you start

### Install

You need Python 3.12 or newer. The `python3` that ships with macOS is older, so install one from [python.org](https://www.python.org/downloads/) or with Homebrew (`brew install python@3.12`).

```bash
git clone <repository-url> CursorPortability
cd CursorPortability
python3.12 -m venv .venv
source .venv/bin/activate
pip install .
cursor-port doctor
```

In every new terminal, run `source <path-to>/CursorPortability/.venv/bin/activate` again, or call `<path-to>/CursorPortability/.venv/bin/cursor-port` directly.

### Quit Cursor properly

Steps that read or write the profile need Cursor fully closed: on macOS use Cursor → Quit Cursor (⌘Q), not just closing windows. Run the commands from Terminal or another terminal app, not from Cursor's built-in terminal. If any Cursor process is still running, the tool stops with `CURSOR_RUNNING`.

### Treat the copies as private

The profile archive, profile copies, bundles, plans, census output (without `--shareable`), and run folders contain your project paths, and most of them contain every chat you have had. Keep them on your own machines, never attach them to an issue, and delete them when you are done ([Clean up](#step-8-clean-up)).

## Route A: copy the profile, then re-point projects

This route fits when your projects on the old machine lived under one folder (`OLD`) and now live under one folder on the Mac (`NEW`) with the same folder names. For example, `/home/<you>/Projects/app` becomes `/Users/<you>/Projects/app`. Projects outside `OLD` are left alone.

Set these once in the Mac terminal. The commands below use them.

```bash
OLD=/home/<you>/Projects                            # project folder on the old machine
NEW=/Users/<you>/Projects                           # project folder on this Mac
PROFILE="$HOME/Library/Application Support/Cursor"  # Cursor's profile on this Mac
WORK="$HOME/cursor-migration"                       # scratch folder for plans and copies
mkdir -p "$WORK"
```

### Step 1: Put your projects in place

Clone or copy each project into `NEW`, keeping its folder name. Use real folders: a project whose path goes through a symlink is skipped.

If you can, don't open these projects in Cursor on the Mac until step 7. A project opened on the Mac first gets its own Mac workspace, and the remap then skips it unless you choose `--replace` or `--link-chats` (step 5).

### Step 2: Copy the Cursor profile (manual, not done by the tool)

The tool never copies a live profile. You do this once by hand. The remap only needs the profile's `User` folder:

| OS | `User` folder |
|----|---------------|
| Linux | `~/.config/Cursor/User` (or `$XDG_CONFIG_HOME/Cursor/User`) |
| macOS | `~/Library/Application Support/Cursor/User` |
| Windows | `%APPDATA%\Cursor\User` |

On the old Linux machine, quit Cursor (every window), then archive the folder:

```bash
tar -C ~/.config/Cursor -czf ~/cursor-user.tar.gz User
```

The archive holds all your chats and may hold sign-in tokens. Move it to the Mac over something you trust, such as `scp` or an encrypted drive, not a public file share.

On the Mac, quit Cursor. If Cursor has never run on this Mac, start it once and quit it so the profile folder exists. Then move the Mac's own `User` folder aside (it is kept, not deleted) and extract the archive in its place:

```bash
mv "$PROFILE/User" "$WORK/User-before-migration"
tar -C "$PROFILE" -xzf ~/cursor-user.tar.gz
```

Anything that was only in the Mac's own profile (its chats and settings) is now in `User-before-migration` and no longer used, so this step is best done on a fresh Cursor setup.

Don't copy the rest of `~/.config/Cursor` (caches, logs, cookies and machine IDs from the old system) or `~/.cursor/extensions` (extensions are built for the old operating system). To reinstall extensions, use Cursor's `cursor` shell command: `cursor --list-extensions > extensions.txt` on the old machine, then `xargs -L1 cursor --install-extension < extensions.txt` on the Mac. You can copy personal files under `~/.cursor` such as `mcp.json` by hand; fix any paths inside them.

You can open Cursor now to check that your old chats appear. They are not attached to projects yet. Don't open projects from `NEW` yet, and quit Cursor before the next step.

### Step 3: Run the census (read-only)

```bash
cursor-port --json workspaces census --from "$OLD" --to "$NEW" > "$WORK/census.json"
```

The census reads copies of the databases and changes nothing. In its `workspaces` list, check each old project:

- `status`: `mapped` is what you want. `destination_missing` means there is no folder at the new path (see step 1). `missing` means the project was outside `OLD` and is left alone. `unsupported` is a workspace that is not a local folder, such as a remote one, and is not moved.
- `composer_header_rows`: how many chats belong to the project.
- `mac_workspace_folder_exists`: `true` means the project was already opened on the Mac (see step 5).

If the census fails with `SOURCE_CHANGED`, Cursor was writing while it read. Quit Cursor and run it again.

### Step 4: Rehearse on a copy

Your real profile is not touched in this step. Quit Cursor, then:

```bash
cp -cR "$PROFILE" "$WORK/rehearsal-profile"
cursor-port workspaces remap plan --from "$OLD" --to "$NEW" \
  --target-profile "$WORK/rehearsal-profile" --output "$WORK/rehearsal-plan.json"
cursor-port --json workspaces remap show "$WORK/rehearsal-plan.json"
cursor-port workspaces remap apply "$WORK/rehearsal-plan.json" --runs-dir "$WORK/rehearsal-runs"
cursor-port workspaces remap verify --run-id <run-id> --runs-dir "$WORK/rehearsal-runs"
```

`cp -c` makes an APFS clone, which is fast and takes almost no extra space; on a disk that is not APFS, use `cp -R`. `show` lists the planned operations and any `warnings` (step 5). `apply` asks you to type `yes`, then prints the run ID that `verify` needs. `verify` should print `Remap verify: PASS`.

Then open a separate Cursor on the copy:

```bash
open -na Cursor --args --user-data-dir "$WORK/rehearsal-profile"
```

In that window, open a moved project from its `NEW` folder and check that:

- its old chats are listed,
- a file referenced in an old chat opens,
- the Agents window shows the new path.

Don't do real work in this window; quit it when you are done. If anything looks wrong, stop here. Delete the copy, and ask for help with the `--shareable` output described in [Privacy](#privacy).

### Step 5: Handle skipped or merged projects

The plan warns about projects it will not move as they are:

| Warning | Meaning | What to do |
|---------|---------|------------|
| `skip <project>: mac workspace folder <id> already exists` | The project was already opened on the Mac | Re-plan with `--replace <project>` (the old machine's workspace state replaces the Mac's) or `--link-chats <project>` (keep the Mac workspace and attach the old chats; only offered when the census shows `link_chats_eligible: true`) |
| `skip <project>: destination folder missing` | No folder at the new path | Put the project in place (step 1) and re-plan |
| `skip <project>: destination path is not canonical (symlink?)` | The new path goes through a symlink | Use the real folder location as `NEW` |
| `merge <project>: <n> Ubuntu workspaces; ...` | The same folder was opened as several workspaces on the old machine (for example, after a re-clone) | Nothing: the chats of all of them are attached. `--skip-duplicates` leaves such projects out instead |

`--replace` and `--link-chats` take a project folder name and can be repeated. After changing flags, rehearse again on a fresh copy.

### Step 6: Run it for real

Quit every Cursor window. First make a full copy of the profile. Once Cursor has been reopened, this copy is the only way back:

```bash
cp -cR "$PROFILE" "$WORK/pre-remap-profile"
cursor-port workspaces remap plan --from "$OLD" --to "$NEW" --output "$WORK/remap-plan.json"
cursor-port --json workspaces remap show "$WORK/remap-plan.json"
cursor-port workspaces remap apply "$WORK/remap-plan.json"
cursor-port workspaces remap verify --run-id <run-id>
```

Add the same `--replace` / `--link-chats` flags you used in the rehearsal. Without `--target-profile`, the plan uses this Mac's profile, and the run folder goes to `~/.cursor-portability/runs/<run-id>`.

Run `verify` before opening Cursor. It counts the chats under each project, so new chats would change the count.

### Step 7: Check in Cursor

Open Cursor, open your projects from `NEW`, and repeat the rehearsal checks. When you continue an old chat:

- New tool calls run in the new folder.
- Earlier messages still mention old paths, so the agent may try one once before using the new path.
- "Restore checkpoint" does not work on turns from before the move.
- Commands that only work on the old system (package managers, shell paths) need their macOS equivalents.

Keep project folders where they are. A project's Mac workspace ID depends on its folder's creation time, so if you delete and re-create the folder (for example, with a fresh clone), Cursor treats it as a new project and its chats stay with the old one.

#### Undo

- **Before reopening Cursor:** `cursor-port workspaces remap rollback --run-id <run-id>` (type `yes`) puts back the files the run changed.
- **After reopening Cursor:** rollback refuses with `TARGET_CHANGED`, because Cursor has changed the database since. Quit Cursor and restore the full copy:

```bash
mv "$PROFILE" "$WORK/remapped-profile"
cp -cR "$WORK/pre-remap-profile" "$PROFILE"
```

### Step 8: Clean up

When you are happy with the result, delete the rehearsal files, plans and run folder. The run folder's snapshot holds a full copy of the global database, including every chat.

```bash
rm -rf "$WORK/rehearsal-profile" "$WORK/rehearsal-runs"
rm -f "$WORK"/*.json
rm -rf ~/.cursor-portability/runs/<run-id>
```

After a few days of normal use, also delete `"$WORK/pre-remap-profile"`, `"$WORK/User-before-migration"`, and `~/cursor-user.tar.gz` on both machines.

## Route B: settings, keybindings and snippets only

This route merges `settings.json`, `keybindings.json` and snippets from the old machine into Cursor on the new one. Chats and project history are not moved. Install the tool on both machines.

On the old machine, quit Cursor and create a bundle:

```bash
cursor-port backup create --output ~/cursor-bundle --zip
cursor-port backup verify ~/cursor-bundle.zip
```

The bundle leaves out known credential files and removes credential-like database keys. Chats inside it can still contain secrets you pasted, so treat it as private. `--include-mcp` adds `mcp.json`, which often holds API keys.

Copy `cursor-bundle.zip` to the new machine, quit Cursor there, and run:

```bash
cursor-port backup verify ~/cursor-bundle.zip
cursor-port plan create --backup ~/cursor-bundle.zip --output ~/cursor-plan.json
cursor-port --json plan show ~/cursor-plan.json
cursor-port migrate apply ~/cursor-plan.json --backup ~/cursor-bundle.zip
cursor-port migrate verify --run-id <run-id>
```

How the merge works:

- Settings and snippets missing on the new machine are added, and comments in the files are kept.
- When both machines have a setting with different values, the new machine's value is kept and listed under `conflicts` in `plan show`. To take the old machine's value, add `--use-source <setting>` to `plan create` (repeatable).
- Keybindings the new machine does not have are appended. One with the same key and `when` but a different command is a conflict.
- Values are copied as they are, so edit any setting that holds a path from the old system.
- `extensions_to_install` in `plan show` lists extensions to install yourself.

To undo, run `cursor-port migrate rollback --run-id <run-id>`. It refuses if you have edited those files since.

## Troubleshooting

Errors print as `CODE: message`. Exit codes: `0` pass, `1` failure, `2` usage error, `4` unknown database format, `5` Cursor is running.

| Error | Meaning | What to do |
|-------|---------|------------|
| `CURSOR_RUNNING` | A Cursor process is still running | Quit Cursor with ⌘Q, check Activity Monitor, and run the command from Terminal |
| `SOURCE_CHANGED` | Cursor wrote to the profile while the tool was reading it | Quit Cursor and run the command again |
| `TARGET_CHANGED: global database changed since plan` | The profile changed between `plan` and `apply` | Create a new plan and apply it straight away, with Cursor closed |
| `TARGET_CHANGED: global database changed since remap; refusing rollback` | Cursor was opened after the remap | Restore the pre-remap copy ([Undo](#undo)) |
| `PLAN_INVALID: cannot re-point chats: ...` | A chat names a different workspace or folder than its project, which has not been tested | Nothing was changed. Ask for help with the `--shareable` census |
| `PLAN_INVALID: workspace databases contain chat-like keys; --link-chats not offered` | `--link-chats` is unsafe for this profile | Use `--replace` instead |
| `UNKNOWN_SCHEMA` | A database layout the tool does not know, for example from a newer Cursor | Nothing was changed. Ask for help with `cursor-port --json inspect --shareable` output |
| `PROFILE_NOT_FOUND` | No profile at the default location | Pass `--target-profile <folder>`, the folder that contains `User` |
| `INSUFFICIENT_DISK_SPACE` | Not enough free space for copies and snapshots | Free some space and retry |
| `Remap verify: FAIL` | The profile does not match what the run wrote | Roll back before opening Cursor, and ask for help |

## Privacy

| File | Contains |
|------|----------|
| Profile archive and copies (`cursor-user.tar.gz`, `rehearsal-profile`, `pre-remap-profile`) | Everything, including chats and possibly sign-in tokens |
| Run folders (`~/.cursor-portability/runs/<run-id>`) | A full copy of the global database, including chats |
| Bundles | Settings, extension list, and databases with credential-like keys removed |
| Census output and plans without `--shareable` | Project paths and workspace IDs |

To ask for help, share only `--shareable` output:

```bash
cursor-port --json workspaces census --from "$OLD" --to "$NEW" --shareable > census-shareable.json
cursor-port --json workspaces remap show "$WORK/remap-plan.json" --shareable
cursor-port --json inspect --shareable
```

Shareable output replaces workspace IDs with short hashes, project names with numbers, and your folder prefixes with placeholders, and it drops profile paths. Database key names are kept in a generalised form, and a key that embeds a one-word project name can still show it, so read the file before posting it.

## What is not moved

- Paths inside chat messages, tool output and checkpoints.
- The `~/.cursor` folder: agent transcripts, its chat store, MCP configuration, and extensions.
- Extensions themselves (reinstall them; Route B lists their IDs).
- Sign-in state and secrets stored with the old system's keyring. Sign in to Cursor and to extensions again if asked.
- Projects outside `OLD`, remote workspaces (SSH, WSL, dev containers), and multi-root `.code-workspace` workspaces.
- Cloud and Background Agent history, which lives on Cursor's servers and is not affected.
