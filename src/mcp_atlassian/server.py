import json
import logging
import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, cast

from mcp.server import Server
from mcp.types import TextContent, Tool

from .confluence import ConfluenceFetcher
from .confluence.config import ConfluenceConfig
from .jira import JiraFetcher
from .jira.config import JiraConfig
from .utils.io import is_read_only_mode
from .utils.logging import log_config_param
from .utils.urls import is_atlassian_cloud_url

# Configure logging
logger = logging.getLogger("mcp-atlassian")


@dataclass
class AppContext:
    """Application context for MCP Atlassian."""

    confluence: ConfluenceFetcher | None = None
    jira: JiraFetcher | None = None


def get_available_services() -> dict[str, bool | None]:
    """Determine which services are available based on environment variables."""

    # Check for either cloud authentication (URL + username + API token)
    # or server/data center authentication (URL + ( personal token or username + API token ))
    confluence_url = os.getenv("CONFLUENCE_URL")
    if confluence_url:
        is_cloud = is_atlassian_cloud_url(confluence_url)

        if is_cloud:
            confluence_is_setup = all(
                [
                    confluence_url,
                    os.getenv("CONFLUENCE_USERNAME"),
                    os.getenv("CONFLUENCE_API_TOKEN"),
                ]
            )
            logger.info("Using Confluence Cloud authentication method")
        else:
            confluence_is_setup = all(
                [
                    confluence_url,
                    os.getenv("CONFLUENCE_PERSONAL_TOKEN")
                    # Some on prem/data center use username and api token too.
                    or (
                        os.getenv("CONFLUENCE_USERNAME")
                        and os.getenv("CONFLUENCE_API_TOKEN")
                    ),
                ]
            )
            logger.info("Using Confluence Server/Data Center authentication method")
    else:
        confluence_is_setup = False

    # Check for either cloud authentication (URL + username + API token)
    # or server/data center authentication (URL + personal token)
    jira_url = os.getenv("JIRA_URL")
    if jira_url:
        is_cloud = is_atlassian_cloud_url(jira_url)

        if is_cloud:
            jira_is_setup = all(
                [jira_url, os.getenv("JIRA_USERNAME"), os.getenv("JIRA_API_TOKEN")]
            )
            logger.info("Using Jira Cloud authentication method")
        else:
            jira_is_setup = all([jira_url, os.getenv("JIRA_PERSONAL_TOKEN")])
            logger.info("Using Jira Server/Data Center authentication method")
    else:
        jira_is_setup = False

    return {"confluence": confluence_is_setup, "jira": jira_is_setup}


@asynccontextmanager
async def server_lifespan(server: Server) -> AsyncIterator[AppContext]:
    """Initialize and clean up application resources."""
    # Get available services
    services = get_available_services()

    try:
        # Log the startup information
        logger.info("Starting MCP Atlassian server")

        # Log read-only mode status
        read_only = is_read_only_mode()
        logger.info(f"Read-only mode: {'ENABLED' if read_only else 'DISABLED'}")

        confluence = None
        jira = None

        # Initialize Confluence if configured
        if services["confluence"]:
            logger.info("Attempting to initialize Confluence client...")
            try:
                confluence_config = ConfluenceConfig.from_env()
                log_config_param(logger, "Confluence", "URL", confluence_config.url)
                log_config_param(
                    logger, "Confluence", "Auth Type", confluence_config.auth_type
                )
                if confluence_config.auth_type == "basic":
                    log_config_param(
                        logger, "Confluence", "Username", confluence_config.username
                    )
                    log_config_param(
                        logger,
                        "Confluence",
                        "API Token",
                        confluence_config.api_token,
                        sensitive=True,
                    )
                else:
                    log_config_param(
                        logger,
                        "Confluence",
                        "Personal Token",
                        confluence_config.personal_token,
                        sensitive=True,
                    )
                log_config_param(
                    logger,
                    "Confluence",
                    "SSL Verify",
                    str(confluence_config.ssl_verify),
                )
                log_config_param(
                    logger,
                    "Confluence",
                    "Spaces Filter",
                    confluence_config.spaces_filter,
                )

                confluence = ConfluenceFetcher(config=confluence_config)
                logger.info("Confluence client initialized successfully.")
            except Exception as e:
                logger.error(
                    f"Failed to initialize Confluence client: {e}", exc_info=True
                )

        # Initialize Jira if configured
        if services["jira"]:
            logger.info("Attempting to initialize Jira client...")
            try:
                jira_config = JiraConfig.from_env()
                log_config_param(logger, "Jira", "URL", jira_config.url)
                log_config_param(logger, "Jira", "Auth Type", jira_config.auth_type)
                if jira_config.auth_type == "basic":
                    log_config_param(logger, "Jira", "Username", jira_config.username)
                    log_config_param(
                        logger,
                        "Jira",
                        "API Token",
                        jira_config.api_token,
                        sensitive=True,
                    )
                else:
                    log_config_param(
                        logger,
                        "Jira",
                        "Personal Token",
                        jira_config.personal_token,
                        sensitive=True,
                    )
                log_config_param(
                    logger, "Jira", "SSL Verify", str(jira_config.ssl_verify)
                )
                log_config_param(
                    logger, "Jira", "Projects Filter", jira_config.projects_filter
                )

                jira = JiraFetcher(config=jira_config)
                logger.info("Jira client initialized successfully.")
            except Exception as e:
                logger.error(f"Failed to initialize Jira client: {e}", exc_info=True)

        # Provide context to the application
        yield AppContext(confluence=confluence, jira=jira)
    finally:
        # Cleanup resources if needed
        pass


# Create server instance
app = Server("mcp-atlassian", lifespan=server_lifespan)


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available Confluence and Jira tools."""
    tools = []
    ctx = app.request_context.lifespan_context

    # Check if we're in read-only mode
    read_only = is_read_only_mode()

    # Add Confluence tools if Confluence is configured
    if ctx and ctx.confluence:
        # Always add read operations
        tools.extend(
            [
                Tool(
                    name="confluence_search",
                    description="Search Confluence content using simple terms or CQL",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query - can be either a simple text (e.g. 'project documentation') or a CQL query string. Simple queries use 'siteSearch' by default, to mimic the WebUI search, with an automatic fallback to 'text' search if not supported. Examples of CQL:\n"
                                "- Basic search: 'type=page AND space=DEV'\n"
                                "- Personal space search: 'space=\"~username\"' (note: personal space keys starting with ~ must be quoted)\n"
                                "- Search by title: 'title~\"Meeting Notes\"'\n"
                                "- Use siteSearch: 'siteSearch ~ \"important concept\"'\n"
                                "- Use text search: 'text ~ \"important concept\"'\n"
                                "- Recent content: 'created >= \"2023-01-01\"'\n"
                                "- Content with specific label: 'label=documentation'\n"
                                "- Recently modified content: 'lastModified > startOfMonth(\"-1M\")'\n"
                                "- Content modified this year: 'creator = currentUser() AND lastModified > startOfYear()'\n"
                                "- Content you contributed to recently: 'contributor = currentUser() AND lastModified > startOfWeek()'\n"
                                "- Content watched by user: 'watcher = \"user@domain.com\" AND type = page'\n"
                                '- Exact phrase in content: \'text ~ "\\"Urgent Review Required\\"" AND label = "pending-approval"\'\n'
                                '- Title wildcards: \'title ~ "Minutes*" AND (space = "HR" OR space = "Marketing")\'\n'
                                'Note: Special identifiers need proper quoting in CQL: personal space keys (e.g., "~username"), reserved words, numeric IDs, and identifiers with special characters.',
                            },
                            "limit": {
                                "type": "number",
                                "description": "Maximum number of results (1-50)",
                                "default": 10,
                                "minimum": 1,
                                "maximum": 50,
                            },
                            "spaces_filter": {
                                "type": "string",
                                "description": "Comma-separated list of space keys to filter results by. Overrides the environment variable CONFLUENCE_SPACES_FILTER if provided.",
                            },
                        },
                        "required": ["query"],
                    },
                ),
                Tool(
                    name="confluence_get_page",
                    description="Get content of a specific Confluence page by ID",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "page_id": {
                                "type": "string",
                                "description": "Confluence page ID (numeric ID, can be found in the page URL). "
                                "For example, in the URL 'https://example.atlassian.net/wiki/spaces/TEAM/pages/123456789/Page+Title', "
                                "the page ID is '123456789'",
                            },
                            "include_metadata": {
                                "type": "boolean",
                                "description": "Whether to include page metadata such as creation date, last update, version, and labels",
                                "default": True,
                            },
                            "convert_to_markdown": {
                                "type": "boolean",
                                "description": "Whether to convert page to markdown (true) or keep it in raw HTML format (false). Raw HTML can reveal macros (like dates) not visible in markdown, but CAUTION: using HTML significantly increases token usage in AI responses.",
                                "default": True,
                            },
                        },
                        "required": ["page_id"],
                    },
                ),
                Tool(
                    name="confluence_get_page_children",
                    description="Get child pages of a specific Confluence page",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "parent_id": {
                                "type": "string",
                                "description": "The ID of the parent page whose children you want to retrieve",
                            },
                            "expand": {
                                "type": "string",
                                "description": "Fields to expand in the response (e.g., 'version', 'body.storage')",
                                "default": "version",
                            },
                            "limit": {
                                "type": "number",
                                "description": "Maximum number of child pages to return (1-50)",
                                "default": 25,
                                "minimum": 1,
                                "maximum": 50,
                            },
                            "include_content": {
                                "type": "boolean",
                                "description": "Whether to include the page content in the response",
                                "default": False,
                            },
                        },
                        "required": ["parent_id"],
                    },
                ),
                Tool(
                    name="confluence_get_page_ancestors",
                    description="Get ancestor (parent) pages of a specific Confluence page",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "page_id": {
                                "type": "string",
                                "description": "The ID of the page whose ancestors you want to retrieve",
                            },
                        },
                        "required": ["page_id"],
                    },
                ),
                Tool(
                    name="confluence_get_comments",
                    description="Get comments for a specific Confluence page",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "page_id": {
                                "type": "string",
                                "description": "Confluence page ID (numeric ID, can be parsed from URL, "
                                "e.g. from 'https://example.atlassian.net/wiki/spaces/TEAM/pages/123456789/Page+Title' "
                                "-> '123456789')",
                            }
                        },
                        "required": ["page_id"],
                    },
                ),
            ]
        )

        # Only add write operations if not in read-only mode
        if not read_only:
            tools.extend(
                [
                    Tool(
                        name="confluence_create_page",
                        description="Create a new Confluence page",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "space_key": {
                                    "type": "string",
                                    "description": "The key of the space to create the page in "
                                    "(usually a short uppercase code like 'DEV', 'TEAM', or 'DOC')",
                                },
                                "title": {
                                    "type": "string",
                                    "description": "The title of the page",
                                },
                                "content": {
                                    "type": "string",
                                    "description": "The content of the page in Markdown format. "
                                    "Supports headings, lists, tables, code blocks, and other "
                                    "Markdown syntax",
                                },
                                "parent_id": {
                                    "type": "string",
                                    "description": "Optional parent page ID. If provided, this page "
                                    "will be created as a child of the specified page",
                                },
                            },
                            "required": ["space_key", "title", "content"],
                        },
                    ),
                    Tool(
                        name="confluence_update_page",
                        description="Update an existing Confluence page",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "page_id": {
                                    "type": "string",
                                    "description": "The ID of the page to update",
                                },
                                "title": {
                                    "type": "string",
                                    "description": "The new title of the page",
                                },
                                "content": {
                                    "type": "string",
                                    "description": "The new content of the page in Markdown format",
                                },
                                "is_minor_edit": {
                                    "type": "boolean",
                                    "description": "Whether this is a minor edit",
                                    "default": False,
                                },
                                "version_comment": {
                                    "type": "string",
                                    "description": "Optional comment for this version",
                                    "default": "",
                                },
                                "parent_id": {
                                    "type": "string",
                                    "description": "Optional the new parent page ID",
                                },
                            },
                            "required": ["page_id", "title", "content"],
                        },
                    ),
                    Tool(
                        name="confluence_delete_page",
                        description="Delete an existing Confluence page",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "page_id": {
                                    "type": "string",
                                    "description": "The ID of the page to delete",
                                },
                            },
                            "required": ["page_id"],
                        },
                    ),
                ]
            )

    # Add Jira tools if Jira is configured
    if ctx and ctx.jira:
        # Always add read operations
        tools.extend(
            [
                Tool(
                    name="jira_get_issue",
                    description="Get details of a specific Jira issue including its Epic links and relationship information",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "issue_key": {
                                "type": "string",
                                "description": "Jira issue key (e.g., 'PROJ-123')",
                            },
                            "fields": {
                                "type": "string",
                                "description": "Fields to return. Can be a comma-separated list (e.g., 'summary,status,customfield_10010'), '*all' for all fields (including custom fields), or omitted for essential fields only",
                                "default": "summary,description,status,assignee,reporter,labels,priority,created,updated,issuetype",
                            },
                            "expand": {
                                "type": "string",
                                "description": (
                                    "Optional fields to expand. Examples: 'renderedFields' "
                                    "(for rendered content), 'transitions' (for available "
                                    "status transitions), 'changelog' (for history)"
                                ),
                                "default": None,
                            },
                            "comment_limit": {
                                "type": "integer",
                                "description": (
                                    "Maximum number of comments to include "
                                    "(0 or null for no comments)"
                                ),
                                "minimum": 0,
                                "maximum": 100,
                                "default": 10,
                            },
                            "properties": {
                                "type": "string",
                                "description": "A comma-separated list of issue properties to return",
                                "default": None,
                            },
                            "update_history": {
                                "type": "boolean",
                                "description": "Whether to update the issue view history for the requesting user",
                                "default": True,
                            },
                        },
                        "required": ["issue_key"],
                    },
                ),
                Tool(
                    name="jira_search",
                    description="Search Jira issues using JQL (Jira Query Language)",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "jql": {
                                "type": "string",
                                "description": "JQL query string (Jira Query Language). Examples:\n"
                                '- Find Epics: "issuetype = Epic AND project = PROJ"\n'
                                '- Find issues in Epic: "parent = PROJ-123"\n'
                                "- Find by status: \"status = 'In Progress' AND project = PROJ\"\n"
                                '- Find by assignee: "assignee = currentUser()"\n'
                                '- Find recently updated: "updated >= -7d AND project = PROJ"\n'
                                '- Find by label: "labels = frontend AND project = PROJ"\n'
                                '- Find by priority: "priority = High AND project = PROJ"',
                            },
                            "fields": {
                                "type": "string",
                                "description": (
                                    "Comma-separated fields to return in the results. "
                                    "Use '*all' for all fields, or specify individual "
                                    "fields like 'summary,status,assignee,priority'"
                                ),
                                "default": "summary,description,status,assignee,reporter,labels,priority,created,updated,issuetype",
                            },
                            "limit": {
                                "type": "number",
                                "description": "Maximum number of results (1-50)",
                                "default": 10,
                                "minimum": 1,
                                "maximum": 50,
                            },
                            "startAt": {
                                "type": "number",
                                "description": "Starting index for pagination (0-based)",
                                "default": 0,
                                "minimum": 0,
                            },
                            "projects_filter": {
                                "type": "string",
                                "description": "Comma-separated list of project keys to filter results by. Overrides the environment variable JIRA_PROJECTS_FILTER if provided.",
                            },
                        },
                        "required": ["jql"],
                    },
                ),
                Tool(
                    name="jira_search_fields",
                    description="Search Jira fields by keyword with fuzzy match",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "keyword": {
                                "type": "string",
                                "description": "Keyword for fuzzy search. If left empty, lists the first 'limit' available fields in their default order.",
                                "default": "",
                            },
                            "limit": {
                                "type": "number",
                                "description": "Maximum number of results",
                                "default": 10,
                                "minimum": 1,
                            },
                            "refresh": {
                                "type": "boolean",
                                "description": "Whether to force refresh the field list",
                                "default": False,
                            },
                        },
                        "required": [],
                    },
                ),
                Tool(
                    name="jira_get_project_issues",
                    description="Get all issues for a specific Jira project",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "project_key": {
                                "type": "string",
                                "description": "The project key",
                            },
                            "limit": {
                                "type": "number",
                                "description": "Maximum number of results (1-50)",
                                "default": 10,
                                "minimum": 1,
                                "maximum": 50,
                            },
                            "startAt": {
                                "type": "number",
                                "description": "Starting index for pagination (0-based)",
                                "default": 0,
                                "minimum": 0,
                            },
                        },
                        "required": ["project_key"],
                    },
                ),
                Tool(
                    name="jira_get_epic_issues",
                    description="Get all issues linked to a specific epic",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "epic_key": {
                                "type": "string",
                                "description": "The key of the epic (e.g., 'PROJ-123')",
                            },
                            "limit": {
                                "type": "number",
                                "description": "Maximum number of issues to return (1-50)",
                                "default": 10,
                                "minimum": 1,
                                "maximum": 50,
                            },
                            "startAt": {
                                "type": "number",
                                "description": "Starting index for pagination (0-based)",
                                "default": 0,
                                "minimum": 0,
                            },
                        },
                        "required": ["epic_key"],
                    },
                ),
                Tool(
                    name="jira_get_transitions",
                    description="Get available status transitions for a Jira issue",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "issue_key": {
                                "type": "string",
                                "description": "Jira issue key (e.g., 'PROJ-123')",
                            },
                        },
                        "required": ["issue_key"],
                    },
                ),
                Tool(
                    name="jira_get_worklog",
                    description="Get worklog entries for a Jira issue",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "issue_key": {
                                "type": "string",
                                "description": "Jira issue key (e.g., 'PROJ-123')",
                            },
                        },
                        "required": ["issue_key"],
                    },
                ),
                Tool(
                    name="jira_download_attachments",
                    description="Download attachments from a Jira issue",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "issue_key": {
                                "type": "string",
                                "description": "Jira issue key (e.g., 'PROJ-123')",
                            },
                            "target_dir": {
                                "type": "string",
                                "description": "Directory where attachments should be saved",
                            },
                        },
                        "required": ["issue_key", "target_dir"],
                    },
                ),
                Tool(
                    name="jira_get_agile_boards",
                    description="Get jira agile boards by name, project key, or type",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "board_name": {
                                "type": "string",
                                "description": "The name of board, support fuzzy search",
                            },
                            "project_key": {
                                "type": "string",
                                "description": "Jira project key (e.g., 'PROJ-123')",
                            },
                            "board_type": {
                                "type": "string",
                                "description": "The type of jira board (e.g., 'scrum', 'kanban')",
                            },
                            "startAt": {
                                "type": "number",
                                "description": "Starting index for pagination (0-based)",
                                "default": 0,
                            },
                            "limit": {
                                "type": "number",
                                "description": "Maximum number of results (1-50)",
                                "default": 10,
                                "minimum": 1,
                                "maximum": 50,
                            },
                        },
                    },
                ),
                Tool(
                    name="jira_get_board_issues",
                    description="Get all issues linked to a specific board",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "board_id": {
                                "type": "string",
                                "description": "The id of the board (e.g., '1001')",
                            },
                            "jql": {
                                "type": "string",
                                "description": "JQL query string (Jira Query Language). Examples:\n"
                                '- Find Epics: "issuetype = Epic AND project = PROJ"\n'
                                '- Find issues in Epic: "parent = PROJ-123"\n'
                                "- Find by status: \"status = 'In Progress' AND project = PROJ\"\n"
                                '- Find by assignee: "assignee = currentUser()"\n'
                                '- Find recently updated: "updated >= -7d AND project = PROJ"\n'
                                '- Find by label: "labels = frontend AND project = PROJ"\n'
                                '- Find by priority: "priority = High AND project = PROJ"',
                            },
                            "fields": {
                                "type": "string",
                                "description": (
                                    "Comma-separated fields to return in the results. "
                                    "Use '*all' for all fields, or specify individual "
                                    "fields like 'summary,status,assignee,priority'"
                                ),
                                "default": "*all",
                            },
                            "startAt": {
                                "type": "number",
                                "description": "Starting index for pagination (0-based)",
                                "default": 0,
                            },
                            "limit": {
                                "type": "number",
                                "description": "Maximum number of results (1-50)",
                                "default": 10,
                                "minimum": 1,
                                "maximum": 50,
                            },
                            "expand": {
                                "type": "string",
                                "description": "Fields to expand in the response (e.g., 'version', 'body.storage')",
                                "default": "version",
                            },
                        },
                        "required": ["board_id", "jql"],
                    },
                ),
                Tool(
                    name="jira_get_sprints_from_board",
                    description="Get jira sprints from board by state",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "board_id": {
                                "type": "string",
                                "description": "The id of board (e.g., '1000')",
                            },
                            "state": {
                                "type": "string",
                                "description": "Sprint state (e.g., 'active', 'future', 'closed')",
                            },
                            "startAt": {
                                "type": "number",
                                "description": "Starting index for pagination (0-based)",
                                "default": 0,
                            },
                            "limit": {
                                "type": "number",
                                "description": "Maximum number of results (1-50)",
                                "default": 10,
                                "minimum": 1,
                                "maximum": 50,
                            },
                        },
                    },
                ),
                Tool(
                    name="jira_create_sprint",
                    description="Create Jira sprint for a board",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "board_id": {
                                "type": "string",
                                "description": "The id of board (e.g., '1000')",
                            },
                            "sprint_name": {
                                "type": "string",
                                "description": "Name of the sprint (e.g., 'Sprint 1')",
                            },
                            "start_date": {
                                "type": "string",
                                "description": "Start time for sprint (ISO 8601 format)",
                            },
                            "end_date": {
                                "type": "string",
                                "description": "End time for sprint (ISO 8601 format)",
                            },
                            "goal": {
                                "type": "string",
                                "description": "Goal of the sprint",
                            },
                        },
                        "required": [
                            "board_id",
                            "sprint_name",
                            "start_date",
                            "end_date",
                        ],
                    },
                ),
                Tool(
                    name="jira_get_sprint_issues",
                    description="Get jira issues from sprint",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "sprint_id": {
                                "type": "string",
                                "description": "The id of sprint (e.g., '10001')",
                            },
                            "fields": {
                                "type": "string",
                                "description": (
                                    "Comma-separated fields to return in the results. "
                                    "Use '*all' for all fields, or specify individual "
                                    "fields like 'summary,status,assignee,priority'"
                                ),
                                "default": "*all",
                            },
                            "startAt": {
                                "type": "number",
                                "description": "Starting index for pagination (0-based)",
                                "default": 0,
                            },
                            "limit": {
                                "type": "number",
                                "description": "Maximum number of results (1-50)",
                                "default": 10,
                                "minimum": 1,
                                "maximum": 50,
                            },
                        },
                        "required": ["sprint_id"],
                    },
                ),
                Tool(
                    name="jira_update_sprint",
                    description="Update jira sprint",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "sprint_id": {
                                "type": "string",
                                "description": "The id of sprint (e.g., '10001')",
                            },
                            "sprint_name": {
                                "type": "string",
                                "description": "Optional: New name for the sprint",
                            },
                            "state": {
                                "type": "string",
                                "description": "Optional: New state for the sprint (future|active|closed)",
                            },
                            "start_date": {
                                "type": "string",
                                "description": "Optional: New start date for the sprint",
                            },
                            "end_date": {
                                "type": "string",
                                "description": "Optional: New end date for the sprint",
                            },
                            "goal": {
                                "type": "string",
                                "description": "Optional: New goal for the sprint",
                            },
                        },
                        "required": ["sprint_id"],
                    },
                ),
            ]
        )

        # Only add write operations if not in read-only mode
        if not read_only:
            tools.extend(
                [
                    Tool(
                        name="jira_create_issue",
                        description="Create a new Jira issue with optional Epic link or parent for subtasks",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "project_key": {
                                    "type": "string",
                                    "description": (
                                        "The JIRA project key (e.g. 'PROJ', 'DEV', 'SUPPORT'). "
                                        "This is the prefix of issue keys in your project. "
                                        "Never assume what it might be, always ask the user."
                                    ),
                                },
                                "summary": {
                                    "type": "string",
                                    "description": "Summary/title of the issue",
                                },
                                "issue_type": {
                                    "type": "string",
                                    "description": (
                                        "Issue type (e.g. 'Task', 'Bug', 'Story', 'Epic', 'Subtask'). "
                                        "The available types depend on your project configuration. "
                                        "For subtasks, use 'Subtask' (not 'Sub-task') and include parent in additional_fields."
                                    ),
                                },
                                "assignee": {
                                    "type": "string",
                                    "description": "Assignee of the ticket (accountID, full name or e-mail)",
                                    "default": None,
                                },
                                "description": {
                                    "type": "string",
                                    "description": "Issue description",
                                    "default": "",
                                },
                                "components": {
                                    "type": "string",
                                    "description": "Comma-separated list of component names to assign (e.g., 'Frontend,API')",
                                    "default": "",
                                },
                                "additional_fields": {
                                    "type": "string",
                                    "description": (
                                        "Optional JSON string of additional fields to set. "
                                        "Examples:\n"
                                        '- Set priority: {"priority": {"name": "High"}}\n'
                                        '- Add labels: {"labels": ["frontend", "urgent"]}\n'
                                        '- Link to parent (for any issue type): {"parent": "PROJ-123"}\n'
                                        '- Set Fix Version/s: {"fixVersions": [{"id": "10020"}]}\n'
                                        '- Custom fields: {"customfield_10010": "value"}'
                                    ),
                                    "default": "{}",
                                },
                            },
                            "required": ["project_key", "summary", "issue_type"],
                        },
                    ),
                    Tool(
                        name="jira_batch_create_issues",
                        description="Create multiple Jira issues in a batch",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "issues": {
                                    "type": "string",
                                    "description": (
                                        "JSON array of issue objects. Each object should contain:\n"
                                        "- project_key (required): The project key (e.g., 'PROJ')\n"
                                        "- summary (required): Issue summary/title\n"
                                        "- issue_type (required): Type of issue (e.g., 'Task', 'Bug')\n"
                                        "- description (optional): Issue description\n"
                                        "- assignee (optional): Assignee username or email\n"
                                        "- components (optional): Array of component names\n"
                                        "Example: [\n"
                                        '  {"project_key": "PROJ", "summary": "Issue 1", "issue_type": "Task"},\n'
                                        '  {"project_key": "PROJ", "summary": "Issue 2", "issue_type": "Bug", "components": ["Frontend"]}\n'
                                        "]"
                                    ),
                                },
                                "validate_only": {
                                    "type": "boolean",
                                    "description": "If true, only validates the issues without creating them",
                                    "default": False,
                                },
                            },
                            "required": ["issues"],
                        },
                    ),
                    Tool(
                        name="jira_update_issue",
                        description="Update an existing Jira issue including changing status, adding Epic links, updating fields, etc.",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "issue_key": {
                                    "type": "string",
                                    "description": "Jira issue key (e.g., 'PROJ-123')",
                                },
                                "fields": {
                                    "type": "string",
                                    "description": (
                                        "A valid JSON object of fields to update as a string. "
                                        'Example: \'{"summary": "New title", "description": "Updated description", '
                                        '"priority": {"name": "High"}, "assignee": "john.doe"}\''
                                    ),
                                },
                                "additional_fields": {
                                    "type": "string",
                                    "description": "Optional JSON string of additional fields to update. Use this for custom fields or more complex updates.",
                                    "default": "{}",
                                },
                                "attachments": {
                                    "type": "string",
                                    "description": "Optional JSON string or comma-separated list of file paths to attach to the issue. "
                                    'Example: "/path/to/file1.txt,/path/to/file2.txt" or "["/path/to/file1.txt","/path/to/file2.txt"]"',
                                },
                            },
                            "required": ["issue_key", "fields"],
                        },
                    ),
                    Tool(
                        name="jira_delete_issue",
                        description="Delete an existing Jira issue",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "issue_key": {
                                    "type": "string",
                                    "description": "Jira issue key (e.g. PROJ-123)",
                                },
                            },
                            "required": ["issue_key"],
                        },
                    ),
                    Tool(
                        name="jira_add_comment",
                        description="Add a comment to a Jira issue",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "issue_key": {
                                    "type": "string",
                                    "description": "Jira issue key (e.g., 'PROJ-123')",
                                },
                                "comment": {
                                    "type": "string",
                                    "description": "Comment text in Markdown format",
                                },
                            },
                            "required": ["issue_key", "comment"],
                        },
                    ),
                    Tool(
                        name="jira_add_worklog",
                        description="Add a worklog entry to a Jira issue",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "issue_key": {
                                    "type": "string",
                                    "description": "Jira issue key (e.g., 'PROJ-123')",
                                },
                                "time_spent": {
                                    "type": "string",
                                    "description": (
                                        "Time spent in Jira format. Examples: "
                                        "'1h 30m' (1 hour and 30 minutes), "
                                        "'1d' (1 day), '30m' (30 minutes), "
                                        "'4h' (4 hours)"
                                    ),
                                },
                                "comment": {
                                    "type": "string",
                                    "description": "Optional comment for the worklog in Markdown format",
                                },
                                "started": {
                                    "type": "string",
                                    "description": (
                                        "Optional start time in ISO format. "
                                        "If not provided, the current time will be used. "
                                        "Example: '2023-08-01T12:00:00.000+0000'"
                                    ),
                                },
                            },
                            "required": ["issue_key", "time_spent"],
                        },
                    ),
                    Tool(
                        name="jira_link_to_epic",
                        description="Link an existing issue to an epic",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "issue_key": {
                                    "type": "string",
                                    "description": "The key of the issue to link (e.g., 'PROJ-123')",
                                },
                                "epic_key": {
                                    "type": "string",
                                    "description": "The key of the epic to link to (e.g., 'PROJ-456')",
                                },
                            },
                            "required": ["issue_key", "epic_key"],
                        },
                    ),
                    Tool(
                        name="jira_create_issue_link",
                        description="Create a link between two Jira issues",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "link_type": {
                                    "type": "string",
                                    "description": "The type of link to create (e.g., 'Duplicate', 'Blocks', 'Relates to')",
                                },
                                "inward_issue_key": {
                                    "type": "string",
                                    "description": "The key of the inward issue (e.g., 'PROJ-123')",
                                },
                                "outward_issue_key": {
                                    "type": "string",
                                    "description": "The key of the outward issue (e.g., 'PROJ-456')",
                                },
                                "comment": {
                                    "type": "string",
                                    "description": "Optional comment to add to the link",
                                },
                                "comment_visibility": {
                                    "type": "object",
                                    "description": "Optional visibility settings for the comment",
                                    "properties": {
                                        "type": {
                                            "type": "string",
                                            "description": "Type of visibility restriction (e.g., 'group')",
                                        },
                                        "value": {
                                            "type": "string",
                                            "description": "Value for the visibility restriction (e.g., 'jira-software-users')",
                                        },
                                    },
                                },
                            },
                            "required": [
                                "link_type",
                                "inward_issue_key",
                                "outward_issue_key",
                            ],
                        },
                    ),
                    Tool(
                        name="jira_remove_issue_link",
                        description="Remove a link between two Jira issues",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "link_id": {
                                    "type": "string",
                                    "description": "The ID of the link to remove",
                                },
                            },
                            "required": ["link_id"],
                        },
                    ),
                    Tool(
                        name="jira_get_link_types",
                        description="Get all available issue link types",
                        inputSchema={
                            "type": "object",
                            "properties": {},
                            "required": [],
                        },
                    ),
                    Tool(
                        name="jira_transition_issue",
                        description="Transition a Jira issue to a new status",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "issue_key": {
                                    "type": "string",
                                    "description": "Jira issue key (e.g., 'PROJ-123')",
                                },
                                "transition_id": {
                                    "type": "string",
                                    "description": (
                                        "ID of the transition to perform. Use the jira_get_transitions tool first "
                                        "to get the available transition IDs for the issue. "
                                        "Example values: '11', '21', '31'"
                                    ),
                                },
                                "fields": {
                                    "type": "string",
                                    "description": (
                                        "JSON string of fields to update during the transition. "
                                        "Some transitions require specific fields to be set. "
                                        'Example: \'{"resolution": {"name": "Fixed"}}\''
                                    ),
                                    "default": "{}",
                                },
                                "comment": {
                                    "type": "string",
                                    "description": (
                                        "Comment to add during the transition (optional). "
                                        "This will be visible in the issue history."
                                    ),
                                },
                            },
                            "required": ["issue_key", "transition_id"],
                        },
                    ),
                ]
            )

    return tools


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> Sequence[TextContent]:
    """Handle tool calls for Confluence and Jira operations."""
    ctx = app.request_context.lifespan_context

    # Check if we're in read-only mode for write operations
    read_only = is_read_only_mode()

    try:
        # Helper functions for formatting results
        def format_comment(comment: Any) -> dict[str, Any]:
            if hasattr(comment, "to_simplified_dict"):
                # Cast the return value to dict[str, Any] to satisfy the type checker
                return cast(dict[str, Any], comment.to_simplified_dict())
            return {
                "id": comment.get("id"),
                "author": comment.get("author", {}).get("displayName", "Unknown"),
                "created": comment.get("created"),
                "body": comment.get("body"),
            }

        # Confluence operations
        if name == "confluence_search" and ctx and ctx.confluence:
            if not ctx or not ctx.confluence:
                raise ValueError("Confluence is not configured.")

            query = arguments.get("query", "")
            limit = min(int(arguments.get("limit", 10)), 50)
            spaces_filter = arguments.get("spaces_filter")

            # Check if the query is a simple search term or already a CQL query
            if query and not any(
                x in query
                for x in ["=", "~", ">", "<", " AND ", " OR ", "currentUser()"]
            ):
                # Convert simple search term to CQL siteSearch (previously it was a 'text' search)
                # This will use the same search mechanism as the WebUI and give much more relevant results
                original_query = query  # Store the original query for fallback
                try:
                    # Try siteSearch first - it's available in newer versions and provides better results
                    query = f'siteSearch ~ "{original_query}"'
                    logger.info(
                        f"Converting simple search term to CQL using siteSearch: {query}"
                    )
                    pages = ctx.confluence.search(
                        query, limit=limit, spaces_filter=spaces_filter
                    )
                except Exception as e:
                    # If siteSearch fails (possibly not supported in this Confluence version),
                    # fall back to text search which is supported in all versions
                    logger.warning(
                        f"siteSearch failed, falling back to text search: {str(e)}"
                    )
                    query = f'text ~ "{original_query}"'
                    logger.info(f"Falling back to text search with CQL: {query}")
                    pages = ctx.confluence.search(
                        query, limit=limit, spaces_filter=spaces_filter
                    )
            else:
                # Using direct CQL query as provided
                pages = ctx.confluence.search(
                    query, limit=limit, spaces_filter=spaces_filter
                )

            # Format results using the to_simplified_dict method
            search_results = [page.to_simplified_dict() for page in pages]

            return [
                TextContent(
                    type="text",
                    text=json.dumps(search_results, indent=2, ensure_ascii=False),
                )
            ]

        elif name == "confluence_get_page" and ctx and ctx.confluence:
            if not ctx or not ctx.confluence:
                raise ValueError("Confluence is not configured.")

            page_id = arguments.get("page_id")
            include_metadata = arguments.get("include_metadata", True)
            convert_to_markdown = arguments.get("convert_to_markdown", True)

            page = ctx.confluence.get_page_content(
                page_id, convert_to_markdown=convert_to_markdown
            )

            if include_metadata:
                # The to_simplified_dict method already includes the content,
                # so we don't need to include it separately at the root level
                result = {
                    "metadata": page.to_simplified_dict(),
                }
            else:
                # For backward compatibility, keep returning content directly
                result = {"content": page.content}

            return [
                TextContent(
                    type="text", text=json.dumps(result, indent=2, ensure_ascii=False)
                )
            ]

        elif name == "confluence_get_page_children" and ctx and ctx.confluence:
            if not ctx or not ctx.confluence:
                raise ValueError("Confluence is not configured.")

            parent_id = arguments.get("parent_id")
            expand = arguments.get("expand", "version")
            limit = min(int(arguments.get("limit", 25)), 50)
            include_content = arguments.get("include_content", False)
            convert_to_markdown = arguments.get("convert_to_markdown", True)
            start = arguments.get("start", 0)

            # Add body.storage to expand if content is requested
            if include_content and "body" not in expand:
                expand = f"{expand},body.storage" if expand else "body.storage"

            pages = None  # Initialize pages to None before try block

            try:
                pages = ctx.confluence.get_page_children(
                    page_id=parent_id,
                    start=start,
                    limit=limit,
                    expand=expand,
                    convert_to_markdown=convert_to_markdown,
                )

                child_pages = [page.to_simplified_dict() for page in pages]

                result = {
                    "parent_id": parent_id,
                    "total": len(child_pages),
                    "limit": limit,
                    "results": child_pages,
                }

            except Exception as e:
                # --- Error Handling ---
                logger.error(
                    f"Error getting/processing children for page ID {parent_id}: {e}",
                    exc_info=True,
                )
                result = {"error": f"Failed to get child pages: {e}"}

            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        result,
                        indent=2,
                        ensure_ascii=False,
                    ),
                )
            ]

        elif name == "confluence_get_page_ancestors" and ctx and ctx.confluence:
            if not ctx or not ctx.confluence:
                raise ValueError("Confluence is not configured.")

            page_id = arguments.get("page_id")

            # Get the ancestor pages
            ancestors = ctx.confluence.get_page_ancestors(page_id)

            # Format results
            ancestor_pages = [page.to_simplified_dict() for page in ancestors]

            return [
                TextContent(
                    type="text",
                    text=json.dumps(ancestor_pages, indent=2, ensure_ascii=False),
                )
            ]

        elif name == "confluence_get_comments" and ctx and ctx.confluence:
            if not ctx or not ctx.confluence:
                raise ValueError("Confluence is not configured.")

            page_id = arguments.get("page_id")
            comments = ctx.confluence.get_page_comments(page_id)

            # Format comments using their to_simplified_dict method if available
            formatted_comments = [format_comment(comment) for comment in comments]

            return [
                TextContent(
                    type="text",
                    text=json.dumps(formatted_comments, indent=2, ensure_ascii=False),
                )
            ]

        elif name == "confluence_create_page":
            if not ctx or not ctx.confluence:
                raise ValueError("Confluence is not configured.")

            # Write operation - check read-only mode
            if read_only:
                return [
                    TextContent(
                        "Operation 'confluence_create_page' is not available in read-only mode."
                    )
                ]

            # Extract arguments
            space_key = arguments.get("space_key")
            title = arguments.get("title")
            content = arguments.get("content")
            parent_id = arguments.get("parent_id")

            # Create the page (with automatic markdown conversion)
            page = ctx.confluence.create_page(
                space_key=space_key,
                title=title,
                body=content,
                parent_id=parent_id,
                is_markdown=True,
            )

            # Format the result
            result = page.to_simplified_dict()

            return [
                TextContent(
                    type="text",
                    text=f"Page created successfully:\n{json.dumps(result, indent=2, ensure_ascii=False)}",
                )
            ]

        elif name == "confluence_update_page":
            if not ctx or not ctx.confluence:
                raise ValueError("Confluence is not configured.")

            # Write operation - check read-only mode
            if read_only:
                return [
                    TextContent(
                        "Operation 'confluence_update_page' is not available in read-only mode."
                    )
                ]

            page_id = arguments.get("page_id")
            title = arguments.get("title")
            content = arguments.get("content")
            is_minor_edit = arguments.get("is_minor_edit", False)
            version_comment = arguments.get("version_comment", "")
            parent_id = arguments.get("parent_id")

            if not page_id or not title or not content:
                raise ValueError(
                    "Missing required parameters: page_id, title, and content are required."
                )

            # Update the page (with automatic markdown conversion)
            updated_page = ctx.confluence.update_page(
                page_id=page_id,
                title=title,
                body=content,
                is_minor_edit=is_minor_edit,
                version_comment=version_comment,
                is_markdown=True,
                parent_id=parent_id,
            )

            # Format results
            page_data = updated_page.to_simplified_dict()

            return [TextContent(type="text", text=json.dumps({"page": page_data}))]

        elif name == "confluence_delete_page":
            if not ctx or not ctx.confluence:
                raise ValueError("Confluence is not configured.")

            # Write operation - check read-only mode
            if read_only:
                return [
                    TextContent(
                        "Operation 'confluence_delete_page' is not available in read-only mode."
                    )
                ]

            page_id = arguments.get("page_id")

            if not page_id:
                raise ValueError("Missing required parameter: page_id is required.")

            try:
                # Delete the page
                result = ctx.confluence.delete_page(page_id=page_id)

                # Format results - our fixed implementation now correctly returns True on success
                if result:
                    response = {
                        "success": True,
                        "message": f"Page {page_id} deleted successfully",
                    }
                else:
                    # This branch should rarely be hit with our updated implementation
                    # but we keep it for safety
                    response = {
                        "success": False,
                        "message": f"Unable to delete page {page_id}. The API request completed but deletion was unsuccessful.",
                    }

                return [
                    TextContent(
                        type="text",
                        text=json.dumps(response, indent=2, ensure_ascii=False),
                    )
                ]
            except Exception as e:
                # API call failed with an exception
                logger.error(f"Error deleting Confluence page {page_id}: {str(e)}")
                return [
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {
                                "success": False,
                                "message": f"Error deleting page {page_id}",
                                "error": str(e),
                            },
                            indent=2,
                            ensure_ascii=False,
                        ),
                    )
                ]

        # Jira operations
        elif name == "jira_get_issue":
            if not ctx or not ctx.jira:
                raise ValueError("Jira is not configured.")

            issue_key = arguments.get("issue_key")
            fields = arguments.get(
                "fields",
                "summary,description,status,assignee,reporter,labels,priority,created,updated,issuetype",
            )
            expand = arguments.get("expand")
            comment_limit = arguments.get("comment_limit", 10)
            properties = arguments.get("properties")
            update_history = arguments.get("update_history", True)

            issue = ctx.jira.get_issue(
                issue_key,
                fields=fields,
                expand=expand,
                comment_limit=comment_limit,
                properties=properties,
                update_history=update_history,
            )

            result = {"content": issue.to_simplified_dict()}

            return [
                TextContent(
                    type="text", text=json.dumps(result, indent=2, ensure_ascii=False)
                )
            ]

        elif name == "jira_search":
            if not ctx or not ctx.jira:
                raise ValueError("Jira is not configured.")

            jql = arguments.get("jql")
            fields = arguments.get(
                "fields",
                "summary,description,status,assignee,reporter,labels,priority,created,updated,issuetype",
            )
            limit = min(int(arguments.get("limit", 10)), 50)
            projects_filter = arguments.get("projects_filter")
            start_at = int(arguments.get("startAt", 0))  # Get startAt

            search_result = ctx.jira.search_issues(
                jql,
                fields=fields,
                limit=limit,
                start=start_at,  # Pass start_at here
                projects_filter=projects_filter,
            )

            # Format results using the to_simplified_dict method
            issues = [issue.to_simplified_dict() for issue in search_result.issues]

            # Include metadata in the response
            response = {
                "total": search_result.total,
                "start_at": search_result.start_at,
                "max_results": search_result.max_results,
                "issues": issues,
            }

            return [
                TextContent(
                    type="text",
                    text=json.dumps(response, indent=2, ensure_ascii=False),
                )
            ]

        elif name == "jira_search_fields" and ctx and ctx.jira:
            if not ctx or not ctx.jira:
                raise ValueError("Jira is not configured.")

            keyword = arguments.get("keyword")
            limit = int(arguments.get("limit", 10))

            result = ctx.jira.search_fields(keyword, limit)

            return [
                TextContent(
                    type="text",
                    text=json.dumps(result, indent=2, ensure_ascii=False),
                )
            ]

        elif name == "jira_get_project_issues" and ctx and ctx.jira:
            if not ctx or not ctx.jira:
                raise ValueError("Jira is not configured.")

            project_key = arguments.get("project_key")
            limit = min(int(arguments.get("limit", 10)), 50)
            start_at = int(arguments.get("startAt", 0))  # Get startAt

            search_result = ctx.jira.get_project_issues(
                project_key, start=start_at, limit=limit
            )

            # Format results
            issues = [issue.to_simplified_dict() for issue in search_result.issues]

            # Include metadata in the response
            response = {
                "total": search_result.total,
                "start_at": search_result.start_at,
                "max_results": search_result.max_results,
                "issues": issues,
            }

            return [
                TextContent(
                    type="text",
                    text=json.dumps(response, indent=2, ensure_ascii=False),
                )
            ]

        elif name == "jira_get_epic_issues":
            if not ctx or not ctx.jira:
                raise ValueError("Jira is not configured.")

            epic_key = arguments.get("epic_key")
            limit = min(int(arguments.get("limit", 10)), 50)
            start_at = int(arguments.get("startAt", 0))  # Get startAt

            # Get issues linked to the epic
            search_result = ctx.jira.get_epic_issues(
                epic_key, start=start_at, limit=limit
            )

            # Format results - iterate directly over the list
            issues = [issue.to_simplified_dict() for issue in search_result]

            # Include metadata in the response
            response = {
                "total": len(search_result),
                "start_at": start_at,
                "max_results": limit,
                "issues": issues,
            }

            return [
                TextContent(
                    type="text",
                    text=json.dumps(response, indent=2, ensure_ascii=False),
                )
            ]

        elif name == "jira_get_transitions":
            if not ctx or not ctx.jira:
                raise ValueError("Jira is not configured.")

            issue_key = arguments.get("issue_key")

            # Get available transitions
            transitions = ctx.jira.get_available_transitions(issue_key)

            # Format transitions
            formatted_transitions = []
            for transition in transitions:
                formatted_transitions.append(
                    {
                        "id": transition.get("id"),
                        "name": transition.get("name"),
                        "to_status": transition.get("to", {}).get("name"),
                    }
                )

            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        formatted_transitions, indent=2, ensure_ascii=False
                    ),
                )
            ]

        elif name == "jira_get_worklog":
            if not ctx or not ctx.jira:
                raise ValueError("Jira is not configured.")

            issue_key = arguments.get("issue_key")

            # Get worklogs
            worklogs = ctx.jira.get_worklogs(issue_key)

            result = {"worklogs": worklogs}

            return [
                TextContent(
                    type="text", text=json.dumps(result, indent=2, ensure_ascii=False)
                )
            ]

        elif name == "jira_download_attachments":
            if not ctx or not ctx.jira:
                raise ValueError("Jira is not configured.")

            issue_key = arguments.get("issue_key")
            target_dir = arguments.get("target_dir")

            if not issue_key:
                raise ValueError("Missing required parameter: issue_key")
            if not target_dir:
                raise ValueError("Missing required parameter: target_dir")

            # Download the attachments
            result = ctx.jira.download_issue_attachments(
                issue_key=issue_key, target_dir=target_dir
            )

            return [
                TextContent(
                    type="text", text=json.dumps(result, indent=2, ensure_ascii=False)
                )
            ]

        elif name == "jira_get_agile_boards":
            if not ctx or not ctx.jira:
                raise ValueError("Jira is not configured.")

            board_name = arguments.get("board_name")
            project_key = arguments.get("project_key")
            board_type = arguments.get("board_type")
            start_at = int(arguments.get("startAt", 0))
            limit = min(int(arguments.get("limit", 10)), 50)

            boards = ctx.jira.get_all_agile_boards_model(
                board_name=board_name,
                project_key=project_key,
                board_type=board_type,
                start=start_at,
                limit=limit,
            )

            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        [board.to_simplified_dict() for board in boards],
                        indent=2,
                        ensure_ascii=False,
                    ),
                )
            ]

        elif name == "jira_get_board_issues":
            if not ctx or not ctx.jira:
                raise ValueError("Jira is not configured.")

            board_id = arguments.get("board_id")
            jql = arguments.get("jql")
            fields = arguments.get("fields", "*all")

            start_at = int(arguments.get("startAt", 0))
            limit = min(int(arguments.get("limit", 10)), 50)
            expand = arguments.get("expand", "version")

            search_result = ctx.jira.get_board_issues(
                board_id=board_id,
                jql=jql,
                fields=fields,
                start=start_at,
                limit=limit,
                expand=expand,
            )

            # Format results
            issues = [issue.to_simplified_dict() for issue in search_result.issues]

            # Include metadata in the response
            response = {
                "total": search_result.total,
                "start_at": search_result.start_at,
                "max_results": search_result.max_results,
                "issues": issues,
            }

            return [
                TextContent(
                    type="text",
                    text=json.dumps(response, indent=2, ensure_ascii=False),
                )
            ]

        elif name == "jira_get_sprints_from_board":
            if not ctx or not ctx.jira:
                raise ValueError("Jira is not configured.")

            board_id = arguments.get("board_id")
            state = arguments.get("state", "active")
            start_at = int(arguments.get("startAt", 0))
            limit = min(int(arguments.get("limit", 10)), 50)

            sprints = ctx.jira.get_all_sprints_from_board_model(
                board_id=board_id, state=state, start=start_at, limit=limit
            )

            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        [sprint.to_simplified_dict() for sprint in sprints],
                        indent=2,
                        ensure_ascii=False,
                    ),
                )
            ]

        elif name == "jira_create_sprint":
            if not ctx or not ctx.jira:
                raise ValueError("Jira is not configured.")

            board_id = arguments.get("board_id")
            sprint_name = arguments.get("sprint_name")
            goal = arguments.get("goal")
            start_date = arguments.get("start_date")
            end_date = arguments.get("end_date")

            sprint = ctx.jira.create_sprint(
                board_id=board_id,
                sprint_name=sprint_name,
                goal=goal,
                start_date=start_date,
                end_date=end_date,
            )

            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        sprint.to_simplified_dict(), indent=2, ensure_ascii=False
                    ),
                )
            ]

        elif name == "jira_get_sprint_issues":
            if not ctx or not ctx.jira:
                raise ValueError("Jira is not configured.")

            sprint_id = arguments.get("sprint_id")
            fields = arguments.get("fields", "*all")
            start_at = int(arguments.get("startAt", 0))
            limit = min(int(arguments.get("limit", 10)), 50)

            search_result = ctx.jira.get_sprint_issues(
                sprint_id=sprint_id,
                fields=fields,
                start=start_at,
                limit=limit,
            )

            # Format results
            issues = [issue.to_simplified_dict() for issue in search_result.issues]

            # Include metadata in the response
            response = {
                "total": search_result.total,
                "start_at": search_result.start_at,
                "max_results": search_result.max_results,
                "issues": issues,
            }

            return [
                TextContent(
                    type="text",
                    text=json.dumps(response, indent=2, ensure_ascii=False),
                )
            ]

        elif name == "jira_update_sprint" and ctx and ctx.jira:
            if not ctx or not ctx.jira:
                raise ValueError("Jira is not configured.")

            sprint_id = arguments.get("sprint_id")
            sprint_name = arguments.get("sprint_name")
            goal = arguments.get("goal")
            start_date = arguments.get("start_date")
            end_date = arguments.get("end_date")
            state = arguments.get("state")

            sprint = ctx.jira.update_sprint(
                sprint_id=sprint_id,
                sprint_name=sprint_name,
                goal=goal,
                start_date=start_date,
                end_date=end_date,
                state=state,
            )

            if sprint is None:
                # Handle the error case, e.g., return an error message
                error_payload = {
                    "error": f"Failed to update sprint {sprint_id}. Check logs for details."
                }
                return [
                    TextContent(
                        type="text",
                        text=json.dumps(error_payload, indent=2, ensure_ascii=False),
                    )
                ]

            # If sprint is not None, proceed as before
            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        sprint.to_simplified_dict(), indent=2, ensure_ascii=False
                    ),
                )
            ]

        elif name == "jira_create_issue":
            if not ctx or not ctx.jira:
                raise ValueError("Jira is not configured.")

            # Write operation - check read-only mode
            if read_only:
                return [
                    TextContent(
                        "Operation 'jira_create_issue' is not available in read-only mode."
                    )
                ]

            # Extract required arguments
            project_key = arguments.get("project_key")
            summary = arguments.get("summary")
            issue_type = arguments.get("issue_type")

            # Extract optional arguments
            description = arguments.get("description", "")
            assignee = arguments.get("assignee")
            components = arguments.get("components")

            # Parse components from comma-separated string to list
            if components and isinstance(components, str):
                # Split by comma and strip whitespace, removing empty entries
                components = [
                    comp.strip() for comp in components.split(",") if comp.strip()
                ]

            # Parse additional fields
            additional_fields = {}
            if arguments.get("additional_fields"):
                try:
                    additional_fields = json.loads(arguments.get("additional_fields"))
                except json.JSONDecodeError:
                    raise ValueError("Invalid JSON in additional_fields")

            # Create the issue
            issue = ctx.jira.create_issue(
                project_key=project_key,
                summary=summary,
                issue_type=issue_type,
                description=description,
                assignee=assignee,
                components=components,
                **additional_fields,
            )

            result = issue.to_simplified_dict()

            return [
                TextContent(
                    type="text",
                    text=f"Issue created successfully:\n{json.dumps(result, indent=2, ensure_ascii=False)}",
                )
            ]

        elif name == "jira_batch_create_issues" and ctx and ctx.jira:
            if not ctx or not ctx.jira:
                raise ValueError("Jira is not configured.")

            # Write operation - check read-only mode
            if read_only:
                return [
                    TextContent(
                        "Operation 'jira_batch_create_issues' is not available in read-only mode."
                    )
                ]

            # Extract required arguments
            issues = arguments.get("issues")
            validate_only = arguments.get("validate_only", False)

            # Parse issues from JSON string to list of dictionaries
            if issues and isinstance(issues, str):
                try:
                    issues = json.loads(issues)
                except json.JSONDecodeError:
                    raise ValueError("Invalid JSON in issues")

            # Create issues in batch
            created_issues = ctx.jira.batch_create_issues(
                issues, validate_only=validate_only
            )

            # Format the response
            result = {
                "message": "Issues created successfully",
                "issues": [issue.to_simplified_dict() for issue in created_issues],
            }

            return [
                TextContent(
                    type="text",
                    text=json.dumps(result, indent=2, ensure_ascii=False),
                )
            ]

        elif name == "jira_update_issue":
            if not ctx or not ctx.jira:
                raise ValueError("Jira is not configured.")

            # Write operation - check read-only mode
            if read_only:
                return [
                    TextContent(
                        "Operation 'jira_update_issue' is not available in read-only mode."
                    )
                ]

            # Extract arguments
            issue_key = arguments.get("issue_key")

            # Parse fields JSON
            fields = {}
            if arguments.get("fields"):
                try:
                    fields = json.loads(arguments.get("fields"))
                except json.JSONDecodeError:
                    raise ValueError("Invalid JSON in fields")

            # Parse additional fields JSON
            additional_fields = {}
            if arguments.get("additional_fields"):
                try:
                    additional_fields = json.loads(arguments.get("additional_fields"))
                except json.JSONDecodeError:
                    raise ValueError("Invalid JSON in additional_fields")

            # Handle attachments if provided
            attachments = []
            if arguments.get("attachments"):
                # Parse attachments - can be a single string or a list of strings
                if isinstance(arguments.get("attachments"), str):
                    try:
                        # Try to parse as JSON array
                        parsed_attachments = json.loads(arguments.get("attachments"))
                        if isinstance(parsed_attachments, list):
                            attachments = parsed_attachments
                        else:
                            # Single file path as a JSON string
                            attachments = [parsed_attachments]
                    except json.JSONDecodeError:
                        # Handle non-JSON string formats
                        if "," in arguments.get("attachments"):
                            # Split by comma and strip whitespace (supporting comma-separated list format)
                            attachments = [
                                path.strip()
                                for path in arguments.get("attachments").split(",")
                            ]
                        else:
                            # Plain string - single file path
                            attachments = [arguments.get("attachments")]
                elif isinstance(arguments.get("attachments"), list):
                    # Already a list
                    attachments = arguments.get("attachments")

                # Validate all paths exist
                for path in attachments[:]:
                    if not os.path.exists(path):
                        logger.warning(f"Attachment file not found: {path}")
                        attachments.remove(path)

            try:
                # Add attachments to additional_fields if any valid paths were found
                if attachments:
                    additional_fields["attachments"] = attachments

                # Update the issue - directly pass fields to JiraFetcher.update_issue
                # instead of using fields as a parameter name
                issue = ctx.jira.update_issue(
                    issue_key=issue_key, **fields, **additional_fields
                )

                result = issue.to_simplified_dict()

                # Include attachment results if available
                if (
                    hasattr(issue, "custom_fields")
                    and "attachment_results" in issue.custom_fields
                ):
                    result["attachment_results"] = issue.custom_fields[
                        "attachment_results"
                    ]

                return [
                    TextContent(
                        type="text",
                        text=f"Issue updated successfully:\n{json.dumps(result, indent=2, ensure_ascii=False)}",
                    )
                ]
            except Exception as e:
                return [
                    TextContent(
                        type="text",
                        text=f"Error updating issue {issue_key}: {str(e)}",
                    )
                ]

        elif name == "jira_delete_issue":
            if not ctx or not ctx.jira:
                raise ValueError("Jira is not configured.")

            # Write operation - check read-only mode
            if read_only:
                return [
                    TextContent(
                        "Operation 'jira_delete_issue' is not available in read-only mode."
                    )
                ]

            issue_key = arguments.get("issue_key")

            # Delete the issue
            deleted = ctx.jira.delete_issue(issue_key)

            result = {"message": f"Issue {issue_key} has been deleted successfully."}

            return [
                TextContent(
                    type="text", text=json.dumps(result, indent=2, ensure_ascii=False)
                )
            ]

        elif name == "jira_add_comment":
            if not ctx or not ctx.jira:
                raise ValueError("Jira is not configured.")

            # Write operation - check read-only mode
            if read_only:
                return [
                    TextContent(
                        "Operation 'jira_add_comment' is not available in read-only mode."
                    )
                ]

            issue_key = arguments.get("issue_key")
            comment = arguments.get("comment")

            # Add the comment
            result = ctx.jira.add_comment(issue_key, comment)

            return [
                TextContent(
                    type="text", text=json.dumps(result, indent=2, ensure_ascii=False)
                )
            ]

        elif name == "jira_add_worklog":
            if not ctx or not ctx.jira:
                raise ValueError("Jira is not configured.")

            # Write operation - check read-only mode
            if read_only:
                return [
                    TextContent(
                        "Operation 'jira_add_worklog' is not available in read-only mode."
                    )
                ]

            # Extract arguments
            issue_key = arguments.get("issue_key")
            time_spent = arguments.get("time_spent")
            comment = arguments.get("comment")
            started = arguments.get("started")

            # Add the worklog
            worklog = ctx.jira.add_worklog(
                issue_key=issue_key,
                time_spent=time_spent,
                comment=comment,
                started=started,
            )

            result = {"message": "Worklog added successfully", "worklog": worklog}

            return [
                TextContent(
                    type="text",
                    text=json.dumps(result, indent=2, ensure_ascii=False),
                )
            ]

        elif name == "jira_link_to_epic":
            if not ctx or not ctx.jira:
                raise ValueError("Jira is not configured.")

            # Write operation - check read-only mode
            if read_only:
                return [
                    TextContent(
                        "Operation 'jira_link_to_epic' is not available in read-only mode."
                    )
                ]

            issue_key = arguments.get("issue_key")
            epic_key = arguments.get("epic_key")

            # Link the issue to the epic
            issue = ctx.jira.link_issue_to_epic(issue_key, epic_key)

            result = {
                "message": f"Issue {issue_key} has been linked to epic {epic_key}.",
                "issue": issue.to_simplified_dict(),
            }

            return [
                TextContent(
                    type="text", text=json.dumps(result, indent=2, ensure_ascii=False)
                )
            ]

        elif name == "jira_transition_issue":
            if not ctx or not ctx.jira:
                raise ValueError("Jira is not configured.")

            # Write operation - check read-only mode
            if read_only:
                return [
                    TextContent(
                        "Operation 'jira_transition_issue' is not available in read-only mode."
                    )
                ]

            # Extract arguments
            issue_key = arguments.get("issue_key")
            transition_id = arguments.get("transition_id")
            comment = arguments.get("comment")

            # Validate required parameters
            if not issue_key:
                raise ValueError("issue_key is required")
            if not transition_id:
                raise ValueError("transition_id is required")

            # Convert transition_id to integer if it's a numeric string
            # This ensures compatibility with the Jira API which expects integers
            if isinstance(transition_id, str) and transition_id.isdigit():
                transition_id = int(transition_id)
                logger.debug(
                    f"Converted string transition_id to integer: {transition_id}"
                )

            # Parse fields JSON
            fields = {}
            if arguments.get("fields"):
                try:
                    fields = json.loads(arguments.get("fields"))
                except json.JSONDecodeError:
                    raise ValueError("Invalid JSON in fields")

            try:
                # Transition the issue
                issue = ctx.jira.transition_issue(
                    issue_key=issue_key,
                    transition_id=transition_id,
                    fields=fields,
                    comment=comment,
                )

                result = {
                    "message": f"Issue {issue_key} transitioned successfully",
                    "issue": issue.to_simplified_dict() if issue else None,
                }

                return [
                    TextContent(
                        type="text",
                        text=json.dumps(result, indent=2, ensure_ascii=False),
                    )
                ]
            except Exception as e:
                # Provide a clear error message, especially for transition ID type issues
                error_msg = str(e)
                if "'transition' identifier must be an integer" in error_msg:
                    error_msg = (
                        f"Error transitioning issue {issue_key}: The Jira API requires transition IDs to be integers. "
                        f"Received transition ID '{transition_id}' of type {type(transition_id).__name__}. "
                        f"Please use the numeric ID value from jira_get_transitions."
                    )
                else:
                    error_msg = f"Error transitioning issue {issue_key} with transition ID {transition_id}: {error_msg}"

                logger.error(error_msg)
                return [
                    TextContent(
                        type="text",
                        text=error_msg,
                    )
                ]

        elif name == "jira_create_issue_link":
            if not ctx or not ctx.jira:
                raise ValueError("Jira is not configured.")

            # Write operation - check read-only mode
            if read_only:
                return [
                    TextContent(
                        "Operation 'jira_create_issue_link' is not available in read-only mode."
                    )
                ]

            # Extract arguments
            link_type = arguments.get("link_type")
            inward_issue_key = arguments.get("inward_issue_key")
            outward_issue_key = arguments.get("outward_issue_key")
            comment_text = arguments.get("comment")
            comment_visibility = arguments.get("comment_visibility")

            # Validate required parameters
            if not link_type:
                raise ValueError("link_type is required")
            if not inward_issue_key:
                raise ValueError("inward_issue_key is required")
            if not outward_issue_key:
                raise ValueError("outward_issue_key is required")

            # Prepare the data structure for creating the issue link
            link_data = {
                "type": {"name": link_type},
                "inwardIssue": {"key": inward_issue_key},
                "outwardIssue": {"key": outward_issue_key},
            }

            # Add comment if provided
            if comment_text:
                comment_data = {"body": comment_text}

                # Add visibility if provided
                if comment_visibility and isinstance(comment_visibility, dict):
                    visibility_type = comment_visibility.get("type")
                    visibility_value = comment_visibility.get("value")

                    if visibility_type and visibility_value:
                        comment_data["visibility"] = {
                            "type": visibility_type,
                            "value": visibility_value,
                        }

                link_data["comment"] = comment_data

            try:
                # Create the issue link
                result = ctx.jira.create_issue_link(link_data)

                return [
                    TextContent(
                        type="text",
                        text=json.dumps(result, indent=2, ensure_ascii=False),
                    )
                ]
            except Exception as e:
                error_msg = f"Error creating issue link: {str(e)}"
                logger.error(error_msg)
                return [
                    TextContent(
                        type="text",
                        text=error_msg,
                    )
                ]

        elif name == "jira_remove_issue_link":
            if not ctx or not ctx.jira:
                raise ValueError("Jira is not configured.")

            # Write operation - check read-only mode
            if read_only:
                return [
                    TextContent(
                        "Operation 'jira_remove_issue_link' is not available in read-only mode."
                    )
                ]

            # Extract arguments
            link_id = arguments.get("link_id")

            # Validate required parameters
            if not link_id:
                raise ValueError("link_id is required")

            try:
                # Remove the issue link
                result = ctx.jira.remove_issue_link(link_id)

                return [
                    TextContent(
                        type="text",
                        text=json.dumps(result, indent=2, ensure_ascii=False),
                    )
                ]
            except Exception as e:
                error_msg = f"Error removing issue link: {str(e)}"
                logger.error(error_msg)
                return [
                    TextContent(
                        type="text",
                        text=error_msg,
                    )
                ]

        elif name == "jira_get_link_types":
            if not ctx or not ctx.jira:
                raise ValueError("Jira is not configured.")

            try:
                # Get all issue link types
                link_types = ctx.jira.get_issue_link_types()

                # Format the response
                formatted_link_types = [
                    link_type.to_simplified_dict() for link_type in link_types
                ]

                return [
                    TextContent(
                        type="text",
                        text=json.dumps(
                            formatted_link_types, indent=2, ensure_ascii=False
                        ),
                    )
                ]
            except Exception as e:
                error_msg = f"Error getting issue link types: {str(e)}"
                logger.error(error_msg)
                return [
                    TextContent(
                        type="text",
                        text=error_msg,
                    )
                ]

        raise ValueError(f"Unknown tool: {name}")

    except Exception as e:
        logger.error(f"Tool execution error: {str(e)}")
        return [TextContent(type="text", text=f"Error: {str(e)}")]


async def run_server(transport: str = "stdio", port: int = 8000) -> None:
    """Run the MCP Atlassian server with the specified transport."""
    if transport == "sse":
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.routing import Mount, Route

        sse = SseServerTransport("/messages/")

        async def handle_sse(request: Request) -> None:
            async with sse.connect_sse(
                request.scope, request.receive, request._send
            ) as streams:
                await app.run(
                    streams[0], streams[1], app.create_initialization_options()
                )

        starlette_app = Starlette(
            debug=True,
            routes=[
                Route("/sse", endpoint=handle_sse),
                Mount("/messages/", app=sse.handle_post_message),
            ],
        )

        import uvicorn

        # Set up uvicorn config
        config = uvicorn.Config(starlette_app, host="0.0.0.0", port=port)  # noqa: S104
        server = uvicorn.Server(config)
        # Use server.serve() instead of run() to stay in the same event loop
        await server.serve()
    else:
        from mcp.server.stdio import stdio_server

        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream, write_stream, app.create_initialization_options()
            )
