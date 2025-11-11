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

====================================================================
import subprocess

ssh_pass = "Password"
fqdn = "fqdn"

cmd = "test1 test2 'root=rstudio-server generate-api-token' user 'user-token' username"

ssh_command = [
    "sshpass", "-p", ssh_pass,
    "ssh", "-o", "StrictHostKeyChecking=no", 
    f"user2@{fqdn}",
    cmd
]

try:
    result = subprocess.run(ssh_command, check=True, text=True, capture_output=True)
    output = result.stdout
    
    # The token is on the line that comes after the dashed line and has content between pipes
    lines = output.splitlines()
    for i, line in enumerate(lines):
        if '-----------------------------------' in line and i + 1 < len(lines):
            # Next line should contain the token between pipes
            token_line = lines[i + 1]
            if '|' in token_line:
                # Extract what's between the first and last pipe
                token = token_line.split('|')[1].strip()
                print(token)
                break
    
except subprocess.CalledProcessError as e:
    print("Error running command:", e.stderr)
