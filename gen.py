def generate_user_token(username: str) -> str:
    """Generate API token for user using the pbrun command"""
    try:
        import shlex
        
        # Build the command parts safely
        token_name = f"{username}-token"
        cmd_parts = [
            'pbrun', 'test', 
            'root=rstudio-server generate-api-token', 
            'user', 
            username,      # The username
            token_name     # The token name: username-token
        ]
        
        # Create the full command with awk processing
        cmd = f"{' '.join(shlex.quote(part) for part in cmd_parts)} | awk -F'|' '/\\|/ {{gsub(/^[ \\t]+|[ \\t]+$/, "", $2); print $2; exit}}'"
        
        logger.info(f"Executing token generation command for user: {username}")
        logger.debug(f"Command: {cmd}")
        
        # Execute the command
        result = subprocess.run(
            cmd, 
            shell=True,
            capture_output=True, 
            text=True, 
            check=True
        )
        
        logger.info(f"Token generation command executed successfully for user: {username}")
        logger.debug(f"Command stdout: {result.stdout}")
        if result.stderr:
            logger.debug(f"Command stderr: {result.stderr}")
        
        # Parse the output
        token = result.stdout.strip()
        
        if token:
            logger.info(f"Token successfully extracted for user {username}: {token[:10]}...")
            return token
        else:
            logger.error(f"No token found in command output for user {username}")
            logger.error(f"Raw stdout: {repr(result.stdout)}")
            logger.error(f"Raw stderr: {repr(result.stderr)}")
            raise Exception("No token found in command output")
        
    except subprocess.CalledProcessError as e:
        logger.error(f"Token generation command failed for user {username}")
        logger.error(f"Return code: {e.returncode}")
        logger.error(f"Command stdout: {e.stdout}")
        logger.error(f"Command stderr: {e.stderr}")
        raise Exception(f"Token generation command failed: {e.stderr}")
    except Exception as e:
        logger.error(f"Error generating token for user {username}: {str(e)}")
        raise Exception(f"Error generating token: {str(e)}")
