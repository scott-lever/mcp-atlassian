import asyncio
import logging
import os

import click
from dotenv import load_dotenv

from .utils.logging import setup_logging

__version__ = "0.8.4"

# Initialize logging with appropriate level
logging_level = logging.WARNING
if os.getenv("MCP_VERBOSE", "").lower() in ("true", "1", "yes"):
    logging_level = logging.DEBUG

# Set up logging using the utility function
logger = setup_logging(logging_level)


@click.command()
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Increase verbosity (can be used multiple times)",
)
@click.option(
    "--env-file", type=click.Path(exists=True, dir_okay=False), help="Path to .env file"
)
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse"]),
    default="stdio",
    help="Transport type (stdio or sse)",
)
@click.option(
    "--port",
    default=8000,
    help="Port to listen on for SSE transport",
)
@click.option(
    "--confluence-url",
    help="Confluence URL (e.g., https://your-domain.atlassian.net/wiki)",
)
@click.option("--confluence-username", help="Confluence username/email")
@click.option("--confluence-token", help="Confluence API token")
@click.option(
    "--confluence-personal-token",
    help="Confluence Personal Access Token (for Confluence Server/Data Center)",
)
@click.option(
    "--confluence-ssl-verify/--no-confluence-ssl-verify",
    default=True,
    help="Verify SSL certificates for Confluence Server/Data Center (default: verify)",
)
@click.option(
    "--confluence-spaces-filter",
    help="Comma-separated list of Confluence space keys to filter search results",
)
@click.option(
    "--jira-url",
    help="Jira URL (e.g., https://your-domain.atlassian.net or https://jira.your-company.com)",
)
@click.option("--jira-username", help="Jira username/email (for Jira Cloud)")
@click.option("--jira-token", help="Jira API token (for Jira Cloud)")
@click.option(
    "--jira-personal-token",
    help="Jira Personal Access Token (for Jira Server/Data Center)",
)
@click.option(
    "--jira-ssl-verify/--no-jira-ssl-verify",
    default=True,
    help="Verify SSL certificates for Jira Server/Data Center (default: verify)",
)
@click.option(
    "--jira-projects-filter",
    help="Comma-separated list of Jira project keys to filter search results",
)
@click.option(
    "--read-only",
    is_flag=True,
    help="Run in read-only mode (disables all write operations)",
)
def main(
    verbose: bool,
    env_file: str | None,
    transport: str,
    port: int,
    confluence_url: str | None,
    confluence_username: str | None,
    confluence_token: str | None,
    confluence_personal_token: str | None,
    confluence_ssl_verify: bool,
    confluence_spaces_filter: str | None,
    jira_url: str | None,
    jira_username: str | None,
    jira_token: str | None,
    jira_personal_token: str | None,
    jira_ssl_verify: bool,
    jira_projects_filter: str | None,
    read_only: bool = False,
) -> None:
    """MCP Atlassian Server - Jira and Confluence functionality for MCP

    Supports both Atlassian Cloud and Jira Server/Data Center deployments.
    """
    # Configure logging based on verbosity
    logging_level = logging.WARNING
    if verbose == 1:
        logging_level = logging.INFO
    elif verbose >= 2:
        logging_level = logging.DEBUG

    # Use our utility function for logging setup
    global logger
    logger = setup_logging(logging_level)

    def was_option_provided(ctx: click.Context, param_name: str) -> bool:
        return (
            ctx.get_parameter_source(param_name) != click.core.ParameterSource.DEFAULT
        )

    # Load environment variables from file if specified, otherwise try default .env
    if env_file:
        logger.debug(f"Loading environment from file: {env_file}")
        load_dotenv(env_file)
    else:
        logger.debug("Attempting to load environment from default .env file")
        load_dotenv()

    # Check environment variables if CLI options were not used (or kept default)
    # CLI arguments take precedence over environment variables

    # Determine final transport mode
    final_transport = transport
    if transport == "stdio":  # Check if the default CLI value is still set
        env_transport = os.getenv("TRANSPORT", "stdio").lower()
        if env_transport in ["stdio", "sse"]:
            final_transport = env_transport
            logger.debug(
                f"Using transport '{final_transport}' from environment variable."
            )

    # Determine final port only if transport is SSE
    final_port = port
    if final_transport == "sse":
        if port == 8000:  # Check if the default CLI value is still set
            env_port_str = os.getenv("PORT")
            if env_port_str and env_port_str.isdigit():
                final_port = int(env_port_str)
                logger.debug(
                    f"Using port '{final_port}' from environment variable for SSE transport."
                )
        else:  # Port was specified via CLI, log it
            logger.debug(
                f"Using port '{final_port}' from command line argument for SSE transport."
            )

    # Set environment variables from command line arguments if provided
    if confluence_url:
        os.environ["CONFLUENCE_URL"] = confluence_url
    if confluence_username:
        os.environ["CONFLUENCE_USERNAME"] = confluence_username
    if confluence_token:
        os.environ["CONFLUENCE_API_TOKEN"] = confluence_token
    if confluence_personal_token:
        os.environ["CONFLUENCE_PERSONAL_TOKEN"] = confluence_personal_token
    if jira_url:
        os.environ["JIRA_URL"] = jira_url
    if jira_username:
        os.environ["JIRA_USERNAME"] = jira_username
    if jira_token:
        os.environ["JIRA_API_TOKEN"] = jira_token
    if jira_personal_token:
        os.environ["JIRA_PERSONAL_TOKEN"] = jira_personal_token

    # Set read-only mode from CLI flag
    if read_only:
        os.environ["READ_ONLY_MODE"] = "true"

    # Get the current click context to check parameter sources
    click_ctx = click.get_current_context()

    # Set SSL verification for Confluence Server/Data Center, respecting env if CLI flag is default
    if was_option_provided(click_ctx, "confluence_ssl_verify"):
        os.environ["CONFLUENCE_SSL_VERIFY"] = str(confluence_ssl_verify).lower()
    # else: environment variable (if set) will be used by ConfluenceConfig.from_env()

    # Set spaces filter for Confluence
    if confluence_spaces_filter:
        os.environ["CONFLUENCE_SPACES_FILTER"] = confluence_spaces_filter

    # Set SSL verification for Jira Server/Data Center, respecting env if CLI flag is default
    if was_option_provided(click_ctx, "jira_ssl_verify"):
        os.environ["JIRA_SSL_VERIFY"] = str(jira_ssl_verify).lower()
    # else: environment variable (if set) will be used by JiraConfig.from_env()

    # Set projects filter for Jira
    if jira_projects_filter:
        os.environ["JIRA_PROJECTS_FILTER"] = jira_projects_filter

    from . import server

    # Run the server with specified transport
    asyncio.run(server.run_server(transport=final_transport, port=final_port))


__all__ = ["main", "server", "__version__"]

if __name__ == "__main__":
    main()
