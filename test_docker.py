import docker
import os

print(f"User: {os.getuid()}")
print(f"Group: {os.getgid()}")
if os.path.exists('/var/run/docker.sock'):
    print("Socket exists")
    print(f"Socket perms: {oct(os.stat('/var/run/docker.sock').st_mode)}")
else:
    print("Socket does NOT exist")

try:
    # Testing from_env
    client = docker.from_env()
    containers = client.containers.list()
    print(f"Success! Found {len(containers)} containers.")
    for c in containers:
        print(f" - {c.name}")
except Exception as e:
    print(f"Error: {e}")
