#!/bin/bash
# Test: Does bwrap isolation inherit to child processes?
# 
# This validates that if we wrap the AGENT process with bwrap,
# child processes (like Bash tool → ps aux) will inherit restrictions.

echo "=== Test 1: ps aux WITHOUT bwrap (current problem) ==="
echo "Running: bash -c 'ps aux | head -5'"
bash -c 'ps aux | head -5'
echo ""
echo "Result: All host processes visible (BAD for security)"
echo ""

echo "=== Test 2: ps aux WITH bwrap wrapping parent ==="
echo "Running: bwrap ... -- bash -c 'bash -c \"ps aux\"'"
echo ""

# Key: --unshare-pid creates new PID namespace
# Child processes inherit this namespace!
bwrap \
    --ro-bind /usr /usr \
    --ro-bind /lib /lib \
    --ro-bind /lib64 /lib64 2>/dev/null \
    --ro-bind /bin /bin \
    --symlink usr/lib64 /lib64 2>/dev/null \
    --proc /proc \
    --dev /dev \
    --tmpfs /tmp \
    --unshare-pid \
    --unshare-ipc \
    --die-with-parent \
    -- bash -c 'echo "PID namespace test:"; ps aux; echo ""; echo "Process count: $(ps aux | wc -l)"'

echo ""
echo "Result: Only sandboxed processes visible (GOOD!)"
echo ""

echo "=== Test 3: Nested subprocess inheritance ==="
echo "Parent → Child → Grandchild all inherit restrictions"
echo ""

bwrap \
    --ro-bind /usr /usr \
    --ro-bind /lib /lib \
    --ro-bind /lib64 /lib64 2>/dev/null \
    --ro-bind /bin /bin \
    --symlink usr/lib64 /lib64 2>/dev/null \
    --proc /proc \
    --dev /dev \
    --tmpfs /tmp \
    --unshare-pid \
    --die-with-parent \
    -- bash -c '
        echo "Level 1 (bash): PID=$$"
        bash -c "
            echo \"Level 2 (nested bash): PID=\$\$\"
            python3 -c \"
import subprocess
import os
print(f\\\"Level 3 (python): PID={os.getpid()}\\\")
result = subprocess.run([\\\"ps\\\", \\\"aux\\\"], capture_output=True, text=True)
print(\\\"ps aux from Python subprocess:\\\")
print(result.stdout)
print(f\\\"Process count: {len(result.stdout.strip().split(chr(10)))}\\\")
\"
        "
    '

echo ""
echo "=== Test 4: File access restrictions ==="
echo ""

bwrap \
    --ro-bind /usr /usr \
    --ro-bind /lib /lib \
    --ro-bind /lib64 /lib64 2>/dev/null \
    --ro-bind /bin /bin \
    --symlink usr/lib64 /lib64 2>/dev/null \
    --proc /proc \
    --dev /dev \
    --tmpfs /tmp \
    --tmpfs /workspace \
    --unshare-pid \
    --die-with-parent \
    -- bash -c '
        echo "Trying to read /etc/passwd..."
        cat /etc/passwd 2>&1 || echo "BLOCKED: /etc/passwd not accessible"
        
        echo ""
        echo "Trying to read /etc/shadow..."
        cat /etc/shadow 2>&1 || echo "BLOCKED: /etc/shadow not accessible"
        
        echo ""
        echo "Trying to write to /workspace..."
        echo "test" > /workspace/test.txt && echo "SUCCESS: Can write to /workspace" || echo "BLOCKED"
        
        echo ""
        echo "Trying to write to /usr..."
        echo "test" > /usr/test.txt 2>&1 || echo "BLOCKED: /usr is read-only"
    '

echo ""
echo "=== CONCLUSION ==="
echo "If Test 2 and 3 show only ~2-5 processes instead of 50+,"
echo "then bwrap inheritance WORKS and we can use this approach!"
