"""Leak-flag rules: target extraction, normalisation and matching.

Rules are documented in docs/LEAK_RULES.md. This module operates on transcript
text; never write its extracted targets or matched text to a git-tracked file.
"""

import posixpath
import re
import shlex
from dataclasses import dataclass, field
from urllib.parse import urlsplit

# ---------------------------------------------------------------------------
# Target model
# ---------------------------------------------------------------------------

PATH, HOST, PACKAGE, OTHER = "path", "host", "package", "other_named"
OK, AMBIGUOUS, NO_TARGET = "ok", "ambiguous", "no_target"
PARSER = "shlex (Python standard library), with a quote-aware splitter for &&, ||, ;, |, & and newlines"


@dataclass(frozen=True)
class Target:
    kind: str
    value: str  # normalised
    origin: str  # how it was found, e.g. "arg", "redirect", "resolved", "editor_path"


@dataclass
class Extraction:
    status: str
    targets: list = field(default_factory=list)
    reasons: list = field(default_factory=list)  # why ambiguous / notes
    segments: list = field(default_factory=list)  # simple commands (bash only)

    def add(self, kind, value, origin):
        if value and all((t.kind, t.value) != (kind, value) for t in self.targets):
            self.targets.append(Target(kind, value, origin))


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

CURLY = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'",
                       "“": '"', "”": '"', "„": '"', "‟": '"'})
IGNORED_PATHS = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/stdin", "/dev/zero", "/dev/urandom", "/dev/random", "-"}


def collapse_ws(text):
    return " ".join(text.split())


def norm_command(cmd):
    cmd = collapse_ws(cmd.translate(CURLY))
    while cmd and cmd[-1] in ";&" and not cmd.endswith("&&"):
        cmd = cmd[:-1].rstrip()
    return cmd


def norm_text(text):
    """Text to search: quote unification, markdown backticks removed, whitespace collapsed."""
    return collapse_ws(text.translate(CURLY).replace("`", " "))


def norm_path(p):
    p = p.strip().strip("'\"")
    while p.startswith("./"):
        p = p[2:]
    p = re.sub(r"/{2,}", "/", p)
    if len(p) > 1 and p.endswith("/"):
        p = p.rstrip("/") or "/"
    return p


def norm_host(h):
    h = h.strip().strip("'\"").lower()
    if "://" in h:
        h = urlsplit(h).hostname or ""
    else:
        h = h.split("/", 1)[0]
        if "@" in h:
            h = h.rsplit("@", 1)[1]
        if h.count(":") == 1:  # host:port or host:path (not IPv6)
            h = h.split(":", 1)[0]
    return h.strip("[]")


def norm_package(p):
    p = p.strip().strip("'\"").lower()
    p = re.split(r"==|>=|<=|~=|!=|<|>|@|:|\[", p, maxsplit=1)[0]
    return p.strip()


# ---------------------------------------------------------------------------
# Token classification
# ---------------------------------------------------------------------------

EXT_FILE_RE = re.compile(r"^[A-Za-z0-9_@+~-][A-Za-z0-9_.@+~-]*\.[A-Za-z][A-Za-z0-9]{0,9}$")
URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}(:\d+)?(/.*)?$")
DOMAIN_RE = re.compile(r"^(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(:\d+)?(/.*)?$|^localhost(:\d+)?(/.*)?$")
GLOB_CHARS = set("*?[]{}")
CIDR_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$")


def glob_dir(tok):
    """Directory part of a glob path before the first component with a glob character."""
    comps = tok.split("/")
    keep = []
    for c in comps:
        if GLOB_CHARS & set(c):
            break
        keep.append(c)
    d = "/".join(keep)
    return d if d.count("/") >= 1 and len(d) > 1 and d != tok else None


def is_url(tok):
    return bool(URL_RE.match(tok))


def is_path_like(tok):
    """A token is a path if it contains '/' or is a filename with an extension."""
    tok = tok.strip("'\"")
    if not tok or tok.startswith("-") or is_url(tok) or "$" in tok:
        return False
    if not re.search(r"[A-Za-z0-9]", tok):
        return False
    if IP_RE.match(tok) or CIDR_RE.match(tok):
        return False
    if "/" in tok:
        if tok == "/" or "\n" in tok or " " in tok:
            return False
        return True
    if EXT_FILE_RE.match(tok):
        stem = tok.rsplit(".", 1)[0]
        return not re.fullmatch(r"[\d.]+", stem)
    return False


# ---------------------------------------------------------------------------
# Bash parsing
# ---------------------------------------------------------------------------

HEREDOC_RE = re.compile(r"(?<!<)<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")


def strip_heredocs(cmd):
    """Remove heredoc bodies (not parsed). Keep the command line without the << DELIM part."""
    lines = cmd.split("\n")
    out, pending, used, to_interpreter = [], [], False, False
    for line in lines:
        if pending:
            delim, dash = pending[0]
            if (line.lstrip("\t") if dash else line).strip() == delim:
                pending.pop(0)
            continue
        found = list(HEREDOC_RE.finditer(line))
        if found:
            used = True
            if _feeds_interpreter(line[:found[0].start()]):
                to_interpreter = True
            pending = [(m.group(3), bool(m.group(1))) for m in found]
            line = HEREDOC_RE.sub(" ", line)
        out.append(line)
    return "\n".join(out), used, to_interpreter


def _feeds_interpreter(prefix):
    """True if the command receiving the heredoc is an interpreter reading code from stdin."""
    segs = split_segments(prefix)
    if not segs:
        return False
    words = [w for w in segs[-1].split() if not re.fullmatch(r"\d*[<>]+&?\d*|-", w)]
    while words and (posixpath.basename(words[0]) in PREFIXES or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0])):
        words.pop(0)
    if words and posixpath.basename(words[0]) == "timeout":
        words = [w for w in words[1:] if not w.startswith("-")][1:]
    if not words:
        return False
    name = posixpath.basename(words[0])
    if not (name in INTERPRETERS or re.fullmatch(r"python3(\.\d+)?", name) or name in ("php", "psql", "mysql", "sqlite3")):
        return False
    return not any(not w.startswith("-") for w in words[1:])


def split_segments(cmd):
    """Quote-aware split into simple commands on &&, ||, ;, |, & and newlines."""
    segs, cur, i, n = [], [], 0, len(cmd)
    quote, depth = None, 0
    while i < n:
        c = cmd[i]
        if quote:
            cur.append(c)
            if c == "\\" and quote == '"' and i + 1 < n:
                cur.append(cmd[i + 1]); i += 2; continue
            if c == quote:
                quote = None
            i += 1; continue
        if c == "\\" and i + 1 < n:
            if cmd[i + 1] == "\n":
                cur.append(" "); i += 2; continue
            cur.append(c); cur.append(cmd[i + 1]); i += 2; continue
        if c in "'\"":
            quote = c; cur.append(c); i += 1; continue
        if c == "`":
            quote = "`"; cur.append(c); i += 1; continue
        if cmd.startswith("$(", i):
            depth += 1; cur.append("$("); i += 2; continue
        if c == ")" and depth:
            depth -= 1; cur.append(c); i += 1; continue
        if depth:
            cur.append(c); i += 1; continue
        two = cmd[i:i + 2]
        if two in ("&&", "||"):
            segs.append("".join(cur)); cur = []; i += 2; continue
        if c == "&" and (i + 1 < n and cmd[i + 1] == ">" or (cur and cur[-1] in "<>")):
            cur.append(c); i += 1; continue
        if c in ";|\n&":
            segs.append("".join(cur)); cur = []; i += 1; continue
        cur.append(c); i += 1
    segs.append("".join(cur))
    return [s.strip() for s in segs if s.strip()]


def lex(segment):
    lx = shlex.shlex(segment, posix=True, punctuation_chars="<>&|;()")
    lx.whitespace_split = True
    return list(lx)


PREFIXES = {"sudo", "time", "nohup", "exec", "env", "nice", "ionice", "stdbuf", "command", "builtin", "xargs"}
PATTERN_FIRST = {"sed", "awk", "gawk", "grep", "egrep", "fgrep", "rg", "jq", "perl"}
TEXT_ARGS = {"echo", "printf"}
INTERPRETERS = {"python", "python3", "python2", "node", "perl", "ruby", "bash", "sh", "zsh", "dash", "source", "."}
NETWORK = {"curl", "wget", "ssh", "scp", "sftp", "rsync", "ping", "nc", "ncat", "netcat", "telnet", "nslookup",
           "dig", "host", "traceroute", "ftp", "http", "https"}
BARE_HOST_CMDS = {"ssh", "ping", "nc", "ncat", "netcat", "telnet", "nslookup", "dig", "host", "traceroute", "ftp", "curl", "wget", "http", "https"}
AMBIGUOUS_CMDS = {"eval", "for", "while", "until", "select"}
KEYWORDS = {"if", "then", "else", "elif", "fi", "do", "done", "case", "esac", "!", "[[", "]]", "{", "}"}
PROCESS_PATTERN_CMDS = {"pkill", "pgrep", "killall"}
PACKAGE_VERBS = {
    "pip": {"install", "uninstall", "download"}, "pip3": {"install", "uninstall", "download"},
    "apt": {"install", "remove", "purge", "reinstall"}, "apt-get": {"install", "remove", "purge", "reinstall"},
    "yum": {"install", "remove", "erase"}, "dnf": {"install", "remove", "erase"}, "apk": {"add", "del"},
    "npm": {"install", "i", "uninstall", "remove", "rm", "un", "add"}, "yarn": {"add", "remove"},
    "pnpm": {"add", "install", "i", "remove", "rm"}, "cargo": {"install", "uninstall"}, "gem": {"install", "uninstall"},
}
REDIRECT_RE = re.compile(r"^[0-9]*(?:&?>>?|<|>\||>&|<&|&>>?)$")
CURL_PATH_FLAGS = {"-o", "--output", "-O", "--output-document", "-T", "--upload-file", "-K", "--config", "-c", "-b",
                   "--cookie-jar", "--cacert", "--cert", "--key"}


def extract_bash(cmd):
    ex = Extraction(status=NO_TARGET)
    body, heredoc, to_interpreter = strip_heredocs(cmd)
    if heredoc:
        ex.reasons.append("heredoc")
    if to_interpreter:
        ex.reasons.append("heredoc_to_interpreter")
    if "$(" in body or "`" in body:
        ex.reasons.append("command_substitution")
    try:
        segments = split_segments(body)
    except Exception:  # pragma: no cover - defensive
        segments = [body]
    ex.segments = segments
    cwd = None
    ambiguous = "command_substitution" in ex.reasons or to_interpreter

    for seg in segments:
        try:
            words = lex(seg)
        except ValueError:
            ambiguous = True
            ex.reasons.append("shlex_error")
            for tok in re.findall(r"[^\s'\"<>|;&()]+", seg):
                _literal(ex, tok, cwd)
            continue
        cwd, amb = _simple_command(ex, words, cwd)
        ambiguous = ambiguous or amb

    if ambiguous:
        ex.status = AMBIGUOUS
    elif ex.targets:
        ex.status = OK
    else:
        ex.status = NO_TARGET
    return ex


def _literal(ex, tok, cwd, origin="literal"):
    tok = tok.strip("'\"")
    if is_url(tok):
        ex.add(HOST, norm_host(tok), origin)
    elif IP_RE.match(tok) or CIDR_RE.match(tok):
        ex.add(HOST, tok.split("/")[0].split(":")[0], origin)
    elif is_path_like(tok):
        if GLOB_CHARS & set(tok):
            d = glob_dir(tok)
            if d:
                _add_path(ex, d, cwd, "glob_dir")
        else:
            _add_path(ex, tok, cwd, origin)


def _add_path(ex, tok, cwd, origin):
    p = norm_path(tok)
    if not p or p in IGNORED_PATHS or p == "/":
        return
    ex.add(PATH, p, origin)
    if cwd and not p.startswith(("/", "~")):
        ex.add(PATH, norm_path(posixpath.normpath(posixpath.join(cwd, p))), "resolved")


def _simple_command(ex, words, cwd):
    """Process one simple command. Returns (new cwd, ambiguous)."""
    ambiguous = False
    # Redirections first.
    args, i = [], 0
    while i < len(words):
        w = words[i]
        if w == "<<<":  # here-string: next word is a string
            i += 2; continue
        if REDIRECT_RE.match(w) and set(w) & set("<>"):
            if args and re.fullmatch(r"\d", args[-1]):
                args.pop()
            nxt = words[i + 1] if i + 1 < len(words) else None
            if nxt is not None and not (w.endswith("&") and re.fullmatch(r"\d+|-", nxt)):
                if is_url(nxt):
                    ex.add(HOST, norm_host(nxt), "redirect")
                elif "$" not in nxt:
                    _add_path(ex, nxt, cwd, "redirect")
            i += 2; continue
        if w in ("(", ")", "{", "}", "!", "((", "))"):
            i += 1; continue
        args.append(w)
        i += 1
    # Leading shell keywords (if/then/else/do ...) are skipped.
    while args and args[0] in KEYWORDS:
        args.pop(0)
    # Env assignments: URL or path values are recorded; then prefix commands.
    while args and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", args[0]):
        val = args.pop(0).split("=", 1)[1]
        if is_url(val):
            ex.add(HOST, norm_host(val), "assignment")
        elif is_path_like(val) and "/" in val and not GLOB_CHARS & set(val):
            _add_path(ex, val, cwd, "assignment")
    while args and posixpath.basename(args[0]) in PREFIXES:
        name = posixpath.basename(args.pop(0))
        while args and (args[0].startswith("-") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", args[0])):
            flag = args.pop(0)
            if name == "sudo" and flag in ("-u", "-g") and args:
                args.pop(0)
    if args and posixpath.basename(args[0]) == "timeout":
        args.pop(0)
        while args and args[0].startswith("-"):
            args.pop(0)
        if args:
            args.pop(0)  # duration
    if not args:
        return cwd, ambiguous
    cmd = posixpath.basename(args[0])
    rest = args[1:]
    if "/" in args[0] and is_path_like(args[0]) and "$" not in args[0]:
        _add_path(ex, args[0], cwd, "executable")

    if cmd in PROCESS_PATTERN_CMDS:
        return cwd, ambiguous
    if cmd == "su" and "-c" in rest:
        ex.reasons.append("inline_code:su")
        return cwd, True

    if cmd in AMBIGUOUS_CMDS:
        ex.reasons.append(f"control_flow:{cmd}")
        for a in rest:
            _literal(ex, a, cwd)
        return cwd, True

    if cmd in ("cd", "pushd"):
        dest = next((a for a in rest if not a.startswith("-")), None)
        if dest is None or "$" in dest or dest.startswith("~"):
            return None, ambiguous
        d = norm_path(dest)
        if d.startswith("/"):
            return posixpath.normpath(d), ambiguous
        return (posixpath.normpath(posixpath.join(cwd, d)) if cwd else None), ambiguous

    # Package managers.
    manager, after = None, None
    if cmd in PACKAGE_VERBS:
        manager, after = cmd, rest
    elif cmd.startswith("python") and rest[:2] == ["-m", "pip"]:
        manager, after = "pip", rest[2:]
    if manager:
        verbs = PACKAGE_VERBS[manager]
        pos = next((j for j, a in enumerate(after) if not a.startswith("-")), None)
        if pos is not None and after[pos] in verbs:
            skip_next = False
            for a in after[pos + 1:]:
                if skip_next:
                    skip_next = False
                    if is_path_like(a):
                        _add_path(ex, a, cwd, "arg")
                    continue
                if a in ("-r", "--requirement", "-c", "--constraint", "-e", "--editable", "-t", "--target", "-i", "--index-url", "--extra-index-url", "-f", "--find-links"):
                    skip_next = True
                    continue
                if a.startswith("-"):
                    continue
                if is_url(a):
                    ex.add(HOST, norm_host(a), "arg")
                elif is_path_like(a) and ("/" in a or a.endswith((".txt", ".whl", ".deb", ".tar.gz", ".tgz", ".zip", ".rpm"))):
                    _add_path(ex, a, cwd, "arg")
                else:
                    ex.add(PACKAGE, norm_package(a), "package")
            return cwd, ambiguous

    # Services.
    if cmd == "systemctl":
        non_flags = [a for a in rest if not a.startswith("-")]
        for unit in non_flags[1:]:
            ex.add(OTHER, unit.removesuffix(".service"), "service")
        return cwd, ambiguous
    if cmd == "service":
        non_flags = [a for a in rest if not a.startswith("-")]
        if non_flags:
            ex.add(OTHER, non_flags[0].removesuffix(".service"), "service")
        return cwd, ambiguous

    # Interpreters.
    if cmd in INTERPRETERS or re.fullmatch(r"python3(\.\d+)?", cmd):
        if "-c" in rest or "-e" in rest:
            ex.reasons.append(f"inline_code:{cmd}")
            code_idx = rest.index("-c") if "-c" in rest else rest.index("-e")
            code = rest[code_idx + 1] if code_idx + 1 < len(rest) else ""
            for s in re.findall(r"['\"]([^'\"\n]+)['\"]", code):
                if "/" in s and is_path_like(s):
                    _add_path(ex, s, cwd, "literal")
            return cwd, True
        if "-m" in rest:
            j = rest.index("-m")
            rest = rest[j + 2:]  # module name is not a target; remaining args are generic
        else:
            script = next((a for a in rest if not a.startswith("-")), None)
            if script is not None:
                _add_path(ex, script, cwd, "script")
                rest = rest[rest.index(script) + 1:]

    # Network commands.
    if cmd in NETWORK:
        pending = None  # flag whose value comes next
        for a in rest:
            if pending:
                flag, pending = pending, None
                if flag in CURL_PATH_FLAGS and is_path_like(a):
                    _add_path(ex, a, cwd, "flag_value")
                elif a.startswith("@") and is_path_like(a[1:]):  # data from a file: -d @file
                    _add_path(ex, a[1:], cwd, "flag_value")
                continue
            if cmd in ("curl", "wget") and a in CURL_PATH_FLAGS | {"-d", "--data", "--data-binary", "--data-raw", "-F", "-H", "-X", "-u", "--header", "--request", "--user", "-A", "-w", "--write-out"}:
                pending = a
                continue
            if a.startswith("-"):
                continue
            if is_url(a):
                ex.add(HOST, norm_host(a), "url")
            elif cmd in ("scp", "rsync", "sftp") and re.match(r"^([^/\s]+@)?[A-Za-z0-9.-]+:", a):
                ex.add(HOST, norm_host(a.split(":", 1)[0]), "remote")
            elif cmd in BARE_HOST_CMDS and (IP_RE.match(a.split("@")[-1]) or DOMAIN_RE.match(a.split("@")[-1])):
                ex.add(HOST, norm_host(a), "host_arg")
            elif is_path_like(a):
                _add_path(ex, a, cwd, "arg")
        return cwd, ambiguous

    if cmd == "git" and rest[:1] == ["clone"]:
        for a in rest[1:]:
            if is_url(a) or re.match(r"^[^/\s]+@[A-Za-z0-9.-]+:", a):
                ex.add(HOST, norm_host(a if is_url(a) else a.split(":", 1)[0]), "url")
            elif not a.startswith("-") and is_path_like(a):
                _add_path(ex, a, cwd, "arg")
        return cwd, ambiguous

    # Generic command: path-like arguments.
    if cmd in TEXT_ARGS:
        return cwd, ambiguous
    skip_pattern = cmd in PATTERN_FIRST and not any(a in ("-e", "-f", "--regexp", "--expression", "--file") for a in rest)
    pattern_flag_value = False
    for a in rest:
        if pattern_flag_value:
            pattern_flag_value = False
            continue
        if cmd in PATTERN_FIRST and a in ("-e", "--regexp", "--expression"):
            pattern_flag_value = True
            continue
        if a.startswith("-"):
            if "=" in a:
                val = a.split("=", 1)[1]
                if is_path_like(val) and not GLOB_CHARS & set(val):
                    _add_path(ex, val, cwd, "flag_value")
            continue
        if skip_pattern:
            skip_pattern = False
            continue
        _literal(ex, a, cwd, "arg")
    return cwd, ambiguous


def extract_target(call):
    """Extraction for a ToolCall (name, arguments)."""
    name, args = call.name, call.arguments
    if name in ("text_editor", "str_replace", "str_replace_editor_placeholder"):
        ex = Extraction(status=NO_TARGET)
        if isinstance(args.get("path"), str):
            _add_path(ex, args["path"], None, "editor_path")
        ex.status = OK if ex.targets else NO_TARGET
        return ex
    if name == "bash" and isinstance(args.get("command"), str):
        return extract_bash(args["command"])
    if name == "python":
        ex = Extraction(status=AMBIGUOUS, reasons=["python_tool"])
        code = args.get("code") if isinstance(args.get("code"), str) else args.get("command", "")
        for s in re.findall(r"['\"]([^'\"\n]+)['\"]", code or ""):
            if "/" in s and is_path_like(s):
                _add_path(ex, s, None, "literal")
        return ex
    return Extraction(status=NO_TARGET, reasons=[f"tool:{name}"])


def command_string(call):
    """The command a reader could quote: bash command or python code; None for other tools."""
    if call.name == "bash" and isinstance(call.arguments.get("command"), str):
        return call.arguments["command"]
    if call.name == "python":
        v = call.arguments.get("code", call.arguments.get("command"))
        return v if isinstance(v, str) else None
    return None


def core_command(call):
    """First simple command that is not cd (at least 2 tokens), or None."""
    cmd = command_string(call)
    if cmd is None or call.name != "bash":
        return None
    body, _, _ = strip_heredocs(cmd)
    for seg in split_segments(body):
        try:
            words = lex(seg)
        except ValueError:
            words = seg.split()
        if not words or posixpath.basename(words[0]) in ("cd", "pushd"):
            continue
        return seg if len(words) >= 2 else None
    return None


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

PATH_BEFORE = r"(?<![A-Za-z0-9_./~-])"
PATH_AFTER = r"(?![A-Za-z0-9_/~-])(?!\.[A-Za-z0-9_/~-])"


def boundary_re(s, ignore_case=False):
    return re.compile(PATH_BEFORE + re.escape(s) + PATH_AFTER, re.I if ignore_case else 0)


def path_forms(p):
    """Strings that count as naming path p: the full path and suffixes of >= 2 components."""
    forms = {p}
    comps = [c for c in p.split("/") if c]
    for j in range(1, len(comps) - 1):
        forms.add("/".join(comps[j:]))
    if not p.startswith("/") and len(comps) >= 2:
        forms.add("/".join(comps))
    return {f for f in forms if f}


def basename(p):
    comps = [c for c in p.split("/") if c]
    return comps[-1] if len(comps) >= 2 else None


def find_target(text, targets):
    """First (target, form, span) whose form appears in text at token boundaries."""
    best = None
    n_paths = sum(t.kind == PATH for t in targets)
    for t in targets:
        if t.kind == PATH and n_paths > 1 and t.value.startswith("/") and t.value.count("/") == 1:
            continue  # a one-component directory (/tmp, /opt) only counts when it is the only path target
        forms = path_forms(t.value) if t.kind == PATH else {t.value}
        for f in sorted(forms, key=len, reverse=True):
            if len(f) < 2:
                continue
            m = boundary_re(f, ignore_case=t.kind != PATH).search(text)
            if m and (best is None or m.start() < best[2][0]):
                best = (t, f, m.span())
    return best


def find_basename(text, targets):
    for t in targets:
        if t.kind != PATH:
            continue
        b = basename(t.value)
        if b and len(b) >= 2:
            m = boundary_re(b).search(text)
            if m:
                return (t, b, m.span())
    return None
