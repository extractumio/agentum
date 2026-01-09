# Run this diagnostic script
docker run --rm -it --security-opt seccomp=unconfined python:3.11-slim bash -c '
echo "=== Kernel Version ==="
uname -r
uname -a

echo ""
echo "=== Architecture ==="
arch

echo ""
echo "=== LSM Modules ==="
cat /sys/kernel/security/lsm 2>/dev/null || echo "Cannot read /sys/kernel/security/lsm"

echo ""
echo "=== Landlock Directory ==="
ls -la /sys/kernel/security/landlock/ 2>/dev/null || echo "Landlock directory does not exist"

echo ""
echo "=== Kernel Config (if available) ==="
if [ -f /proc/config.gz ]; then
    zcat /proc/config.gz | grep -i landlock
else
    echo "/proc/config.gz not available"
fi

echo ""
echo "=== Docker Host Info ==="
cat /etc/os-release 2>/dev/null | head -5
'