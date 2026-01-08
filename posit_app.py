from fastapi import FastAPI, HTTPException, Header, Query
from pydantic import BaseModel, Field
import requests
import json
import os
import uvicorn
import re
import subprocess
from typing import List, Dict, Any, Optional
from enum import Enum
from datetime import datetime
import logging
import random
from collections import defaultdict

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('session_management.log')
    ]
)
logger = logging.getLogger("session_management")

app = FastAPI(title="Session Management API", version="2.0.0")

# Environment and Project enums
class Environment(str, Enum):
    DEV = "DEV"
    UAT = "UAT"
    PROD = "PROD"

class Project(str, Enum):
    PROJECT1 = "PROJECT1"
    PROJECT2 = "PROJECT2"

# Pydantic models for request/response
class LaunchSessionRequest(BaseModel):
    session_name: Optional[str] = Field(None, description="Optional custom session name. If not provided, will generate as 'JupyterLab Session {number}'")
    workbench: str = "JupyterLab"
    cluster: str = "Local"
    env: Environment = Field(..., description="Environment: DEV, UAT, or PROD")
    project: Project = Field(..., description="Project: PROJECT1 or PROJECT2")
    node_selection: Optional[str] = Field(None, description="For PROJECT1 only: 'P' or 'V' to select node. If not provided, defaults to 'P'")

class StopSessionRequest(BaseModel):
    session_ids: List[str] = Field(..., description="List of session IDs to stop")
    force_quit: bool = False
    suspend_session: bool = False
    env: Environment = Field(..., description="Environment: DEV, UAT, or PROD")
    project: Project = Field(..., description="Project: PROJECT1 or PROJECT2")

class LaunchSessionResponse(BaseModel):
    success: bool
    message: str
    session_url: str = None
    session_name: str = None
    selected_node: Optional[str] = None
    selected_server: Optional[str] = None
    error: str = None

class SessionInfo(BaseModel):
    session_id: str
    url: str
    session_name: str
    display_name: str

class GetSessionsResponse(BaseModel):
    success: bool
    message: str
    sessions: List[SessionInfo] = []
    error: str = None

class StopSessionResponse(BaseModel):
    success: bool
    message: str
    stopped_sessions: List[str] = []
    error: str = None

class TokenResponse(BaseModel):
    username: str
    token: str = None
    available_users: List[str] = []

class AvailableUsersResponse(BaseModel):
    available_users: List[str]

class UserAccessResponse(BaseModel):
    username: str
    user_groups: List[str]
    accessible_projects: Dict[str, List[str]]  # project -> list of environments
    has_access: bool

class ReloadResponse(BaseModel):
    success: bool
    message: str
    timestamp: str

class ServerConfigResponse(BaseModel):
    success: bool
    message: str
    servers: Dict[str, Dict[str, List[str]]]
    failed_servers: Dict[str, Dict[str, List[str]]]

class ServerPathConfig(BaseModel):
    env: Environment
    project: Project
    server_list_path: str

# API endpoints (relative paths)
LAUNCH_API = "/api/launch_session"
GET_SESSION_API = "/api/get_session"
STOP_SESSION_API = "/api/stop_session"

# Configuration files
TOKENS_FILE = "tokens.json"
GROUP_CONFIG_FILE = "group_config.json"

# Global variables to store data in memory
TOKENS_DATA = None
GROUP_CONFIG = None
SERVER_CONFIG = None  # Store server configuration
FAILED_SERVERS = defaultdict(lambda: defaultdict(list))  # Track failed servers by env/project
SERVER_PATHS = {}  # Store server list file paths for each env/project
TOKENS_LAST_MODIFIED = None
GROUP_CONFIG_LAST_MODIFIED = None
SERVER_CONFIG_LAST_MODIFIED = None

def load_server_paths_from_env() -> Dict[str, Dict[str, str]]:
    """
    Load server list file paths from environment variables.
    Expected format in .env file:
    DEV_PROJECT1_SERVER_LIST=/path/to/dev/project1/server-list.txt
    DEV_PROJECT2_SERVER_LIST=/path/to/dev/project2/server-list.txt
    UAT_PROJECT1_SERVER_LIST=/path/to/uat/project1/server-list.txt
    UAT_PROJECT2_SERVER_LIST=/path/to/uat/project2/server-list.txt
    PROD_PROJECT1_SERVER_LIST=/path/to/prod/project1/server-list.txt
    PROD_PROJECT2_SERVER_LIST=/path/to/prod/project2/server-list.txt
    """
    global SERVER_PATHS
    
    SERVER_PATHS = {}
    
    for env in Environment:
        env_key = env.value
        SERVER_PATHS[env_key] = {}
        
        for project in Project:
            project_key = project.value
            
            # Construct environment variable name
            env_var_name = f"{env_key}_{project_key}_SERVER_LIST"
            
            # Get the path from environment variable
            server_list_path = os.getenv(env_var_name)
            
            if server_list_path:
                SERVER_PATHS[env_key][project_key] = server_list_path
                logger.info(f"Loaded server list path for {env_key}/{project_key}: {server_list_path}")
            else:
                logger.warning(f"Environment variable {env_var_name} not found. Using default path.")
                # Create a default path pattern
                default_path = f"/opt/session-management/{env_key.lower()}/{project_key.lower()}/server-list.txt"
                SERVER_PATHS[env_key][project_key] = default_path
                logger.info(f"Using default path for {env_key}/{project_key}: {default_path}")
    
    logger.info(f"Server paths configuration loaded: {json.dumps(SERVER_PATHS, indent=2)}")
    return SERVER_PATHS

def get_server_list_path(env: Environment, project: Project) -> str:
    """Get the server list file path for a specific environment and project"""
    env_str = env.value
    project_str = project.value
    
    if not SERVER_PATHS:
        load_server_paths_from_env()
    
    path = SERVER_PATHS.get(env_str, {}).get(project_str)
    
    if not path:
        # Generate default path if not found
        default_path = f"/opt/session-management/{env_str.lower()}/{project_str.lower()}/server-list.txt"
        logger.warning(f"Server list path not found for {env_str}/{project_str}, using default: {default_path}")
        return default_path
    
    return path

def load_server_config_for_project(env: Environment, project: Project, force_reload: bool = False) -> List[str]:
    """
    Load server configuration from project-specific server-list.txt file.
    Expected format in file: server_fqdn (one per line)
    Example:
    dev-project1-server1.example.com
    dev-project1-server2.example.com
    """
    env_str = env.value
    project_str = project.value
    
    # Initialize global config if needed
    if SERVER_CONFIG is None:
        SERVER_CONFIG = defaultdict(lambda: defaultdict(list))
    
    try:
        # Get the specific server list file path for this project/environment
        server_file_path = get_server_list_path(env, project)
        
        # Check if file exists
        if not os.path.exists(server_file_path):
            logger.error(f"Server list file not found at {server_file_path} for {env_str}/{project_str}")
            
            # Create backup default servers
            default_servers = [f"{env_str.lower()}-{project_str.lower()}-server1.example.com"]
            
            # Update global config
            if env_str not in SERVER_CONFIG:
                SERVER_CONFIG[env_str] = {}
            SERVER_CONFIG[env_str][project_str] = default_servers
            
            logger.warning(f"Using default servers for {env_str}/{project_str}: {default_servers}")
            return default_servers
        
        # Check if file has been modified
        current_mtime = os.path.getmtime(server_file_path)
        
        # Check if we need to reload
        need_reload = force_reload or (env_str not in SERVER_CONFIG) or (project_str not in SERVER_CONFIG[env_str])
        
        # Reload if needed
        if need_reload or (SERVER_CONFIG_LAST_MODIFIED and current_mtime > SERVER_CONFIG_LAST_MODIFIED):
            servers = []
            
            with open(server_file_path, 'r') as file:
                for line_num, line in enumerate(file, 1):
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue  # Skip empty lines and comments
                    
                    # Remove any trailing comments
                    server = line.split('#')[0].strip()
                    
                    if server:
                        servers.append(server)
                        logger.debug(f"Added server {server} for {env_str}/{project_str} (line {line_num})")
            
            # Update global config
            if env_str not in SERVER_CONFIG:
                SERVER_CONFIG[env_str] = {}
            SERVER_CONFIG[env_str][project_str] = servers
            
            SERVER_CONFIG_LAST_MODIFIED = current_mtime
            logger.info(f"Server configuration reloaded for {env_str}/{project_str} at {datetime.now()}")
            logger.info(f"Loaded {len(servers)} servers for {env_str}/{project_str}: {servers}")
            
            return servers
        else:
            # Return cached servers
            return SERVER_CONFIG.get(env_str, {}).get(project_str, [])
            
    except Exception as e:
        logger.error(f"Error loading server configuration for {env_str}/{project_str}: {e}")
        
        # Return default servers if available
        default_servers = SERVER_CONFIG.get(env_str, {}).get(project_str, [])
        if default_servers:
            logger.warning(f"Using cached servers for {env_str}/{project_str} due to error: {default_servers}")
            return default_servers
        
        # Last resort: create a single default server
        fallback_server = f"{env_str.lower()}-{project_str.lower()}-fallback.example.com"
        logger.error(f"All attempts failed. Using fallback server: {fallback_server}")
        
        # Update global config with fallback
        if env_str not in SERVER_CONFIG:
            SERVER_CONFIG[env_str] = {}
        SERVER_CONFIG[env_str][project_str] = [fallback_server]
        
        return [fallback_server]

def get_available_servers(env: Environment, project: Project) -> List[str]:
    """Get list of available servers for given environment and project"""
    # Load servers for this specific project/environment
    all_servers = load_server_config_for_project(env, project)
    
    env_str = env.value
    project_str = project.value
    
    failed_servers = FAILED_SERVERS.get(env_str, {}).get(project_str, [])
    
    # Filter out failed servers
    available_servers = [s for s in all_servers if s not in failed_servers]
    
    logger.debug(f"Available servers for {env_str}/{project_str}: {available_servers}")
    logger.debug(f"Failed servers for {env_str}/{project_str}: {failed_servers}")
    logger.debug(f"All servers for {env_str}/{project_str}: {all_servers}")
    
    return available_servers

def mark_server_failed(env: Environment, project: Project, server: str):
    """Mark a server as failed for a specific environment and project"""
    env_str = env.value
    project_str = project.value
    
    # Initialize if needed
    if env_str not in FAILED_SERVERS:
        FAILED_SERVERS[env_str] = defaultdict(list)
    
    if server not in FAILED_SERVERS[env_str][project_str]:
        FAILED_SERVERS[env_str][project_str].append(server)
        logger.warning(f"Marked server {server} as failed for {env_str}/{project_str}")
    
    # Optional: write failed servers to a file for persistence
    try:
        failed_servers_file = "/var/log/failed_servers.json"
        with open(failed_servers_file, 'w') as f:
            json.dump(dict(FAILED_SERVERS), f, indent=2)
    except Exception as e:
        logger.error(f"Failed to write failed servers to file: {e}")

def mark_server_working(env: Environment, project: Project, server: str):
    """Mark a previously failed server as working again"""
    env_str = env.value
    project_str = project.value
    
    if env_str in FAILED_SERVERS and project_str in FAILED_SERVERS[env_str]:
        if server in FAILED_SERVERS[env_str][project_str]:
            FAILED_SERVERS[env_str][project_str].remove(server)
            logger.info(f"Removed server {server} from failed list for {env_str}/{project_str}")
            
            # Update persistent storage
            try:
                failed_servers_file = "/var/log/failed_servers.json"
                with open(failed_servers_file, 'w') as f:
                    json.dump(dict(FAILED_SERVERS), f, indent=2)
            except Exception as e:
                logger.error(f"Failed to update failed servers file: {e}")

def get_base_url_with_failover(env: Environment, project: Project, operation: str = "general") -> str:
    """
    Get base URL with failover support.
    Tries servers in sequence, marking failed ones.
    
    Args:
        env: Environment
        project: Project
        operation: Type of operation for logging purposes
    
    Returns:
        Base URL of a working server
    
    Raises:
        HTTPException if no servers are available
    """
    available_servers = get_available_servers(env, project)
    
    if not available_servers:
        # Check if we have any servers configured at all
        all_servers = load_server_config_for_project(env, project)
        
        if not all_servers:
            logger.error(f"No servers configured for environment '{env}' and project '{project}'")
            raise HTTPException(
                status_code=400,
                detail=f"No servers configured for environment '{env}' and project '{project}'"
            )
        else:
            logger.error(f"No available servers for {env.value}/{project.value}. All servers marked as failed: {FAILED_SERVERS.get(env.value, {}).get(project.value, [])}")
            raise HTTPException(
                status_code=503,
                detail=f"No available servers for {env.value}/{project.value}. All configured servers are currently unavailable."
            )
    
    # Try servers in order
    for server in available_servers:
        base_url = server
        if not base_url.startswith(('http://', 'https://')):
            base_url = f"https://{base_url}"
        
        logger.info(f"Trying server {server} for {env.value}/{project.value} ({operation})")
        
        # Simple health check
        try:
            # Try a quick TCP connect (simplified for now)
            # In production, you might want to implement actual health checks
            logger.info(f"Selected server {server} for {env.value}/{project.value}")
            return server  # Return without https:// prefix for consistency
            
        except Exception as e:
            logger.warning(f"Server {server} appears unhealthy: {e}")
            mark_server_failed(env, project, server)
            continue
    
    # If we get here, all servers failed
    logger.error(f"All servers failed for {env.value}/{project.value}")
    raise HTTPException(
        status_code=503,
        detail=f"All servers for {env.value}/{project.value} are currently unavailable"
    )

def format_base_url(base_url: str) -> str:
    """Format base URL to ensure it has https:// prefix"""
    if not base_url.startswith(('http://', 'https://')):
        return f"https://{base_url}"
    return base_url

# Token management functions (same as before, but kept for completeness)
def load_tokens_data(force_reload: bool = False) -> Dict[str, Any]:
    """Load tokens data from JSON file into memory"""
    global TOKENS_DATA, TOKENS_LAST_MODIFIED
    
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        token_file_path = os.path.join(script_dir, TOKENS_FILE)
        
        # Check if file exists
        if not os.path.exists(token_file_path):
            logger.error(f"Token file '{TOKENS_FILE}' not found at {token_file_path}")
            raise FileNotFoundError(f"Token file '{TOKENS_FILE}' not found")
        
        # Check if file has been modified
        current_mtime = os.path.getmtime(token_file_path)
        
        # Load data if not already loaded, force reload, or file has been modified
        if TOKENS_DATA is None or force_reload or (TOKENS_LAST_MODIFIED and current_mtime > TOKENS_LAST_MODIFIED):
            with open(token_file_path, 'r') as file:
                TOKENS_DATA = json.load(file)
            TOKENS_LAST_MODIFIED = current_mtime
            logger.info(f"Tokens data reloaded at {datetime.now()}")
            
        return TOKENS_DATA
            
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing JSON file '{TOKENS_FILE}': {e}")
        raise HTTPException(status_code=400, detail=f"Error parsing JSON file '{TOKENS_FILE}': {e}")
    except Exception as e:
        logger.error(f"Error loading token file: {e}")
        raise HTTPException(status_code=500, detail=f"Error loading token file: {e}")

def get_token_from_memory(project_name: str, env: Environment, username: str) -> str:
    """Get token from in-memory data based on project, environment, and username"""
    tokens_data = load_tokens_data()
    
    # Navigate through the nested structure: project_name -> env -> username
    project_data = tokens_data.get(project_name)
    if not project_data:
        logger.warning(f"Project '{project_name}' not found in token file")
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found in token file")
    
    env_data = project_data.get(env.value)
    if not env_data:
        logger.warning(f"Environment '{env}' not found for project '{project_name}'")
        raise HTTPException(status_code=404, detail=f"Environment '{env}' not found for project '{project_name}'")
    
    token = env_data.get(username)
    if not token:
        logger.warning(f"Token not found for user '{username}' in project '{project_name}', environment '{env}'")
        raise HTTPException(status_code=404, detail=f"Token not found for user '{username}' in project '{project_name}', environment '{env}'")
    
    logger.debug(f"Token found for user '{username}' in {project_name}/{env.value}")
    return token

def get_available_users_from_memory(project: Optional[Project] = None, env: Optional[Environment] = None) -> List[str]:
    """Get list of available users from in-memory tokens data with optional filtering"""
    try:
        tokens_data = load_tokens_data()
        
        users = set()
        
        # Filter by project if specified
        projects_to_check = [project.value] if project else tokens_data.keys()
        
        for project_name in projects_to_check:
            project_data = tokens_data.get(project_name, {})
            
            # Filter by environment if specified
            envs_to_check = [env.value] if env else project_data.keys()
            
            for env_name in envs_to_check:
                env_data = project_data.get(env_name, {})
                users.update(env_data.keys())
        
        logger.debug(f"Found {len(users)} available users for project={project}, env={env}")
        return sorted(list(users))
        
    except Exception as e:
        logger.error(f"Error getting available users: {e}")
        return []

def generate_user_token(username: str, env: Environment, project: Project) -> str:
    """Generate API token for user using the pbrun command via SSH"""
    try:
        # Get available servers for this specific project/environment
        available_servers = get_available_servers(env, project)
        
        if not available_servers:
            logger.error(f"No available servers for environment '{env}' and project '{project}'")
            raise Exception(f"No available servers for environment '{env}' and project '{project}'")
        
        # Try each server in sequence
        for fqdn in available_servers:
            try:
                logger.info(f"Generating token for user '{username}' on {fqdn} ({env.value}/{project.value})")
                
                # SSH credentials (should be stored in environment variables)
                ssh_pass = os.getenv("SSH_PASSWORD", "Password")
                ssh_username = os.getenv("SSH_USERNAME", "username")
                
                # Build the remote command
                remote_cmd = f"pbrun test 'root=rstudio-server generate-api-token' user '{username}-token' {username}"
                
                # Build SSH command using list format
                ssh_command = [
                    "sshpass", "-p", ssh_pass,
                    "ssh", "-o", "StrictHostKeyChecking=no", 
                    f"{ssh_username}@{fqdn}",
                    remote_cmd
                ]
                
                logger.info(f"Executing SSH command to {fqdn} for user: {username}")
                logger.debug(f"SSH command: {' '.join(ssh_command)}")

                # Run the SSH command with timeout
                result = subprocess.run(ssh_command, check=True, text=True, capture_output=True, timeout=30)
                
                logger.info(f"Token generation command executed successfully for user: {username} on server: {fqdn}")
                
                # Process the output
                output = result.stdout
                token = None
                
                for line in output.splitlines():
                    if '|' in line:
                        parts = line.split('|')
                        if len(parts) >= 2:
                            token = parts[1].strip()
                            if token:
                                logger.info(f"Token successfully extracted for user {username}")
                                break
                
                if not token:
                    # If pipe format not found, try to find any non-empty line
                    for line in output.splitlines():
                        stripped_line = line.strip()
                        if stripped_line and not stripped_line.startswith('#'):
                            token = stripped_line
                            logger.info(f"Using non-pipe formatted token for user {username}")
                            break
                
                if not token:
                    logger.error(f"No token found in command output for user {username}")
                    continue  # Try next server
                
                # Mark this server as working
                mark_server_working(env, project, fqdn)
                return token
                
            except subprocess.TimeoutExpired:
                logger.error(f"Token generation timed out on server {fqdn}")
                mark_server_failed(env, project, fqdn)
                continue
            except subprocess.CalledProcessError as e:
                logger.error(f"Token generation command failed on server {fqdn} for user {username}")
                logger.error(f"Return code: {e.returncode}")
                logger.error(f"Error output: {e.stderr}")
                mark_server_failed(env, project, fqdn)
                continue
            except Exception as e:
                logger.error(f"Error generating token on server {fqdn} for user {username}: {str(e)}")
                mark_server_failed(env, project, fqdn)
                continue
        
        # If we get here, all servers failed
        raise Exception(f"All servers failed for token generation. Failed servers: {FAILED_SERVERS.get(env.value, {}).get(project.value, [])}")
        
    except Exception as e:
        logger.error(f"Error generating token for user {username}: {str(e)}")
        raise Exception(f"Error generating token: {str(e)}")

def add_token_to_file(project: Project, env: Environment, username: str, token: str) -> None:
    """Add or update user token in the tokens.json file"""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        token_file_path = os.path.join(script_dir, TOKENS_FILE)
        
        logger.info(f"Adding token for user '{username}' in {project.value}/{env.value}")
        
        # Read existing data
        if os.path.exists(token_file_path):
            with open(token_file_path, 'r') as file:
                tokens_data = json.load(file)
            logger.debug(f"Successfully read existing token file")
        else:
            logger.info(f"Token file does not exist, creating new file")
            tokens_data = {}
        
        # Ensure the nested structure exists
        if project.value not in tokens_data:
            logger.info(f"Creating new project section: {project.value}")
            tokens_data[project.value] = {}
        
        if env.value not in tokens_data[project.value]:
            logger.info(f"Creating new environment section: {env.value}")
            tokens_data[project.value][env.value] = {}
        
        # Check if user already exists
        user_exists = username in tokens_data[project.value][env.value]
        if user_exists:
            logger.warning(f"Overwriting existing token for user '{username}' in {project.value}/{env.value}")
        else:
            logger.info(f"Adding new user '{username}' to {project.value}/{env.value}")
        
        # Add or update the token
        tokens_data[project.value][env.value][username] = token
        
        # Write back to file
        with open(token_file_path, 'w') as file:
            json.dump(tokens_data, file, indent=2)
        
        # Reload the in-memory data
        global TOKENS_DATA, TOKENS_LAST_MODIFIED
        TOKENS_DATA = tokens_data
        TOKENS_LAST_MODIFIED = os.path.getmtime(token_file_path)
        
        logger.info(f"Successfully added token for user '{username}' in {project.value}/{env.value}")
        
    except Exception as e:
        logger.error(f"Error updating token file for user {username}: {str(e)}")
        raise Exception(f"Error updating token file: {str(e)}")

def get_or_create_user_token(project: Project, env: Environment, username: str) -> tuple[str, str]:
    """
    Get user token from memory/file, or create if it doesn't exist.
    Returns (username, token)
    """
    try:
        # First try to get existing token
        token = get_token_from_memory(project.value, env, username)
        logger.info(f"Found existing token for user '{username}' in {project.value}/{env.value}")
        return username, token
        
    except HTTPException as e:
        # If token not found (404), check if user has access and create token
        if e.status_code == 404:
            # Check if user has access to this project/environment
            has_access = check_user_access_for_launch(username, project, env)
            
            if has_access:
                try:
                    logger.info(f"Token not found for user '{username}', generating new token...")
                    # Generate new token with environment and project parameters
                    new_token = generate_user_token(username, env, project)
                    
                    # Add token to file
                    add_token_to_file(project, env, username, new_token)
                    
                    logger.info(f"Successfully generated and stored token for user '{username}'")
                    return username, new_token
                    
                except Exception as token_error:
                    logger.error(f"Failed to generate token for user '{username}': {str(token_error)}")
                    raise HTTPException(
                        status_code=500,
                        detail=f"User has access but failed to generate token: {str(token_error)}"
                    )
            else:
                # User doesn't have access, re-raise the original 404
                logger.warning(f"User '{username}' does not have access to {project.value}/{env.value}")
                raise e
        else:
            # Re-raise other HTTP exceptions
            raise e
    except Exception as e:
        logger.error(f"Error getting user token for {username}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error getting user token: {str(e)}"
        )

# Group configuration functions
def load_group_config(force_reload: bool = False) -> Dict[str, Any]:
    """Load group configuration from JSON file"""
    global GROUP_CONFIG, GROUP_CONFIG_LAST_MODIFIED
    
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_file_path = os.path.join(script_dir, GROUP_CONFIG_FILE)
        
        # Check if file exists
        if not os.path.exists(config_file_path):
            logger.error(f"Group config file '{GROUP_CONFIG_FILE}' not found at {config_file_path}")
            raise HTTPException(status_code=404, detail=f"Group config file '{GROUP_CONFIG_FILE}' not found")
        
        # Check if file has been modified
        current_mtime = os.path.getmtime(config_file_path)
        
        # Load data if not already loaded, force reload, or file has been modified
        if GROUP_CONFIG is None or force_reload or (GROUP_CONFIG_LAST_MODIFIED and current_mtime > GROUP_CONFIG_LAST_MODIFIED):
            with open(config_file_path, 'r') as file:
                GROUP_CONFIG = json.load(file)
            GROUP_CONFIG_LAST_MODIFIED = current_mtime
            logger.info(f"Group configuration reloaded at {datetime.now()}")
            
        return GROUP_CONFIG
            
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing group config file: {e}")
        raise HTTPException(status_code=400, detail=f"Error parsing group config file: {e}")
    except Exception as e:
        logger.error(f"Error loading group config: {e}")
        raise HTTPException(status_code=500, detail=f"Error loading group config: {e}")

def get_user_groups(username: str) -> List[str]:
    """Get user groups using the 'groups' command"""
    try:
        logger.info(f"Getting groups for user: {username}")
        # Execute the groups command
        result = subprocess.run(
            ['groups', username],
            capture_output=True,
            text=True,
            check=True
        )
        
        # Parse the output - groups command returns: username : group1 group2 group3
        output = result.stdout.strip()
        logger.debug(f"Groups command output for {username}: {output}")
        
        if ':' in output:
            groups_part = output.split(':', 1)[1].strip()
            groups = groups_part.split()
            logger.info(f"User '{username}' belongs to groups: {groups}")
            return groups
        else:
            logger.warning(f"No groups found for user {username}")
            return []
            
    except subprocess.CalledProcessError as e:
        logger.warning(f"Groups command failed for user {username}, may not exist: {e.stderr}")
        # User might not exist or no groups
        return []
    except FileNotFoundError:
        logger.error("'groups' command not available on this system")
        raise HTTPException(status_code=500, detail="'groups' command not available on this system")
    except Exception as e:
        logger.error(f"Error getting user groups for {username}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting user groups: {e}")

def check_project_access(user_groups: List[str], project_config: Dict[str, Any]) -> List[str]:
    """Check which environments user has access to for a given project"""
    accessible_environments = []
    
    for env, env_config in project_config.items():
        required_groups = env_config.get("groups", [])
        
        # Handle both string and list formats for groups
        if isinstance(required_groups, str):
            required_groups = [required_groups]
        
        # Check if user has any of the required groups
        if any(group in user_groups for group in required_groups):
            accessible_environments.append(env)
    
    logger.debug(f"User has access to environments: {accessible_environments}")
    return accessible_environments

def check_user_access_for_launch(username: str, project: Project, env: Environment) -> bool:
    """Check if user has access to launch session in the specified project and environment"""
    try:
        logger.info(f"Checking access for user '{username}' in {project.value}/{env.value}")
        
        # Load group configuration
        group_config = load_group_config()
        
        # Get user's groups
        user_groups = get_user_groups(username)
        
        # Check access for the specific project and environment
        project_configs = group_config.get("project_name", {})
        project_config = project_configs.get(project.value, {})
        
        env_config = project_config.get(env.value, {})
        required_groups = env_config.get("groups", [])
        
        logger.debug(f"Required groups for {project.value}/{env.value}: {required_groups}")
        logger.debug(f"User '{username}' groups: {user_groups}")
        
        # Handle both string and list formats for groups
        if isinstance(required_groups, str):
            required_groups = [required_groups]
        
        # Check if user has any of the required groups
        has_access = any(group in user_groups for group in required_groups)
        
        logger.info(f"Access {'GRANTED' if has_access else 'DENIED'} for user '{username}' in {project.value}/{env.value}")
        
        return has_access
        
    except Exception as e:
        logger.error(f"Error checking user access for {username}: {e}")
        return False

async def validate_node_selection(selected_server: str, node_selection_flag: str, username: str) -> str:
    """
    Validate and determine the actual node using local CLI script for Project1.

    Args:
        selected_server (str): The server FQDN
        node_selection_flag (str): Either "P" or "V" (user intent)
        username (str): The username launching the session

    Returns:
        str: Final validated node name
    """
    try:
        logger.info(f"Running posit_select_node.py with flag: {node_selection_flag} for user: {username} on server: {selected_server}")
        
        script_path = os.path.join(os.path.dirname(__file__), "posit_select_node.py")
        if not os.path.exists(script_path):
            raise FileNotFoundError(f"Node selection script not found at {script_path}")

        # Validate node selection flag
        if node_selection_flag.upper() not in ["P", "V"]:
            raise ValueError(f"Invalid node selection flag: {node_selection_flag}. Must be 'P' or 'V'")

        # Run the external CLI script and capture output
        result = subprocess.run(
            ["python3", script_path, node_selection_flag],
            check=True,
            capture_output=True,
            text=True
        )
        
        selected_node = result.stdout.strip()
        if not selected_node:
            raise ValueError("Empty node returned from node selection script")

        logger.info(f"Node selected by script: {selected_node}")
        return selected_node

    except subprocess.CalledProcessError as e:
        logger.error(f"Node selection script failed: {e.stderr}")
        raise HTTPException(
            status_code=500,
            detail=f"Node selection script failed: {e.stderr}"
        )
    except Exception as e:
        logger.error(f"Error in node selection logic: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error during node selection: {e}"
        )

async def make_api_request_with_retry(base_url: str, api_endpoint: str, payload: dict, token: str, 
                                     env: Environment, project: Project, operation: str = "api") -> Dict[str, Any]:
    """
    Make API request to external service with server failover.
    """
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {token}'
    }

    # Get list of servers to try for this specific project/environment
    available_servers = get_available_servers(env, project)
    all_servers_tried = []
    
    for server in available_servers:
        try:
            formatted_base_url = format_base_url(server)
            full_url = formatted_base_url + api_endpoint
            
            logger.info(f"Making {operation} request to: {full_url}")
            
            response = requests.request("POST", full_url, 
                                      headers=headers, data=json.dumps(payload), 
                                      verify=False, timeout=30)
            
            logger.info(f"API response status from {server}: {response.status_code}")
            
            # If we get a connection/timeout error, mark server as failed and continue
            if response.status_code >= 500 or response.status_code == 408:
                logger.warning(f"Server {server} returned error {response.status_code}")
                mark_server_failed(env, project, server)
                all_servers_tried.append(server)
                continue
            
            response.raise_for_status()
            
            # If successful, mark server as working and return response
            mark_server_working(env, project, server)
            return json.loads(response.text)
            
        except requests.exceptions.Timeout:
            logger.error(f"Request to {server} timed out")
            mark_server_failed(env, project, server)
            all_servers_tried.append(server)
            continue
        except requests.exceptions.ConnectionError:
            logger.error(f"Connection error to {server}")
            mark_server_failed(env, project, server)
            all_servers_tried.append(server)
            continue
        except requests.exceptions.RequestException as e:
            logger.error(f"Request to {server} failed: {e}")
            mark_server_failed(env, project, server)
            all_servers_tried.append(server)
            continue
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse response JSON from {server}: {e}")
            # Don't mark as failed for JSON parse error - server is responding
            raise HTTPException(status_code=500, detail=f"Failed to parse response JSON: {e}")
    
    # If we get here, all servers failed
    logger.error(f"All servers failed for {operation} on {env.value}/{project.value}. Tried: {all_servers_tried}")
    raise HTTPException(
        status_code=503,
        detail=f"All servers for {env.value}/{project.value} are currently unavailable for {operation}"
    )

async def get_sessions_api(env: Environment, project: Project, token: str) -> Dict[str, Any]:
    """Get sessions using the provided API with failover"""
    logger.info(f"Getting sessions from {env.value}/{project.value}")
    payload = {
        "method": "get_session"
    }
    
    return await make_api_request_with_retry(
        base_url="",
        api_endpoint=GET_SESSION_API,
        payload=payload,
        token=token,
        env=env,
        project=project,
        operation="get_sessions"
    )

async def stop_session_api(env: Environment, project: Project, token: str, 
                          session_ids: List[str], force_quit: bool = False, 
                          suspend_session: bool = False) -> Dict[str, Any]:
    """Stop/kill sessions using the provided API with failover"""
    session_ids_str = ",".join(session_ids)
    
    logger.info(f"Stopping sessions: {session_ids_str}, force_quit: {force_quit}, suspend: {suspend_session}")
    
    payload = {
        "method": "stop_session",
        "kwparams": {
            "session_ids": session_ids_str,
            "force_quit": force_quit,
            "suspend_session": suspend_session
        }
    }
    
    return await make_api_request_with_retry(
        base_url="",
        api_endpoint=STOP_SESSION_API,
        payload=payload,
        token=token,
        env=env,
        project=project,
        operation="stop_sessions"
    )

def extract_session_info(server: str, session_data: Dict[str, Any]) -> SessionInfo:
    """Extract session information from the API response"""
    display_name = session_data.get("display_name", "")
    
    if not display_name:
        display_name = session_data.get("name", session_data.get("session_name", ""))
    
    formatted_base_url = format_base_url(server)
    session_info = SessionInfo(
        session_id=session_data.get("id", ""),
        url=formatted_base_url + session_data.get("url", ""),
        session_name=display_name,
        display_name=display_name
    )
    
    logger.debug(f"Extracted session info: {session_info.session_id} - {session_info.display_name}")
    return session_info

def get_next_available_session_number(existing_sessions: List[SessionInfo]) -> int:
    """
    Find the next available session number by checking existing session names.
    Pattern: JupyterLab Session {number}
    """
    pattern = re.compile(r"^JupyterLab Session (\d+)$")
    used_numbers = set()
    
    for session in existing_sessions:
        match = pattern.match(session.display_name)
        if match:
            try:
                used_numbers.add(int(match.group(1)))
            except (ValueError, TypeError):
                continue
    
    next_number = 1
    while next_number in used_numbers:
        next_number += 1
    
    logger.info(f"Next available session number: {next_number} (used numbers: {sorted(used_numbers)})")
    return next_number

async def launch_session_api(env: Environment, project: Project, token: str, 
                           custom_session_name: Optional[str], workbench: str, 
                           cluster: str, placement_constraints: List[str] = None) -> tuple[dict, str, str]:
    """Launch a session using the provided API with failover and unique name"""
    try:
        # First get existing sessions to determine next session number
        sessions_response = await get_sessions_api(env, project, token)
        existing_sessions = []
        
        if sessions_response and "result" in sessions_response and "sessions" in sessions_response["result"]:
            # Get a server for session info - use the first available one
            available_servers = get_available_servers(env, project)
            if available_servers:
                server_for_info = available_servers[0]
                for session_data in sessions_response["result"]["sessions"]:
                    session_info = extract_session_info(server_for_info, session_data)
                    existing_sessions.append(session_info)
        
        # Use custom session name if provided, otherwise generate one
        if custom_session_name:
            unique_session_name = custom_session_name
            logger.info(f"Using custom session name: {unique_session_name}")
        else:
            next_number = get_next_available_session_number(existing_sessions)
            unique_session_name = f"JupyterLab Session {next_number}"
            logger.info(f"Generated session name: {unique_session_name}")
        
    except Exception as e:
        logger.warning(f"Error getting existing sessions, using simple naming: {e}")
        unique_session_name = f"JupyterLab Session 1"
    
    # Prepare launch parameters
    launch_parameters = {
        "name": unique_session_name,
        "cluster": cluster,
        "placement_constraints": placement_constraints or [],
        "resource_limits": [],
        "queues": []
    }
    
    payload = {
        "method": "launch_session",
        "kwparams": {
            "workbench": workbench,
            "name": unique_session_name,
            "launch_parameters": launch_parameters
        }
    }
    
    logger.info(f"Launching session with name: {unique_session_name}, workbench: {workbench}, cluster: {cluster}, placement_constraints: {placement_constraints}")
    
    # Use the make_api_request_with_retry function
    response_data = await make_api_request_with_retry(
        base_url="",
        api_endpoint=LAUNCH_API,
        payload=payload,
        token=token,
        env=env,
        project=project,
        operation="launch_session"
    )
    
    # Get the first available server for URL construction
    available_servers = get_available_servers(env, project)
    selected_server = available_servers[0] if available_servers else "unknown"
    
    return response_data, unique_session_name, selected_server

# Load data into memory on startup
@app.on_event("startup")
async def startup_event():
    """Load configurations when the application starts"""
    try:
        load_tokens_data()
        logger.info("Tokens data loaded successfully into memory")
    except Exception as e:
        logger.error(f"Could not load tokens data on startup: {e}")
    
    try:
        load_group_config()
        logger.info("Group configuration loaded successfully into memory")
    except Exception as e:
        logger.error(f"Could not load group configuration: {e}")
    
    try:
        # Load server paths from environment variables
        load_server_paths_from_env()
        logger.info("Server paths configuration loaded from environment variables")
        
        # Pre-load server configurations for all environments/projects
        for env in Environment:
            for project in Project:
                try:
                    servers = load_server_config_for_project(env, project)
                    logger.info(f"Pre-loaded {len(servers)} servers for {env.value}/{project.value}")
                except Exception as e:
                    logger.error(f"Could not pre-load servers for {env.value}/{project.value}: {e}")
    except Exception as e:
        logger.error(f"Could not load server configuration: {e}")

# Endpoints (same as before, but updated to use new server path system)
@app.get("/")
async def root():
    """Root endpoint with API information"""
    logger.info("Root endpoint accessed")
    return {
        "message": "Session Management API",
        "version": "2.0.0",
        "endpoints": {
            "GET /tokens/{project}/{env}/{username}": "Get token for a specific user in project and environment",
            "POST /launch-session": "Launch a new session (requires X-User-ID header, env, project in body; node_selection optional for Project 1)",
            "GET /sessions": "Get all sessions for a user (requires X-User-ID header, env and project in query params)",
            "POST /stop-session": "Stop/kill one or more sessions (requires X-User-ID header, env and project in body)",
            "GET /available-users": "Get list of available users",
            "GET /available-users/{project}/{env}": "Get list of available users for specific project and environment",
            "GET /env-projects": "Get available environment and project combinations",
            "GET /user-project-access/{username}": "Get project access for a user based on group membership",
            "GET /user-project-access": "Get project access for current user (from X-User-ID header)",
            "GET /server-config": "Get current server configuration and failed servers",
            "GET /server-paths": "Get configured server list file paths",
            "POST /admin/reload-tokens": "Reload tokens.json file",
            "POST /admin/reload-group-config": "Reload group_config.json file",
            "POST /admin/reload-servers": "Reload server-list.txt files",
            "POST /admin/reset-failed-servers": "Reset failed servers list",
            "GET /select-node/{node_flag}": "Get server name for specific node (P or V)"
        }
    }

@app.get("/env-projects")
async def get_env_projects():
    """Get available environment and project combinations"""
    logger.info("Environment-projects mapping requested")
    # Return the server paths configuration
    return {
        "environments": [env.value for env in Environment],
        "projects": [project.value for project in Project],
        "server_paths": SERVER_PATHS,
        "server_config": SERVER_CONFIG,
        "failed_servers": dict(FAILED_SERVERS)
    }

@app.get("/server-config", response_model=ServerConfigResponse)
async def get_server_config():
    """Get current server configuration and failed servers"""
    logger.info("Server configuration requested")
    try:
        # Convert SERVER_CONFIG to regular dict
        server_config_dict = {}
        if SERVER_CONFIG:
            for env, projects in SERVER_CONFIG.items():
                server_config_dict[env] = {}
                for project, servers in projects.items():
                    server_config_dict[env][project] = servers
        
        return ServerConfigResponse(
            success=True,
            message="Server configuration retrieved successfully",
            servers=server_config_dict,
            failed_servers=dict(FAILED_SERVERS)
        )
    except Exception as e:
        logger.error(f"Error getting server configuration: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting server configuration: {e}")

@app.get("/server-paths")
async def get_server_paths():
    """Get configured server list file paths"""
    logger.info("Server paths configuration requested")
    return {
        "success": True,
        "message": "Server paths configuration retrieved successfully",
        "server_paths": SERVER_PATHS,
        "timestamp": datetime.now().isoformat()
    }

# ... (other endpoints remain the same as in your original code - they'll automatically use the new server path system)

# New endpoint to get server list file content
@app.get("/server-list-content/{env}/{project}")
async def get_server_list_content(env: Environment, project: Project):
    """Get the content of the server-list.txt file for a specific environment and project"""
    logger.info(f"Server list content requested for {env.value}/{project.value}")
    
    try:
        # Get the file path
        server_file_path = get_server_list_path(env, project)
        
        # Check if file exists
        if not os.path.exists(server_file_path):
            raise HTTPException(
                status_code=404,
                detail=f"Server list file not found at {server_file_path}"
            )
        
        # Read file content
        with open(server_file_path, 'r') as file:
            content = file.read()
        
        # Get the list of servers
        servers = load_server_config_for_project(env, project)
        
        return {
            "success": True,
            "env": env.value,
            "project": project.value,
            "file_path": server_file_path,
            "content": content,
            "parsed_servers": servers,
            "timestamp": datetime.now().isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reading server list file for {env.value}/{project.value}: {e}")
        raise HTTPException(status_code=500, detail=f"Error reading server list file: {e}")

# Admin endpoints to reload server configurations
@app.post("/admin/reload-servers", response_model=ReloadResponse)
async def reload_servers():
    """Reload all server-list.txt files"""
    logger.info("Admin reload servers request received")
    try:
        # Reload server paths from environment variables
        load_server_paths_from_env()
        
        # Reload server configurations for all environments/projects
        for env in Environment:
            for project in Project:
                try:
                    servers = load_server_config_for_project(env, project, force_reload=True)
                    logger.info(f"Reloaded {len(servers)} servers for {env.value}/{project.value}")
                except Exception as e:
                    logger.error(f"Could not reload servers for {env.value}/{project.value}: {e}")
        
        logger.info("Server configurations reloaded successfully via admin endpoint")
        return ReloadResponse(
            success=True,
            message="Server configurations reloaded successfully",
            timestamp=datetime.now().isoformat()
        )
    except Exception as e:
        logger.error(f"Failed to reload servers via admin endpoint: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to reload servers: {e}")

@app.post("/admin/reload-servers/{env}/{project}", response_model=ReloadResponse)
async def reload_servers_for_env_project(env: Environment, project: Project):
    """Reload server-list.txt file for specific environment and project"""
    logger.info(f"Admin reload servers request received for {env.value}/{project.value}")
    try:
        servers = load_server_config_for_project(env, project, force_reload=True)
        
        logger.info(f"Server configuration reloaded for {env.value}/{project.value} via admin endpoint")
        return ReloadResponse(
            success=True,
            message=f"Server configuration reloaded for {env.value}/{project.value}",
            timestamp=datetime.now().isoformat()
        )
    except Exception as e:
        logger.error(f"Failed to reload servers for {env.value}/{project.value}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to reload servers: {e}")

if __name__ == "__main__":
    logger.info("Starting Session Management API server (Version 2.0.0)")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_config=None)
