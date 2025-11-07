def generate_user_token(username: str, env: Environment, project: Project) -> str:
    """Generate API token for user by SSHing to the appropriate FQDN"""
    try:
        # Get the FQDN from the environment-project mapping
        fqdn = ENV_PROJECT_MAP.get(env, {}).get(project)
        if not fqdn:
            logger.error(f"No FQDN configured for environment '{env}' and project '{project}'")
            raise Exception(f"No FQDN configured for environment '{env}' and project '{project}'")
        
        logger.info(f"Generating token for user '{username}' on {fqdn} ({env.value}/{project.value})")
        
        # Build the SSH command to execute on remote host
        remote_cmd = f"pbrun test 'root=rstudio-server generate-api-token' user '{username}-token' {username} | awk -F'|' '/\\|/ {{gsub(/^[ \\t]+|[ \\t]+$/, \"\", $2); print $2; exit}}'"
        
        # Password for SSH (replace 'your_password' with actual password)
        ssh_password = "your_password_here"
        
        # Build the full SSH command with sshpass
        ssh_cmd = f"sshpass -p '{ssh_password}' ssh -o StrictHostKeyChecking=no {fqdn} '{remote_cmd}'"
        
        logger.info(f"Executing SSH command to {fqdn} for user: {username}")
        logger.debug(f"SSH command: {ssh_cmd}")

        # Run the SSH command
        result = subprocess.run(
            ssh_cmd,
            shell=True,
            capture_output=True,
            text=True,
            check=True
        )

        logger.info(f"Token generation command executed successfully on {fqdn} for user: {username}")

        token = result.stdout.strip()
        if not token:
            logger.error(f"No token extracted for user {username} from {fqdn}")
            logger.error(f"Full output: {result.stdout}")
            logger.error(f"Error output: {result.stderr}")
            raise Exception(f"No token found in command output from {fqdn}")

        logger.info(f"Token successfully extracted for user {username} from {fqdn}")
        return token

    except subprocess.CalledProcessError as e:
        logger.error(f"Token generation command failed for user {username} on {fqdn}")
        logger.error(f"SSH error: {e.stderr}")
        logger.error(f"Return code: {e.returncode}")
        raise Exception(f"Token generation failed on {fqdn}: {e.stderr}")
    except Exception as e:
        logger.error(f"Error generating token for user {username} on {fqdn}: {str(e)}")
        raise Exception(f"Error generating token on {fqdn}: {str(e)}")
