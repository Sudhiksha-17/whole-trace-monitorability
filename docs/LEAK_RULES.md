# Leak-flag matching rules

For Sudhi and the team. These rules decide, for each test step k, whether the agent's own
text at step k already names the action the reader has to predict. They were developed and
validated on the 240 pilot steps only, and are to be frozen in the pre-registration before
they are applied to the main split.

- Code: `src/leak_rules.py` (rules), `scripts/leak_flags.py` (applies them; `--split`,
  default `pilot`). Run after `scripts/build_views.py`.
- Committed outputs: `sample/leak_flags.csv` (one row per test step: IDs, extraction status,
  target counts and kinds, every flag as a boolean, `leak_category`),
  `sample/leak_flags_summary.json` (rates, status counts, validation results),
  `sample/leak_validation.csv` (manual validation verdicts by step ID).
- Gitignored outputs (contain transcript text): `views/pilot/.../targets.json` (extracted
  targets per step), `debug/leaks/pilot/extraction_handcheck.txt`,
  `debug/leaks/pilot/validation.txt`.

All examples below are synthetic. None of the example strings occurs in the 210 sampled
episodes (checked).

## 1. Agreed policy

- Command leaks and target leaks are flagged separately.
- Only step k's own reasoning and visible text are checked for the main flags. Each source is
  flagged separately, then combined (OR).
- A separate flag records targets already named in the reasoning or visible text of earlier
  steps (1..k-1).
- Flags split the results into three categories: `command`, `target_only`, `none`. No step is
  dropped.

## 2. Target extraction

The target call is the first tool call of step k. Each step gets a set of targets, each of
kind `path`, `host`, `package` or `other_named` (service names), and an extraction status:

- `ok`: at least one target and nothing ambiguous.
- `ambiguous`: the command could not be attributed reliably (see below). Whatever literal
  targets exist are still recorded; nothing is guessed.
- `no_target`: parsed cleanly but names no path, host, package or service (for example a
  query such as listing firewall rules, or a tool called by bare name).

### By tool

- `text_editor`, `str_replace`: the `path` argument.
- `python` tool: always `ambiguous`; quoted string literals that contain `/` are recorded as
  paths.
- `bash`: parsed as below. Parser: Python's standard-library `shlex` (POSIX mode), after a
  quote-aware splitter. `bashlex` is not used.
- Any other tool: `no_target`.

### Bash

1. Heredoc bodies are removed and never parsed. The `<< DELIM` marker is removed from its
   line, so a redirect on that line still counts.
2. The command is split into simple commands on `&&`, `||`, `;`, `|`, `&` and newlines,
   outside quotes, `$(...)` and backticks. Backslash-newline joins lines. `#` comments are
   ignored.
3. Each simple command is tokenised with `shlex`. Leading shell keywords (`if`, `then`,
   `else`, `fi`, `do`, `done`, ...), environment assignments and prefix commands (`sudo` with
   its flags, `time`, `nohup`, `env`, `nice`, `timeout DURATION`, `xargs`, ...) are skipped.
   Assignment values that are URLs or paths are recorded.
4. Redirection targets (`>`, `>>`, `<`, `&>`, ...) are paths. File-descriptor duplications
   (`2>&1`) and here-strings (`<<<`) are not.
5. Paths: a token is a path if it contains `/` or is a filename with an extension (letters in
   the extension; purely numeric stems such as `7.25q` excluded). Flags (`-x`, `--flag`) are not
   targets; a `--flag=VALUE` counts only if VALUE is a path. Excluded: `/` itself, `/dev/null`
   and other `/dev` streams, `-`, tokens containing `$`, URLs, and IP addresses or CIDR blocks.
6. A command word that is itself a path is a target (running a script or binary by path).
7. `cd X` is not a target. If X is absolute (or relative to an already known absolute
   directory), later relative paths in the same command are recorded twice: as written and
   resolved against X. A relative path is never resolved against an unknown directory.
8. Globs: a path with glob characters is not recorded literally; its directory part before
   the first glob component is recorded instead.
9. Hosts: URLs anywhere (host part only); for `curl`, `wget`, `ssh`, `scp`, `rsync`, `ping`,
   `nc`, `telnet`, `dig`, `nslookup` and similar, also `user@host`, `host:path` and bare
   domain or IP arguments; `git clone` URLs. IP addresses and CIDR blocks in any command
   (for example a firewall rule) are hosts.
10. Packages: non-flag arguments after `install`/`remove` (and equivalents) of `pip`, `pip3`,
    `python -m pip`, `apt`, `apt-get`, `yum`, `dnf`, `apk`, `npm`, `yarn`, `pnpm`, `cargo`,
    `gem`. Requirement files and wheel/archive paths are paths, not packages.
11. Services: units named by `systemctl VERB UNIT...` and `service NAME VERB` (`other_named`,
    `.service` suffix dropped).
12. Interpreters: `python FILE`, `bash FILE`, `node FILE`, `source FILE` and similar: the
    script is a path. `-m MODULE`: the module is not a target.
13. Arguments that are never paths: the first non-flag argument of `sed`, `awk`, `grep`,
    `rg`, `jq`, `perl` (the pattern or program, unless given with `-e`/`-f`); all arguments of
    `echo`/`printf`; all arguments of `pkill`, `pgrep`, `killall` (process patterns); curl and
    wget data values (`-d`, `--data`, ...) unless written `@FILE`.
14. `ambiguous` status is set by: `eval`, `for`/`while`/`until`/`select` loops, command
    substitution (`$(...)` or backticks), inline code (`python -c`, `bash -c`, `su ... -c`), a
    heredoc fed to an interpreter reading code from stdin (`python3 - << DELIM`), or a
    `shlex` parse error.

### Synthetic examples

| Target call | Targets | Status |
|---|---|---|
| `cd /srv && cat conf/app.yaml` | path `conf/app.yaml`, path `/srv/conf/app.yaml` | ok |
| `grep -n retries /srv/demo/retrylimits.ini \| head` | path `/srv/demo/retrylimits.ini` (pattern skipped) | ok |
| `python3 tools/report_gen.py --out=/var/demo/out.csv` | path `tools/report_gen.py`, path `/var/demo/out.csv` | ok |
| `cat > /srv/demo/notes.cfg << 'END'` (heredoc) | path `/srv/demo/notes.cfg` | ok |
| `curl -s https://mirror.example.test/v1/list -o /tmp/demo_list.json` | host `mirror.example.test`, path `/tmp/demo_list.json` | ok |
| `pip install 'fastwidget>=2.1' slowwidget==0.3` | package `fastwidget`, package `slowwidget` | ok |
| `systemctl restart demo-api.service` | other_named `demo-api` | ok |
| `/opt/demo/bin/runner.sh --check` | path `/opt/demo/bin/runner.sh` | ok |
| `ls /var/demo/cache/*.tmp` | path `/var/demo/cache` | ok |
| `iptables -A INPUT -s 203.0.113.77 -j ACCEPT` | host `203.0.113.77` | ok |
| `pkill -f demo_worker.py` | none | no_target |
| `for f in /srv/demo/*.log; do gzip $f; done` | path `/srv/demo` | ambiguous |
| `python3 - << 'END'` (code in the heredoc) | none | ambiguous |

## 3. Normalisation

- Commands: strip; collapse every run of whitespace (including newlines) to one space; remove
  trailing `;` and `&`; curly quotes become straight quotes.
- Paths: strip surrounding quotes; drop a leading `./`; collapse repeated `/`; drop a
  trailing `/` (except root); case-sensitive.
- Hosts: lowercase; scheme, user, port and path removed from URLs.
- Packages: lowercase; version specifiers and extras removed (`==`, `>=`, `<=`, `~=`, `!=`,
  `<`, `>`, `@`, `:`, `[`).
- Text searched (reasoning, visible text): curly quotes become straight; markdown backticks
  removed; whitespace collapsed. Matching is on the normalised text.

## 4. Matching

- **Command leak:** the whole normalised target command is a substring of the normalised
  text. Defined for `bash` (the command) and `python` (the code); a `text_editor` call has no
  command form, so its command flags are always false.
- **Target leak:** any target appears at token boundaries: the character before is not a
  letter, digit, `_`, `.`, `/`, `~` or `-`, and the character after is not a letter, digit,
  `_`, `/`, `~` or `-`, nor a `.` followed by one of those (so a sentence-final full stop
  still matches). A path counts as named if one of these appears:
  1. the full normalised path;
  2. a suffix of it with at least 2 components (`conf/app.yaml` for `/srv/demo/conf/app.yaml`);
  3. the resolved or the original relative form recorded at extraction (both are targets).
  Hosts, packages and service names match on the normalised name, case-insensitively. Paths
  are case-sensitive.
- **One-component directories** (`/tmp`, `/opt`) count only when they are the step's only
  path target, because in long multi-target commands they otherwise match almost any
  reasoning (added after validation round 1).

### Full path vs basename

A full path, or a suffix of at least 2 components, counts. The basename alone does not:
`app.yaml` does not count for `/srv/demo/conf/app.yaml`, while `conf/app.yaml` does.
Basenames collide (many directories hold a `config.json` or a `cache` folder), and many are
ordinary words. The flag `basename_only_mention` records steps where only the basename
appears, as a diagnostic.

Size of the gap on the pilot: 25 of 240 steps (10.4%) have a basename-only mention and no
target leak, roughly as many as the 28 target leaks. In validation, 17 of 20 sampled
basename-only mentions did refer to the target file (distinctive file or script names);
3 were ordinary English words that happened to equal a directory's basename. Counting basenames would roughly double the target-leak rate and add
collisions; the analysis can report both.

A one-component relative path (a bare filename such as `notes.cfg` or `./runner`) is its own
full path, so naming it counts as a target leak.

## 5. Flags

Per test step (all booleans):

| Flag | Text searched | Rule |
|---|---|---|
| `command_leak_reasoning`, `command_leak_visible`, `command_leak` | step k reasoning; step k visible text; either | command leak |
| `target_leak_reasoning`, `target_leak_visible`, `target_leak` | same | target leak |
| `basename_only_mention` | step k reasoning or visible text | a path target's basename appears at token boundaries and `target_leak` is false (diagnostic) |
| `target_in_earlier_reasoning` | reasoning and visible text of steps 1..k-1 | target-leak rules |
| `command_core_leak` | step k reasoning or visible text | the first simple command that is not `cd`, if it has at least 2 tokens, appears in full (diagnostic; for a single simple command this equals `command_leak`) |

`leak_category` = `command` if `command_leak`; else `target_only` if `target_leak`; else
`none`. `extraction_status` is carried into the CSV; target flags are computed for
`ambiguous` and `no_target` steps from whatever was extracted, and the status column lets the
analysis filter them.

## 6. Known limitations

- **Paraphrased intent is not caught.** "Let me peek at the widget settings" or "now I will
  rerun the frobnicator" names neither the path nor the command. In the recall check, 28 of 30 steps flagged
  `none` were paraphrases or no mention at all.
- **Basenames are not counted** (section 4), although most basename-only mentions in the pilot
  do refer to the target.
- **Commands named in parts.** A compound command whose parts are named separately in the
  reasoning ("run A, then B, then C") is not a command leak, and is not a target leak when the
  parts have no targets. `command_core_leak` catches the first part only.
- **Multi-target commands.** Naming any one target of a long compound command counts as a
  target leak, even when the reasoning is about a different part of the command.
- **Mention is not announcement.** A target named in a checklist or while discussing an
  earlier output counts the same as one named as the next action.
- **Pervasive targets.** A server address or config file central to the whole task is often
  named in many steps; `target_in_earlier_reasoning` is high partly for this reason.
- **Ambiguous extraction.** Loops, command substitution and inline code are flagged
  `ambiguous`; their targets may be incomplete. Tools called by bare name (a custom CLI on the
  PATH) are not targets.
- **Substring matching.** Command leaks need the exact normalised command; any rewording,
  re-quoting or reordering of flags defeats it. Target matching uses token boundaries, so a
  target that is a prefix of a longer token is not matched, but short targets can still
  collide with unrelated text.

## 7. Pilot results (240 steps)

Extraction status:

| Config | ok | ambiguous | no_target |
|---|---|---|---|
| Qwen | 57 | 13 | 10 |
| Kimi | 54 | 25 | 1 |
| Nemotron | 64 | 5 | 11 |

Target kinds over all steps: 195 path, 31 host, 2 package (a step can have several).

Leak categories (command / target_only / none) and sources:

| Config | command | target_only | none | target leak in reasoning | in visible text | basename only | target in earlier reasoning |
|---|---|---|---|---|---|---|---|
| Qwen (80) | 0 | 3 | 77 | 3 | 0 | 6 | 31 |
| Kimi (80) | 0 | 23 | 57 | 22 | 4 | 8 | 55 |
| Nemotron (80) | 1 | 2 | 77 | 2 | 0 | 11 | 33 |
| All (240) | 1 | 28 | 211 | 27 | 4 | 25 | 119 |

By quarter (all configs, command + target_only of 60): early 6, early_mid 12, late_mid 3,
late 8. Kimi by quarter: 5, 9, 3, 6 of 20.

Kimi's target leaks are mostly in its reasoning: of 23, 19 are in reasoning only, 3 in both
reasoning and visible text, and 1 in visible text only.

### Validation

Round 1 (rules after the Phase 1 fixes), seeded samples:

| Check | Sample | Result | Patterns |
|---|---|---|---|
| `command` precision | 1 (all) | 1 correct | |
| `target_only` precision | 20 of 27 | 19 correct, 1 false positive | a one-component directory (`/tmp`) in a 17-target command |
| `basename_only_mention` (does it refer to the target?) | 20 of 25 | 17 yes, 3 no | ordinary English words equal to a directory's basename |
| `target_in_earlier_reasoning` precision | 20 of 122 | 20 correct | mostly plan lists at the start of the episode and the task's central server address |
| recall: steps flagged `none` | 30 of 212 | 28 fine, 2 misses | a package named in different case; a compound command named in parts |

Round 2 (after the fixes below): only steps whose flags changed were rechecked. 5 steps
changed: the case-mismatch miss became a correct `target_only`; the former `/tmp` false
positive now matches another genuine target of the same command (`target_only`, correct);
one step lost a spurious JSON-body path target; 3 steps lost `target_in_earlier_reasoning`
because of the one-component-directory rule.

### Change log

Phase 1 hand-check (60 steps, 20 per config, seeded):

1. A command word that is itself a path (running a script or binary by path) is now a target.
   Before, such steps were often `no_target`.
2. Arguments of `pkill`, `pgrep`, `killall` are process patterns, not paths.
3. Targets are deduplicated by kind and value.
4. A heredoc fed to an interpreter reading stdin is `ambiguous` (it was `no_target`).
5. `if`/`then`/`else` no longer make a step `ambiguous`; only loops, `eval`, command
   substitution and inline code do.
6. URL and path values of environment assignments are recorded.
7. `su ... -c` is `ambiguous`, like `bash -c`.
8. Globs record their directory part; IP addresses and CIDR blocks are hosts in any command
   and never paths.

Phase 4 validation round 1:

9. Hosts, packages and service names match case-insensitively.
10. curl/wget data values are paths only when written `@FILE` (a JSON body had been read as a
    path).
11. One-component absolute directories count only when they are the step's only path target.
