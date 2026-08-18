"""Quick test for the native host — sends a getCertificate request and prints the response."""
import json, struct, subprocess, sys, os

host_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "giddh_dsc_host.py")
python = sys.executable

msg = json.dumps({"action": "getCertificate"}).encode("utf-8")
framed = struct.pack("<I", len(msg)) + msg

proc = subprocess.run(
    [python, host_script],
    input=framed,
    capture_output=True,
    timeout=10,
)

print("=== STDOUT (raw bytes) ===")
print(proc.stdout[:200])
print("=== STDOUT (decoded) ===")
if len(proc.stdout) >= 4:
    resp_len = struct.unpack("<I", proc.stdout[:4])[0]
    resp_body = proc.stdout[4:4+resp_len]
    print(f"Response length: {resp_len}")
    try:
        parsed = json.loads(resp_body)
        print(json.dumps(parsed, indent=2))
    except:
        print(f"Raw: {resp_body}")
else:
    print("No valid response (too short)")

print("=== STDERR ===")
print(proc.stderr.decode("utf-8", errors="replace"))
