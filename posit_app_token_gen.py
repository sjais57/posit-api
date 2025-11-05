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

app = FastAPI(title="Session Management API", version="1.0.0")

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
    node_selection: Optional[str] = Field(None, description="Node selection for the session (optional for both projects)")

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

# API endpoints (relative paths)
LAUNCH_API = "/api/launch_session"
GET_SESSION_API = "/api/get_session"
STOP_SESSION_API = "/api/stop_session"

# Environment to base URL mapping
ENV_PROJECT_MAP = {
    Environment.DEV: {
        Project.PROJECT1: "dev-project1.example.com",
        Project.PROJECT2: "dev-project2.example.com"
    },
    Environment.UAT: {
        Project.PROJECT1: "uat-project1.example.com",
        Project.PROJECT2: "uat-project2.example.com"
    },
    Environment.PROD: {
        Project.PROJECT1: "prod-project1.example.com",
        Project.PROJECT2: "prod-project2.example.com"
    }
}

# Global variables to store data in memory
TOKENS_DATA = None
GROUP_CONFIG = None
TOKENS_FILE = "tokens.json"
GROUP_CONFIG_FILE = "group_config.json"
TOKENS_LAST_MODIFIED = None
GROUP_CONFIG_LAST_MODIFIED = None

def get_base_url(env: Environment, project: Project) -> str:
    """Get base URL based on environment and project"""
    base_url = ENV_PROJECT_MAP.get(env, {}).get(project)
    if not base_url:
        raise HTTPException(
            status_code=400,
            detail=f"No base URL configured for environment '{env}' and project '{project}'"
        )
    return base_url

def format_base_url(base_url: str) -> str:
    """Format base URL to ensure it has https:// prefix"""
    if not base_url.startswith(('http://', 'https://')):
        return f"https://{base_url}"
    return base_url

# Token management functions
def load_tokens_data(force_reload: bool = False) -> Dict[str, Any]:
    """Load tokens data from JSON file into memory"""
    global TOKENS_DATA, TOKENS_LAST_MODIFIED
    
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        token_file_path = os.path.join(script_dir, TOKENS_FILE)
        
        # Check if file exists
        if not os.path.exists(token_file_path):
            raise FileNotFoundError(f"Token file '{TOKENS_FILE}' not found")
        
        # Check if file has been modified
        current_mtime = os.path.getmtime(token_file_path)
        
        # Load data if not already loaded, force reload, or file has been modified
        if TOKENS_DATA is None or force_reload or (TOKENS_LAST_MODIFIED and current_mtime > TOKENS_LAST_MODIFIED):
            with open(token_file_path, 'r') as file:
                TOKENS_DATA = json.load(file)
            TOKENS_LAST_MODIFIED = current_mtime
            print(f"Tokens data reloaded at {datetime.now()}")
            
        return TOKENS_DATA
            
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Error parsing JSON file '{TOKENS_FILE}': {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading token file: {e}")

def get_token_from_memory(project_name: str, env: Environment, username: str) -> str:
    """Get token from in-memory data based on project, environment, and username"""
    tokens_data = load_tokens_data()
    
    # Navigate through the nested structure: project_name -> env -> username
    project_data = tokens_data.get(project_name)
    if not project_data:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found in token file")
    
    env_data = project_data.get(env.value)
    if not env_data:
        raise HTTPException(status_code=404, detail=f"Environment '{env}' not found for project '{project_name}'")
    
    token = env_data.get(username)
    if not token:
        raise HTTPException(status_code=404, detail=f"Token not found for user '{username}' in project '{project_name}', environment '{env}'")
    
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
        
        return sorted(list(users))
        
    except Exception:
        return []

def generate_user_token(username: str) -> str:
    """Generate API token for user using the pbrun command"""
    try:
        # Build the command
        cmd = [
            'pbrun', 'test', 
            'root=rstudio-server generate-api-token', 
            'user', 
            f'username-token', 
            username
        ]
        
        # Execute the command
        result = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True, 
            check=True
        )
        
        # Parse the output to extract the token using awk-like logic
        output_lines = result.stdout.split('\n')
        for line in output_lines:
            if '|' in line:
                # Extract the token part after the pipe
                parts = line.split('|')
                if len(parts) >= 2:
                    token = parts[1].strip()
                    if token:  # Ensure it's not empty
                        return token
        
        # If no token found in pipe format, try to find any non-empty line
        for line in output_lines:
            stripped_line = line.strip()
            if stripped_line and not stripped_line.startswith('#'):
                return stripped_line
                
        raise Exception("No token found in command output")
        
    except subprocess.CalledProcessError as e:
        raise Exception(f"Token generation command failed: {e.stderr}")
    except Exception as e:
        raise Exception(f"Error generating token: {str(e)}")

def add_token_to_file(project: Project, env: Environment, username: str, token: str) -> None:
    """Add or update user token in the tokens.json file"""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        token_file_path = os.path.join(script_dir, TOKENS_FILE)
        
        # Read existing data
        if os.path.exists(token_file_path):
            with open(token_file_path, 'r') as file:
                tokens_data = json.load(file)
        else:
            tokens_data = {}
        
        # Ensure the nested structure exists
        if project.value not in tokens_data:
            tokens_data[project.value] = {}
        
        if env.value not in tokens_data[project.value]:
            tokens_data[project.value][env.value] = {}
        
        # Add or update the token
        tokens_data[project.value][env.value][username] = token
        
        # Write back to file
        with open(token_file_path, 'w') as file:
            json.dump(tokens_data, file, indent=2)
        
        # Reload the in-memory data
        global TOKENS_DATA, TOKENS_LAST_MODIFIED
        TOKENS_DATA = tokens_data
        TOKENS_LAST_MODIFIED = os.path.getmtime(token_file_path)
        
        print(f"Successfully added token for user '{username}' in {project.value}/{env.value}")
        
    except Exception as e:
        raise Exception(f"Error updating token file: {str(e)}")

def get_or_create_user_token(project: Project, env: Environment, username: str) -> tuple[str, str]:
    """
    Get user token from memory/file, or create if it doesn't exist.
    Returns (username, token)
    """
    try:
        # First try to get existing token
        token = get_token_from_memory(project.value, env, username)
        return username, token
        
    except HTTPException as e:
        # If token not found (404), check if user has access and create token
        if e.status_code == 404:
            # Check if user has access to this project/environment
            has_access = check_user_access_for_launch(username, project, env)
            
            if has_access:
                try:
                    print(f"Token not found for user '{username}', generating new token...")
                    # Generate new token
                    new_token = generate_user_token(username)
                    
                    # Add token to file
                    add_token_to_file(project, env, username, new_token)
                    
                    print(f"Successfully generated and stored token for user '{username}'")
                    return username, new_token
                    
                except Exception as token_error:
                    raise HTTPException(
                        status_code=500,
                        detail=f"User has access but failed to generate token: {str(token_error)}"
                    )
            else:
                # User doesn't have access, re-raise the original 404
                raise e
        else:
            # Re-raise other HTTP exceptions
            raise e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error getting user token: {str(e)}"
        )

def get_user_token(project: Project, env: Environment, username: str) -> tuple[str, str]:
    """Centralized function to get user ID and token (with auto-creation)"""
    return get_or_create_user_token(project, env, username)

# Group configuration functions
def load_group_config(force_reload: bool = False) -> Dict[str, Any]:
    """Load group configuration from JSON file"""
    global GROUP_CONFIG, GROUP_CONFIG_LAST_MODIFIED
    
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_file_path = os.path.join(script_dir, GROUP_CONFIG_FILE)
        
        # Check if file exists
        if not os.path.exists(config_file_path):
            raise HTTPException(status_code=404, detail=f"Group config file '{GROUP_CONFIG_FILE}' not found")
        
        # Check if file has been modified
        current_mtime = os.path.getmtime(config_file_path)
        
        # Load data if not already loaded, force reload, or file has been modified
        if GROUP_CONFIG is None or force_reload or (GROUP_CONFIG_LAST_MODIFIED and current_mtime > GROUP_CONFIG_LAST_MODIFIED):
            with open(config_file_path, 'r') as file:
                GROUP_CONFIG = json.load(file)
            GROUP_CONFIG_LAST_MODIFIED = current_mtime
            print(f"Group configuration reloaded at {datetime.now()}")
            
        return GROUP_CONFIG
            
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Error parsing group config file: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading group config: {e}")

def get_user_groups(username: str) -> List[str]:
    """Get user groups using the 'groups' command"""
    try:
        # Execute the groups command
        result = subprocess.run(
            ['groups', username],
            capture_output=True,
            text=True,
            check=True
        )
        
        # Parse the output - groups command returns: username : group1 group2 group3
        output = result.stdout.strip()
        if ':' in output:
            groups_part = output.split(':', 1)[1].strip()
            groups = groups_part.split()
            return groups
        else:
            return []
            
    except subprocess.CalledProcessError:
        # User might not exist or no groups
        return []
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="'groups' command not available on this system")
    except Exception as e:
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
    
    return accessible_environments

def check_user_access_for_launch(username: str, project: Project, env: Environment) -> bool:
    """Check if user has access to launch session in the specified project and environment"""
    try:
        # Load group configuration
        group_config = load_group_config()
        
        # Get user's groups
        user_groups = get_user_groups(username)
        
        # Check access for the specific project and environment
        project_configs = group_config.get("project_name", {})
        project_config = project_configs.get(project.value, {})
        
        env_config = project_config.get(env.value, {})
        required_groups = env_config.get("groups", [])
        
        # Handle both string and list formats for groups
        if isinstance(required_groups, str):
            required_groups = [required_groups]
        
        # Check if user has any of the required groups
        has_access = any(group in user_groups for group in required_groups)
        
        return has_access
        
    except Exception as e:
        print(f"Error checking user access: {e}")
        return False

async def make_api_request(base_url: str, api_endpoint: str, payload: dict, token: str) -> Dict[str, Any]:
    """Make API request to external service"""
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {token}'
    }

    try:
        formatted_base_url = format_base_url(base_url)
        response = requests.request("POST", formatted_base_url + api_endpoint, 
                                  headers=headers, data=json.dumps(payload), verify=False)
        response.raise_for_status()
        return json.loads(response.text)
        
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Request to external API failed: {e}")
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse response JSON: {e}")

async def validate_node_selection(base_url: str, node_selection: str, username: str) -> bool:
    """Validate node selection by making the node selection API call"""
    try:
        # Construct the node selection URL
        node_url = f"https://{base_url}:8084/cluster/{node_selection}/user/{username}"
        
        print(f"Node selection URL: {node_url}")  # Debug logging
        
        # No token required for this endpoint
        headers = {}
        
        response = requests.request("GET", node_url, headers=headers, verify=False)
        
        print(f"Node selection response status: {response.status_code}")  # Debug logging
        print(f"Node selection response text: {response.text[:200]}")  # Debug logging
        
        response.raise_for_status()
        
        # If we get here, the node selection was successful
        print(f"Node selection successful for node: {node_selection}")  # Debug logging
        return True
        
    except requests.exceptions.RequestException as e:
        print(f"Node selection request error: {e}")  # Debug logging
        raise HTTPException(
            status_code=400,
            detail=f"Node selection failed for node '{node_selection}': {str(e)}"
        )
    except Exception as e:
        print(f"Node selection unexpected error: {e}")  # Debug logging
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error during node selection: {str(e)}"
        )

async def get_sessions_api(base_url: str, token: str) -> Dict[str, Any]:
    """Get sessions using the provided API"""
    payload = {
        "method": "get_session"
    }
    
    return await make_api_request(base_url, GET_SESSION_API, payload, token)

async def stop_session_api(base_url: str, token: str, session_ids: List[str], force_quit: bool = False, suspend_session: bool = False) -> Dict[str, Any]:
    """Stop/kill sessions using the provided API"""
    # Convert session_ids list to comma-separated string for the external API
    session_ids_str = ",".join(session_ids)
    
    payload = {
        "method": "stop_session",
        "kwparams": {
            "session_ids": session_ids_str,  # Send as string
            "force_quit": force_quit,
            "suspend_session": suspend_session
        }
    }
    
    return await make_api_request(base_url, STOP_SESSION_API, payload, token)

def extract_session_info(base_url: str, session_data: Dict[str, Any]) -> SessionInfo:
    """Extract session information from the API response"""
    display_name = session_data.get("display_name", "")
    
    if not display_name:
        display_name = session_data.get("name", session_data.get("session_name", ""))
    
    formatted_base_url = format_base_url(base_url)
    return SessionInfo(
        session_id=session_data.get("id", ""),
        url=formatted_base_url + session_data.get("url", ""),
        session_name=display_name,
        display_name=display_name
    )

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
    
    return next_number

async def launch_session_api(base_url: str, token: str, custom_session_name: Optional[str], workbench: str, cluster: str) -> tuple[dict, str]:
    """Launch a session using the provided API with unique name"""
    try:
        sessions_response = await get_sessions_api(base_url, token)
        existing_sessions = []
        
        if sessions_response and "result" in sessions_response and "sessions" in sessions_response["result"]:
            for session_data in sessions_response["result"]["sessions"]:
                session_info = extract_session_info(base_url, session_data)
                existing_sessions.append(session_info)
        
        # Use custom session name if provided, otherwise generate one
        if custom_session_name:
            unique_session_name = custom_session_name
        else:
            next_number = get_next_available_session_number(existing_sessions)
            unique_session_name = f"JupyterLab Session {next_number}"
        
    except Exception as e:
        print(f"Error getting existing sessions, using simple naming: {e}")
        unique_session_name = f"JupyterLab Session 1"
    
    # Prepare launch parameters
    launch_parameters = {
        "name": unique_session_name,
        "cluster": cluster,
        "placement_constraints": [],
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
    
    response_data = await make_api_request(base_url, LAUNCH_API, payload, token)
    return response_data, unique_session_name

# Load data into memory on startup
@app.on_event("startup")
async def startup_event():
    """Load tokens and group configuration data into memory when the application starts"""
    try:
        load_tokens_data()
        print("Tokens data loaded successfully into memory")
    except Exception as e:
        print(f"Warning: Could not load tokens data on startup: {e}")
    
    try:
        load_group_config()
        print("Group configuration loaded successfully into memory")
    except Exception as e:
        print(f"Warning: Could not load group configuration: {e}")

# Endpoints
@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "message": "Session Management API",
        "version": "1.0.0",
        "endpoints": {
            "GET /tokens/{project}/{env}/{username}": "Get token for a specific user in project and environment",
            "POST /launch-session": "Launch a new session (requires X-User-ID header, env, project in body; node_selection optional)",
            "GET /sessions": "Get all sessions for a user (requires X-User-ID header, env and project in query params)",
            "POST /stop-session": "Stop/kill one or more sessions (requires X-User-ID header, env and project in body)",
            "GET /available-users": "Get list of available users",
            "GET /available-users/{project}/{env}": "Get list of available users for specific project and environment",
            "GET /env-projects": "Get available environment and project combinations",
            "GET /user-project-access/{username}": "Get project access for a user based on group membership",
            "GET /user-project-access": "Get project access for current user (from X-User-ID header)",
            "POST /admin/reload-tokens": "Reload tokens.json file",
            "POST /admin/reload-group-config": "Reload group_config.json file"
        }
    }

@app.get("/env-projects")
async def get_env_projects():
    """Get available environment and project combinations"""
    return {
        "environments": [env.value for env in Environment],
        "projects": [project.value for project in Project],
        "mappings": ENV_PROJECT_MAP
    }

@app.get("/tokens/{project}/{env}/{username}", response_model=TokenResponse)
async def get_token(project: Project, env: Environment, username: str):
    """Get token for a specific user in a specific project and environment"""
    token = get_token_from_memory(project.value, env, username)
    available_users = get_available_users_from_memory(project, env)
    
    return TokenResponse(
        username=username,
        token=token,
        available_users=available_users
    )

@app.get("/available-users", response_model=AvailableUsersResponse)
async def get_available_users_endpoint():
    """Get list of all available users from tokens file"""
    users = get_available_users_from_memory()
    return AvailableUsersResponse(available_users=users)

@app.get("/available-users/{project}", response_model=AvailableUsersResponse)
async def get_available_users_by_project(project: Project):
    """Get list of available users for a specific project"""
    users = get_available_users_from_memory(project=project)
    return AvailableUsersResponse(available_users=users)

@app.get("/available-users/{project}/{env}", response_model=AvailableUsersResponse)
async def get_available_users_by_project_env(project: Project, env: Environment):
    """Get list of available users for a specific project and environment"""
    users = get_available_users_from_memory(project=project, env=env)
    return AvailableUsersResponse(available_users=users)

@app.get("/user-project-access/{username}", response_model=UserAccessResponse)
async def get_user_project_access(username: str):
    """Get project access for a user based on group membership"""
    try:
        # Load group configuration
        group_config = load_group_config()
        
        # Get user's groups
        user_groups = get_user_groups(username)
        
        # Check access for each project
        accessible_projects = {}
        
        project_configs = group_config.get("project_name", {})
        for project_name, project_config in project_configs.items():
            accessible_envs = check_project_access(user_groups, project_config)
            if accessible_envs:
                accessible_projects[project_name] = accessible_envs
        
        return UserAccessResponse(
            username=username,
            user_groups=user_groups,
            accessible_projects=accessible_projects,
            has_access=bool(accessible_projects)
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error checking user access: {e}")

@app.get("/user-project-access", response_model=UserAccessResponse)
async def get_current_user_project_access(
    x_user_id: str = Header(..., description="Username to check access for")
):
    """Get project access for the current user (from header) based on group membership"""
    return await get_user_project_access(x_user_id)

@app.post("/launch-session", response_model=LaunchSessionResponse)
async def launch_session_endpoint(
    request: LaunchSessionRequest,
    username: str = Header(..., description="Username to look up token from tokens.json")
):
    """Launch a session with the provided parameters"""
    # First check if user has access to launch in this project and environment
    has_access = check_user_access_for_launch(username, request.project, request.env)
    
    if not has_access:
        raise HTTPException(
            status_code=403,
            detail=f"User '{username}' does not have permission to launch sessions in project '{request.project.value}', environment '{request.env.value}'. Check group membership."
        )
    
    # This will automatically generate token if user has access but token doesn't exist
    username, token = get_or_create_user_token(request.project, request.env, username)
    base_url = get_base_url(request.env, request.project)

    # Handle node selection logic
    final_node_selection = request.node_selection
    
    # For PROJECT1, if no node_selection provided, use "N" as default
    if request.project == Project.PROJECT1 and not request.node_selection:
        final_node_selection = "N"
    
    # For PROJECT2, ignore any node_selection provided by user
    if request.project == Project.PROJECT2:
        final_node_selection = None

    try:
        # For PROJECT1, validate node selection if provided (including default "N")
        if request.project == Project.PROJECT1 and final_node_selection:
            await validate_node_selection(base_url, final_node_selection, username)

        # Now proceed with the actual launch session call
        response_data, actual_session_name = await launch_session_api(
            base_url=base_url,
            token=token,
            custom_session_name=request.session_name,
            workbench=request.workbench,
            cluster=request.cluster
        )

        if response_data and "result" in response_data and "url" in response_data["result"]:
            formatted_base_url = format_base_url(base_url)
            full_url = formatted_base_url + response_data["result"]["url"]
            
            return LaunchSessionResponse(
                success=True,
                message="Session launched successfully",
                session_url=full_url,
                session_name=actual_session_name
            )
        else:
            return LaunchSessionResponse(
                success=False,
                message="Unexpected response format from external API",
                error=str(response_data)
            )
            
    except HTTPException:
        raise
    except Exception as e:
        return LaunchSessionResponse(
            success=False,
            message="Failed to launch session",
            error=str(e)
        )

@app.get("/sessions", response_model=GetSessionsResponse)
async def get_sessions_endpoint(
    username: str = Header(..., description="Username to look up token from tokens.json"),
    env: Environment = Query(..., description="Environment: DEV, UAT, or PROD"),
    project: Project = Query(..., description="Project: PROJECT1 or PROJECT2")
):
    """Get all sessions for a user"""
    # This will automatically generate token if user has access but token doesn't exist
    username, token = get_or_create_user_token(project, env, username)
    base_url = get_base_url(env, project)

    try:
        response_data = await get_sessions_api(base_url, token)

        if response_data and "result" in response_data and "sessions" in response_data["result"]:
            sessions = []
            for session_data in response_data["result"]["sessions"]:
                session_info = extract_session_info(base_url, session_data)
                sessions.append(session_info)
            
            return GetSessionsResponse(
                success=True,
                message=f"Found {len(sessions)} sessions",
                sessions=sessions
            )
        else:
            return GetSessionsResponse(
                success=False,
                message="No sessions found or unexpected response format",
                error=str(response_data) if response_data else "Empty response"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        return GetSessionsResponse(
            success=False,
            message="Failed to get sessions",
            error=str(e)
        )

@app.post("/stop-session", response_model=StopSessionResponse)
async def stop_session_endpoint(
    request: StopSessionRequest,
    username: str = Header(..., description="Username to look up token from tokens.json")
):
    """Stop/kill one or more sessions by session_ids"""
    # This will automatically generate token if user has access but token doesn't exist
    username, token = get_or_create_user_token(request.project, request.env, username)
    base_url = get_base_url(request.env, request.project)

    try:
        response_data = await stop_session_api(
            base_url=base_url,
            token=token,
            session_ids=request.session_ids,
            force_quit=request.force_quit,
            suspend_session=request.suspend_session
        )

        if response_data:
            return StopSessionResponse(
                success=True,
                message=f"Successfully stopped {len(request.session_ids)} sessions",
                stopped_sessions=request.session_ids
            )
        else:
            return StopSessionResponse(
                success=False,
                message="Empty response from external API",
                error="No response data received"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        return StopSessionResponse(
            success=False,
            message="Failed to stop sessions",
            error=str(e)
        )

@app.post("/admin/reload-tokens", response_model=ReloadResponse)
async def reload_tokens():
    """Reload tokens.json file"""
    try:
        load_tokens_data(force_reload=True)
        return ReloadResponse(
            success=True,
            message="Tokens data reloaded successfully",
            timestamp=datetime.now().isoformat()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to reload tokens: {e}")

@app.post("/admin/reload-group-config", response_model=ReloadResponse)
async def reload_group_config():
    """Reload group_config.json file"""
    try:
        load_group_config(force_reload=True)
        return ReloadResponse(
            success=True,
            message="Group configuration reloaded successfully",
            timestamp=datetime.now().isoformat()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to reload group config: {e}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
