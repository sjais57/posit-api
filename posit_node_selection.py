Updated validate_node_selection Logic

Here is the updated version of validate_node_selection(), which you can paste into your script to replace the old HTTP call:

async def validate_node_selection(base_url: str, node_selection_flag: str, username: str) -> str:
    """
    Validate and determine the actual node using local CLI script for Project1.

    Args:
        base_url (str): The base URL for the cluster
        node_selection_flag (str): Either "P" or "V" (user intent)
        username (str): The username launching the session

    Returns:
        str: Final validated node name (e.g., node123.posit-cluster.dev)
    """
    try:
        logger.info(f"Running posit_select_node.py with flag: {node_selection_flag} for user: {username}")
        
        script_path = os.path.join(os.path.dirname(__file__), "posit_select_node.py")
        if not os.path.exists(script_path):
            raise FileNotFoundError(f"Node selection script not found at {script_path}")

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


Update in launch_session_endpoint()

In your existing endpoint /launch-session, update the node selection logic block like this:

# For PROJECT1, if no node_selection provided, default to "P"
if request.project == Project.PROJECT1:
    final_node_selection_flag = request.node_selection or "P"
    
    # Run node selection script and get actual node name
    selected_node = await validate_node_selection(base_url, final_node_selection_flag, username)
    
    logger.info(f"Validated node for Project1: {selected_node}")
    # Add placement constraint for selected node
    launch_parameters["placement_constraints"] = [f"node=={selected_node}"]
else:
    # PROJECT2 ignores node selection
    selected_node = None


def get_server_list_path(project: Project) -> str:
    """
    Resolve server-list.txt path from environment variable.
    PROJECT1 -> PROJECT1_SERVER_LIST
    PROJECT2 -> PROJECT2_SERVER_LIST
    """
    env_var = f"{project.value}_SERVER_LIST"
    path = os.getenv(env_var)

    if not path:
        raise HTTPException(
            status_code=500,
            detail=f"Environment variable '{env_var}' is not set"
        )

    if not os.path.exists(path):
        raise HTTPException(
            status_code=500,
            detail=f"Server list file not found at: {path}"
        )

    return path

def get_base_url(env: Environment, project: Project) -> str:
    """
    Read first server from project-specific server-list.txt
    """
    server_list_path = get_server_list_path(project)

    with open(server_list_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                logger.info(
                    f"Using server '{line}' for {env.value}/{project.value}"
                )
                return line

    raise HTTPException(
        status_code=500,
        detail=f"No valid servers found in {server_list_path}"
    )

