import subprocess

ssh_pass = "Password"
fqdn = "fqdn"

# Just run the command without processing, do processing in Python
cmd = "test1 test2 'root=rstudio-server generate-api-token' user 'user-token'"

ssh_command = [
    "sshpass", "-p", ssh_pass,
    "ssh", "-o", "StrictHostKeyChecking=no", 
    f"user2@{fqdn}",
    cmd
]

try:
    result = subprocess.run(ssh_command, check=True, text=True, capture_output=True)
    
    # Process the output in Python instead of awk
    output = result.stdout
    for line in output.splitlines():
        if '|' in line:
            parts = line.split('|')
            if len(parts) >= 2:
                token = parts[1].strip()
                print("Extracted token:", token)
                break
    else:
        print("No token found in output")
        print("Raw output:", output)
        
except subprocess.CalledProcessError as e:
    print("Error running command:", e.stderr)
    print("Return code:", e.returncode)
