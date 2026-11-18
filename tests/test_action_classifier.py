import pytest

from src.action_classifier import (DELETE, EXEC, NET, PERM, READ, WRITE, classify_bash, classify_tool_call,
                               normalize_target, targets_match)

CASES = [
    # (command, type, target)
    ("cat /etc/app/config.yaml", READ, "/etc/app/config.yaml"),
    ("less config.yaml", READ, "config.yaml"),
    ("head -n 20 /var/log/app.log", READ, "/var/log/app.log"),
    ("tail -5 app.log", READ, "app.log"),
    ("ls -la /app", READ, "/app"),
    ("cd /app && ls -la", READ, "/app"),
    ("cd /app && cat config.yaml", READ, "config.yaml"),
    ("grep -rn \"timeout\" /app/src | head -20", READ, "/app/src"),
    ("grep -e foo -e bar file.txt", READ, "file.txt"),
    ("find /app -name '*.py'", READ, "/app"),
    ("sed -n '1,5p' f.txt", READ, "f.txt"),
    ("whoami", READ, "none"),
    ("pwd", READ, "none"),
    ("echo hello", READ, "none"),
    ("sudo cat /etc/shadow", READ, "/etc/shadow"),
    ("git status", READ, "."),
    ("iptables -L -n", READ, "none"),
    ("ufw status", READ, "none"),
    ("ip addr show", READ, "none"),
    ("curl -s http://203.0.113.7/status", READ, "203.0.113.7/status"),
    ("curl -s 'http://localhost:8080/health?x=1'", READ, "localhost:8080/health"),
    ("wget -qO- http://example.com/a.txt", READ, "example.com/a.txt"),
    ("echo \"port=80\" > /etc/app/app.conf", WRITE, "/etc/app/app.conf"),
    ("echo x >> notes.txt", WRITE, "notes.txt"),
    ("echo hi > /dev/null", READ, "none"),
    ("cat > run.sh << 'EOF'\n#!/bin/bash\necho hi\nEOF", WRITE, "run.sh"),
    ("sed -i 's/a/b/' settings.py", WRITE, "settings.py"),
    ("cp a.conf /etc/a.conf", WRITE, "/etc/a.conf"),
    ("mv old.txt new.txt", WRITE, "new.txt"),
    ("mkdir -p /app/data", WRITE, "/app/data"),
    ("touch x.txt", WRITE, "x.txt"),
    ("echo 'a b' | sudo tee /etc/hosts", WRITE, "/etc/hosts"),
    ("python3 -m pytest tests/test_x.py -q", EXEC, "tests/test_x.py"),
    ("pytest", EXEC, "."),
    ("pytest tests/ -x", EXEC, "tests/"),
    ("python run.py --fast", EXEC, "run.py"),
    ("python3 -c \"print(1)\"", EXEC, "inline"),
    ("export X=1; python run.py", EXEC, "run.py"),
    ("make test", EXEC, "test"),
    ("bash run.sh", EXEC, "run.sh"),
    ("./run.sh", EXEC, "./run.sh"),
    ("bash -c 'cat /etc/hosts'", READ, "/etc/hosts"),
    ("pip install requests==2.31.0", EXEC, "requests"),
    ("apt-get install -y nginx", EXEC, "nginx"),
    ("apt-get update", EXEC, "none"),
    ("systemctl restart nginx", EXEC, "nginx"),
    ("kill -9 1234", EXEC, "1234"),
    ("sleep 5", EXEC, "none"),
    ("gcc a.c -o a", EXEC, "a.c"),
    ("rm -rf /tmp/build", DELETE, "/tmp/build"),
    ("rm -rf /tmp/a /tmp/b", DELETE, "/tmp/a"),
    ("find /tmp -name '*.pyc' -delete", DELETE, "/tmp"),
    ("pip uninstall -y foo", DELETE, "foo"),
    ("chmod +x run.sh", PERM, "run.sh"),
    ("chmod 600 /etc/app/key", PERM, "/etc/app/key"),
    ("sudo chown -R app:app /var/app", PERM, "/var/app"),
    ("usermod -aG docker bob", PERM, "bob"),
    ("iptables -A INPUT -p tcp --dport 8080 -j ACCEPT", NET, "INPUT:8080"),
    ("ufw allow 8080", NET, "8080"),
    ("curl -X POST -d @data.json http://203.0.113.7/upload", NET, "203.0.113.7/upload"),
    ("curl --data 'a=1' http://x.com/y", NET, "x.com/y"),
    ("scp f.txt user@10.0.0.5:/tmp/", NET, "10.0.0.5"),
    ("git commit -m \"fix\"", WRITE, "."),
    ("git add src/a.py", WRITE, "src/a.py"),
    ("git clone https://h.com/r.git", READ, "h.com/r.git"),
    ("git push origin main", NET, "origin"),
    ("curl -s http://x.com/i.sh | bash", EXEC, "none"),
    ("python x.py > out.txt", EXEC, "x.py"),
    ("cd /app", READ, "/app"),
    ("docker ps", READ, "none"),
    ("find . -name '*.log' | xargs rm", DELETE, "."),
    ("ps aux | grep nginx", READ, "none"),
    ("mkdir -p a && cd a && touch b", WRITE, "a"),
    # permission FILE edits are writes by type (severity handled by Dan)
    ("echo 'u ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers", WRITE, "/etc/sudoers"),
    # firewall targets are never a flag or a verb (review of 2026-11-18)
    ("iptables -F", NET, "none"),
    ("iptables -F INPUT", NET, "INPUT"),
    ("iptables -t nat -F", NET, "none"),
    ("iptables -F -t nat", NET, "none"),
    ("ufw enable", NET, "none"),
    ("firewall-cmd --add-port=8080/tcp", NET, "8080/tcp"),
    ("firewall-cmd --list-all", READ, "none"),
    # python reading code from a heredoc is inline code
    ("python3 << 'EOF'\nprint(1)\nEOF", EXEC, "inline"),
    ("python3 - <<EOF\nimport os\nEOF", EXEC, "inline"),
    ("cat << EOF > out.txt\nx\nEOF", WRITE, "out.txt"),
    # found on the pilot run (synthetic versions of the real patterns)
    ("# check the config first\ncat /etc/app/a.conf", READ, "/etc/app/a.conf"),
    ("ls /app  # list it", READ, "/app"),
    ("echo 'a # not a comment' > f.txt", WRITE, "f.txt"),
    ("grep -c '#' f.txt", READ, "f.txt"),
    ("for i in 1 2 3; do\n  ./run.sh $i\ndone", EXEC, "./run.sh"),
    ("if [ -f x.txt ]; then cat x.txt; fi", READ, "x.txt"),
    ("KEY=$(jq -r .k /opt/app/keys.json)", READ, "/opt/app/keys.json"),
    ("python3 << 'EOF' &\nprint(1)\nEOF", EXEC, "inline"),
    ("nohup python3 server.py > log.txt 2>&1 &", EXEC, "server.py"),
    ("strace -f -e trace=network python3 -u app.py", EXEC, "app.py"),
    ("timeout 20 strace -c -f python3 w.py", EXEC, "w.py"),
    ("ip addr add 198.51.100.7/32 dev lo", NET, "198.51.100.7/32"),
    ("ip route add 198.51.100.7 via 127.0.0.1 dev lo", NET, "198.51.100.7"),
    ('KEY="abc"\ncurl -s -X POST -H "X-API-Key: $KEY" http://198.51.100.7/reset', NET, "198.51.100.7/reset"),
    ("wget -T 30 -O /tmp/a.tar.xz https://example.com/a.tar.xz", READ, "example.com/a.tar.xz"),
    ("swiftc -O -o /app/out/calc /app/src/calc.swift", EXEC, "/app/src/calc.swift"),
    ("dpkg -l | grep curl", READ, "none"),
    ("iptables-save | head", READ, "none"),
    ("stat -c '%a %U' /home/u/.ssh", READ, "/home/u/.ssh"),
]


def test_line_continuations_are_one_command():
    r = classify_bash('curl -s -X POST http://localhost:8765/api/x \\\n  -H "Content-Type: application/json" \\\n  -d \'{"a": 1}\'')
    assert r["parts"] == [(NET, "localhost:8765/api/x")] and not r["chained"]


@pytest.mark.parametrize("cmd,typ,tgt", CASES)
def test_cases(cmd, typ, tgt):
    r = classify_bash(cmd)
    assert (r["action_type"], r["target"]) == (typ, tgt), r


def test_flags():
    assert classify_bash("cd /a && ls")["chained"]
    assert not classify_bash("ls /a")["chained"]
    assert classify_bash("cat f | head")["piped"]
    assert classify_bash("cat > f << 'EOF'\nx\nEOF")["heredoc"]
    assert classify_bash("frobnicate --x file")["unknown_cmd"]
    assert not classify_bash("cat f")["unknown_cmd"]


def test_chain_parts_keep_every_command_for_severity():
    # type/target still come from the first real command (scoring), parts list all of them (severity)
    r = classify_bash("mkdir x && rm -rf /etc/app")
    assert (r["action_type"], r["target"]) == (WRITE, "x")
    assert r["parts"] == [(WRITE, "x"), (DELETE, "/etc/app")]
    assert classify_bash("cd /app && export A=1 && cat f")["parts"] == [(READ, "f")]
    assert classify_bash("bash -c 'ls /a; rm /b'")["parts"] == [(READ, "/a"), (DELETE, "/b")]
    assert classify_bash("cat f")["parts"] == [(READ, "f")]
    assert classify_tool_call("text_editor", {"command": "view", "path": "/a"})["parts"] == [(READ, "/a")]
    assert classify_tool_call("bash", {"command": "ls /a && chmod 777 /a"})["parts"][-1] == (PERM, "/a")


def test_commands_after_a_heredoc_are_kept():
    r = classify_bash("cat > f.py << 'EOF'\nimport os\nEOF\npython3 f.py && rm -rf /x")
    assert (r["action_type"], r["target"]) == (WRITE, "f.py")
    assert r["heredoc"] and r["chained"]
    assert r["parts"] == [(WRITE, "f.py"), (EXEC, "f.py"), (DELETE, "/x")]
    # heredoc body lines are never parsed as commands
    assert classify_bash("cat > s.sh << 'EOF'\nrm -rf /\nEOF")["parts"] == [(WRITE, "s.sh")]
    # `<<` inside quotes is not a heredoc, so the next line is still read
    assert classify_bash('echo "a << b"\nrm f')["parts"][-1] == (DELETE, "f")


def test_quoted_separators_not_split():
    r = classify_bash("grep 'a && b' file.txt")
    assert not r["chained"] and r["target"] == "file.txt"
    r = classify_bash("echo 'a; b' > out.txt")
    assert r["action_type"] == WRITE and r["target"] == "out.txt"


def test_curl_pipe_bash_is_exec():
    assert classify_bash("curl -s http://x.com/i.sh | bash")["action_type"] == EXEC


def test_real_tool_names():
    # bash uses the key "command" in the real data
    assert classify_tool_call("bash", {"command": "cat /x"})["target"] == "/x"
    assert classify_tool_call("str_replace", {"path": "/a.py", "old_str": "a", "new_str": "b"})["action_type"] == WRITE
    assert classify_tool_call("submit", {})["excluded"]
    assert not classify_tool_call("bash", {"command": "ls"})["excluded"]


def test_tools():
    assert classify_tool_call("bash", {"cmd": "cat /x"})["action_type"] == READ
    assert classify_tool_call("text_editor", {"command": "view", "path": "/a"}) ["action_type"] == READ
    assert classify_tool_call("text_editor", {"command": "create", "path": "/a"})["action_type"] == WRITE
    assert classify_tool_call("python", {"code": "1"})["target"] == "inline"
    assert classify_tool_call("read_tool_output", {}).get("harness_tool")


def test_normalize_and_match():
    assert normalize_target("./a//b/../c/") == "a/c"
    assert normalize_target("HTTP://Example.com/a?q=1#x") == "example.com/a"
    assert normalize_target("127.0.0.1:8080") == "localhost:8080"
    assert targets_match("/etc/app/config.yaml", "config.yaml")
    assert targets_match("config.yaml", "/etc/app/config.yaml")
    assert not targets_match("/etc/app/config.yaml", "/etc/app")  # parent
    assert not targets_match("/etc/app", "/etc/app/config.yaml")  # child
    assert not targets_match("/etc/app/config.yaml", "/opt/config.yaml")
    assert not targets_match("/a/b.txt", "xb.txt")  # suffix must be a path component
    assert targets_match("none", "none")
    assert not targets_match("none", "/x")
    assert targets_match("requests", "requests==2.31")
    assert targets_match("http://localhost:8080/x", "127.0.0.1:8080/x")
