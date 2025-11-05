def generate_user_token(username: str) -> str:
    """Generate API token for user using the pbrun command"""
    try:
        # Build the command - much simpler approach
        cmd = f"pbrun test 'root=rstudio-server generate-api-token' user '{username}-token' {username} | awk -F'|' '/\\|/ {{gsub(/^[ \\t]+|[ \\t]+$/, \"\", $2); print $2; exit}}'"

        logger.info(f"Executing token generation command for user: {username}")
        logger.debug(f"Command: {cmd}")

        # Run the command
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            check=True
        )

        logger.info(f"Token generation command executed successfully for user: {username}")

        token = result.stdout.strip()
        if not token:
            logger.error(f"No token extracted for user {username}")
            logger.error(f"Full output: {result.stdout}")
            raise Exception("No token found in command output")

        logger.info(f"Token successfully extracted for user {username}")
        return token

    except subprocess.CalledProcessError as e:
        logger.error(f"Token generation command failed for user {username}")
        logger.error(f"Error output: {e.stderr}")
        raise Exception(f"Token generation command failed: {e.stderr}")
    except Exception as e:
        logger.error(f"Error generating token for user {username}: {str(e)}")
        raise Exception(f"Error generating token: {str(e)}")
