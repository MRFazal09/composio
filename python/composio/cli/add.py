"""
Add new integration.

Usage:
    composio add [options]
"""

import typing as t
import webbrowser

import click
from beaupy.spinners import DOTS, Spinner

from composio.cli.context import Context, login_required, pass_context
from composio.cli.utils.decorators import pass_entity_id
from composio.cli.utils.helpfulcmd import HelpfulCmd
from composio.client import Composio, Entity
from composio.client.collections import (
    AppAuthScheme,
    AppModel,
    AuthSchemeField,
    IntegrationModel,
)
from composio.client.exceptions import ComposioClientError
from composio.constants import DEFAULT_ENTITY_ID
from composio.exceptions import ComposioSDKError
from composio.utils.url import get_web_url


class AddIntegrationExamples(HelpfulCmd):
    examples = [
        click.style("composio add <app_name>", fg="green")
        + click.style("                      # Add a new integration\n", fg="black"),
        click.style("composio add <app_name> --no-browser", fg="green")
        + click.style(
            "         # Add a new integration without opening the browser\n", fg="black"
        ),
        click.style("composio add <app_name> -i <integration_id>", fg="green")
        + click.style(
            "  # Add a new integration using an existing integration ID\n", fg="black"
        ),
    ]


@click.command(name="add", cls=AddIntegrationExamples)
@click.help_option("--help", "-h", "-help")
@click.argument("name", type=str)
@click.option(
    "--no-browser",
    is_flag=True,
    default=False,
    help="Don't open browser for verifying connection",
)
@click.option(
    "-i",
    "--integration-id",
    type=str,
    help="Specify intgration ID to use existing integration",
)
@click.option(
    "-a",
    "--auth-mode",
    type=str,
    help="Specify auth mode for given app.",
)
@click.option(
    "-s",
    "--scope",
    "scopes",
    type=str,
    help="Specify scopes for the connection.",
    multiple=True,
)
@login_required
@pass_entity_id
@pass_context
def _add(
    context: Context,
    name: str,
    scopes: t.Tuple[str, ...],
    entity_id: str,
    integration_id: t.Optional[str],
    no_browser: bool = False,
    auth_mode: t.Optional[str] = None,
) -> None:
    """Add a new integration."""
    try:
        add_integration(
            name=name.lower().strip(),
            context=context,
            entity_id=entity_id,
            integration_id=integration_id,
            no_browser=no_browser,
            auth_mode=auth_mode,
            scopes=scopes,
        )
    except ComposioSDKError as e:
        raise click.ClickException(
            message=e.message,
        ) from e


def _replace_connection() -> bool:
    """Prompt user to check if they want to replace the connection or not."""
    return (
        click.prompt(
            "> Do you want to replace the existing connection?",
            type=click.Choice(
                choices=("y", "n"),
                case_sensitive=False,
            ),
        )
        == "y"
    )


def _collect_input_fields(fields: t.List[AuthSchemeField]) -> t.Dict:
    """Collect"""
    inputs = {}
    for _field in fields:
        field = _field.model_dump()
        if field.get("expected_from_customer", True):
            if field.get("required", False):
                value = input(
                    f"> Enter {field.get('displayName', field.get('name'))}: "
                )
                if not value:
                    raise click.ClickException(
                        f"{field.get('displayName', field.get('name'))} is required"
                    )
            else:
                value = input(
                    f"Enter {field.get('displayName', field.get('name'))} (Optional):"
                ) or t.cast(
                    str,
                    field.get("default"),
                )
            inputs[field.get("name")] = value
    return inputs


def _load_integration(
    context: Context,
    integration_id: t.Optional[str] = None,
) -> t.Optional[IntegrationModel]:
    """Load integration model."""
    if integration_id is None:
        return None

    for integration in context.client.integrations.get():
        if integration.id == integration_id:
            return integration

    raise click.ClickException(f"No integration found with ID: `{integration_id}`")


def add_integration(
    name: str,
    context: Context,
    entity_id: str = DEFAULT_ENTITY_ID,
    integration_id: t.Optional[str] = None,
    no_browser: bool = False,
    auth_mode: t.Optional[str] = None,
    scopes: t.Optional[t.Tuple[str, ...]] = None,
) -> None:
    """
    Add integration.

    :param name: App name.
    :param context: CLI runtime context.
    :param entity_id: Entity ID to use for creating integration.
    :param no_browser: Don't open browser.
    :param auth_mode: Preferred auth mode.
    :param scopes: List of scopes for the connected account.
    """
    entity = context.client.get_entity(id=entity_id)
    integration = _load_integration(
        context=context,
        integration_id=integration_id,
    )
    try:
        existing_connection = entity.get_connection(app=name)
    except ComposioClientError:
        existing_connection = None

    if existing_connection is not None:
        context.console.print(
            f"[yellow]Warning: An existing connection for {name} was found.[/yellow]\n"
        )
        if not _replace_connection():
            context.console.print(
                "\n[green]Existing connection retained. No new connection added.[/green]\n"
            )
            return None

    context.console.print(
        f"\n[green]> Adding integration: {name.capitalize()}...[/green]\n"
    )
    app = t.cast(AppModel, context.client.apps.get(name=name))
    if app.no_auth:
        raise click.ClickException(f"{app.name} does not require authentication")

    auth_schemes = app.auth_schemes or []
    if len(auth_schemes) == 0:
        context.console.print(f"{app.name} does not need authentication")
        return None

    auth_modes = {auth_scheme.auth_mode: auth_scheme for auth_scheme in auth_schemes}
    if auth_mode is not None and auth_mode not in auth_modes:
        raise click.ClickException(
            f"Invalid value for `auth_mode`, select from `{set(auth_modes)}`"
        )

    if auth_mode is not None:
        auth_scheme = auth_modes[auth_mode]
    elif len(auth_modes) == 1:
        ((auth_mode, auth_scheme),) = auth_modes.items()
    else:
        auth_mode = t.cast(
            str,
            click.prompt(
                "Select auth mode: ",
                type=click.Choice(choices=list(auth_modes)),
            ),
        )
        auth_scheme = auth_modes[auth_mode]

    if auth_mode.lower() in ("basic", "api_key"):
        return _handle_basic_auth(
            entity=entity,
            client=context.client,
            app_name=name,
            auth_mode=auth_mode,
            auth_scheme=auth_scheme,
            scopes=scopes,
        )
    return _handle_oauth(
        entity=entity,
        client=context.client,
        app_name=name,
        no_browser=no_browser,
        integration=integration,
    )


def _get_auth_config(
    scopes: t.Optional[t.Tuple[str, ...]] = None
) -> t.Optional[t.Dict]:
    """Get auth config."""
    scopes = scopes or ()
    if len(scopes) == 0:
        return None

    return {
        "client_id": "*************",
        "client_secret": "*************",
        "scopes": ",".join(scopes),
    }


def _handle_oauth(
    entity: Entity,
    client: Composio,
    app_name: str,
    no_browser: bool = False,
    integration: t.Optional[IntegrationModel] = None,
) -> None:
    """Handle basic auth."""
    connection = entity.initiate_connection(
        app_name=app_name.lower(),
        redirect_url=get_web_url(path="redirect"),
        integration=integration,
    )
    if not no_browser:
        webbrowser.open(
            url=str(connection.redirectUrl),
        )
    click.echo(
        f"Please authenticate {app_name} in the browser and come back here. "
        f"URL: {connection.redirectUrl}"
    )
    spinner = Spinner(
        DOTS,
        f"⚠ Waiting for {app_name} authentication...",
    )
    spinner.start()
    connection.wait_until_active(client=client)
    spinner.stop()
    click.echo(f"✔ {app_name} added successfully!")


def _handle_basic_auth(
    entity: Entity,
    client: Composio,
    app_name: str,
    auth_mode: str,
    auth_scheme: AppAuthScheme,
    integration: t.Optional[IntegrationModel] = None,
    scopes: t.Optional[t.Tuple[str, ...]] = None,
) -> None:
    """Handle basic auth."""
    entity.initiate_connection(
        app_name=app_name.lower(),
        auth_mode=auth_mode,
        auth_config=_get_auth_config(scopes=scopes),
        integration=integration,
    ).save_user_access_data(
        client=client,
        field_inputs=_collect_input_fields(
            fields=auth_scheme.fields,
        ),
        entity_id=entity.id,
    )
    click.echo(f"✔ {app_name} added successfully!")
