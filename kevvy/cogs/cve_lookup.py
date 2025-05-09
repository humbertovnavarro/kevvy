import discord
from discord import app_commands
from discord.ext import commands
import re
import logging
from typing import Optional, TYPE_CHECKING, Literal, Dict, Any
import datetime
import sqlite3
import asyncio

# Use absolute imports for type checking
if TYPE_CHECKING:
    from kevvy.bot import SecurityBot
    from kevvy.nvd_client import NVDClient
    from kevvy.db_utils import KEVConfigDB  # Import SeverityLevel

logger = logging.getLogger(__name__)

# Regex for validating CVE ID format in commands
CVE_VALIDATE_REGEX = re.compile(r"^CVE-\d{4}-\d{4,}$", re.IGNORECASE)
# Regex for finding potential CVE IDs anywhere in message text
CVE_SCAN_REGEX = re.compile(r"CVE-\d{4}-\d{4,}", re.IGNORECASE)
MAX_CVES_PER_MESSAGE = 5  # Limit embeds per message

# Define SeverityLevel choices for commands
SeverityLevelChoices = Literal["critical", "high", "medium", "low", "all"]


# Define the command group
class CVELookupCog(commands.Cog):
    """Cog for handling CVE lookup and configuration commands."""

    def __init__(self, bot: "SecurityBot"):
        self.bot = bot
        self.nvd_client: Optional["NVDClient"] = self.bot.nvd_client
        self.db: Optional["KEVConfigDB"] = self.bot.db  # Add DB reference

    # Define the base /cve group
    cve_group = app_commands.Group(
        name="cve", description="Commands related to CVE information."
    )

    # --- NEW Top-Level /verbose Group ---
    verbose_group = app_commands.Group(
        name="verbose",
        description="Configure global and per-channel verbosity of CVE alerts.",
        guild_only=True,
    )
    # Note: Removed parent=cve_group to make it top-level

    def create_cve_embed(self, cve_data: dict, verbose: bool = True) -> discord.Embed:
        """Creates a Discord embed from fetched CVE data, adjusting detail based on verbosity."""
        embed = discord.Embed(
            title=f"{cve_data.get('id', 'Unknown CVE')}",
            url=cve_data.get("link"),
            color=discord.Color.purple(),  # Or choose another color
        )

        # Always show score if available
        if cve_data.get("cvss"):
            cvss_info = f"**Score:** {cve_data['cvss']}"
            if verbose and cve_data.get("cvss_version"):  # Only show version in verbose
                cvss_info += f" ({cve_data['cvss_version']})"
            embed.add_field(
                name="CVSS Score", value=cvss_info, inline=not verbose
            )  # Inline score in standard mode

        if verbose:
            # Fields only for verbose mode
            if cve_data.get("description"):
                embed.description = cve_data.get("description")
            else:
                embed.description = "No description provided."

            if cve_data.get("cvss_vector"):
                embed.add_field(
                    name="CVSS Vector",
                    value=f"`{cve_data['cvss_vector']}`",
                    inline=False,
                )

            if cve_data.get("cwe_ids"):
                cwe_text = ", ".join(cve_data["cwe_ids"])
                embed.add_field(name="Weakness (CWE)", value=cwe_text, inline=True)

            if cve_data.get("published"):
                embed.add_field(
                    name="Published", value=cve_data["published"], inline=True
                )
            if cve_data.get("modified"):
                embed.add_field(
                    name="Last Modified", value=cve_data["modified"], inline=True
                )

            if references := cve_data.get("references", []):
                ref_limit = 5
                ref_text = ""
                for i, ref in enumerate(references[:ref_limit]):
                    ref_text += f"- [{ref.get('source', 'Link')}]({ref.get('url')})"
                    if ref.get("tags"):
                        ref_text += f" ({', '.join(ref['tags'])})"
                    ref_text += "\n"
                if len(references) > ref_limit:
                    ref_text += f"*...and {len(references) - ref_limit} more.*"
                embed.add_field(name="References", value=ref_text.strip(), inline=False)
        else:
            # Standard mode: Keep it concise
            desc_snippet = cve_data.get("description", "No description provided.")[
                :100
            ]  # Limit standard description
            if len(cve_data.get("description", "")) > 100:
                desc_snippet += "..."
            embed.description = desc_snippet
            # Removed footer setting from here

        # Footer and timestamp should apply regardless of verbosity
        embed.set_footer(text=f"Source: {cve_data.get('source', 'N/A')}")
        embed.timestamp = discord.utils.utcnow()

        return embed

    # Placeholder for KEV embed creation
    def create_kev_embed(
        self, cve_id: str, kev_entry: Dict[str, Any], verbose: bool = True
    ) -> discord.Embed:
        """Creates a Discord embed for a KEV entry notification, adjusting detail based on verbosity."""

        embed = discord.Embed(
            title=f"🚨 KEV Alert: {cve_id} is Known Exploited!",  # Slightly different title
            color=discord.Color.red(),
        )
        nvd_link = f"https://nvd.nist.gov/vuln/detail/{cve_id}"

        if verbose:
            # Verbose KEV Embed
            embed.description = (
                f"**Vulnerability Name:** {kev_entry.get('vulnerabilityName', 'N/A')}\n"
                f"**Action Required:** {kev_entry.get('shortDescription', 'See CISA advisory.')}\n"
                f"**Due Date:** {kev_entry.get('dueDate', 'N/A')}"
            )
            embed.add_field(
                name="Vendor", value=kev_entry.get("vendorProject", "N/A"), inline=True
            )
            embed.add_field(
                name="Product", value=kev_entry.get("product", "N/A"), inline=True
            )
            embed.add_field(
                name="Known Ransomware Use",
                value=kev_entry.get("knownRansomwareCampaignUse", "N/A"),
                inline=True,
            )
            embed.add_field(
                name="Links",
                value=f"[NVD]({nvd_link}) | [CISA KEV](https://www.cisa.gov/known-exploited-vulnerabilities-catalog)",
                inline=False,
            )
            embed.timestamp = discord.utils.utcnow()
        else:
            # Standard KEV Embed (Concise)
            embed.description = (
                f"{kev_entry.get('vulnerabilityName', 'N/A')} - Added to KEV Catalog."
            )
            embed.add_field(
                name="Action Due", value=kev_entry.get("dueDate", "N/A"), inline=True
            )
            embed.add_field(
                name="Links",
                value=f"[NVD]({nvd_link}) | [CISA KEV](https://www.cisa.gov/known-exploited-vulnerabilities-catalog)",
                inline=True,
            )
            embed.set_footer(
                text="Source: CISA KEV | Use /verbose enable_global for more details."
            )
            # No timestamp for standard

        return embed

    @cve_group.command(
        name="lookup", description="Look up details for a specific CVE ID from NVD."
    )
    @app_commands.describe(cve_id="The CVE ID (e.g., CVE-2023-12345)")
    async def lookup_subcommand(self, interaction: discord.Interaction, cve_id: str):
        """Handles the /cve lookup subcommand."""
        await interaction.response.defer()

        if not CVE_VALIDATE_REGEX.match(cve_id):
            await interaction.followup.send(
                "❌ Invalid CVE ID format. Please use `CVE-YYYY-NNNNN...` (e.g., CVE-2023-12345).",
                ephemeral=True,
            )
            return

        # Use helper method for lookup logic
        await self._perform_cve_lookup(interaction, cve_id.upper())

    # Helper for lookup logic shared by command and potentially message scanning
    async def _perform_cve_lookup(
        self, interaction: discord.Interaction, cve_id_upper: str
    ):
        if not self.nvd_client:
            logger.error("NVDClient is not available for CVE lookup.")
            await interaction.followup.send(
                "❌ The NVD client is not configured or failed to initialize. Cannot perform lookup.",
                ephemeral=True,
            )
            return

        try:
            logger.info(
                f"User {interaction.user} ({interaction.user.id}) looking up CVE: {cve_id_upper} via /cve lookup"
            )

            # --- Add Stat Increment ---
            async with self.bot.stats_lock:
                self.bot.stats_cve_lookups += 1
            # --- End Stat Increment ---

            cve_details = await self.nvd_client.get_cve_details(cve_id_upper)

            if cve_details:
                # --- Add Stat Increment ---
                # Using nvd_fallback_success counter here for simplicity as this command only uses NVD
                async with self.bot.stats_lock:
                    self.bot.stats_nvd_fallback_success += 1
                # --- End Stat Increment ---

                embed = self.create_cve_embed(cve_details)
                await interaction.followup.send(embed=embed)
            else:
                await interaction.followup.send(
                    f"🤷 Could not find details for `{cve_id_upper}` in NVD, or an error occurred during fetch."
                )

        except Exception as e:
            logger.error(
                f"Unexpected error during CVE lookup for {cve_id_upper}: {e}",
                exc_info=True,
            )

            # --- Add Stat Increment ---
            async with self.bot.stats_lock:
                self.bot.stats_api_errors_nvd += 1
            # --- End Stat Increment ---

            await interaction.followup.send(
                f"❌ An unexpected error occurred while looking up `{cve_id_upper}`. Please try again later.",
                ephemeral=True,
            )

    # --- NEW /cve latest command (incorporating Future Enhancements) ---
    @cve_group.command(
        name="latest", description="Display the most recent CVEs with filters."
    )
    @app_commands.describe(
        count="Number of CVEs to show (default 5, max 10)",
        days="Look back N days (default 7, max 30)",
        vendor="Filter by vendor name (case-insensitive match)",
        product="Filter by product name (case-insensitive match)",
        in_kev="Filter for CVEs also in the KEV catalog (True/False)",
    )
    @app_commands.choices(
        severity=[
            app_commands.Choice(name="Critical", value="critical"),
            app_commands.Choice(name="High", value="high"),
            app_commands.Choice(name="Medium", value="medium"),
            app_commands.Choice(name="Low", value="low"),
        ]
    )
    async def cve_latest_command(
        self,
        interaction: discord.Interaction,
        count: app_commands.Range[int, 1, 10] = 5,
        days: app_commands.Range[int, 1, 30] = 7,
        severity: Optional[SeverityLevelChoices] = None,
        vendor: Optional[str] = None,
        product: Optional[str] = None,
        in_kev: Optional[bool] = None,
    ):
        """Displays the most recent CVEs with optional filters."""
        # Check necessary clients
        if not self.nvd_client:
            await interaction.response.send_message(
                "❌ NVD client is not available. Cannot fetch CVE data.", ephemeral=True
            )
            return
        # KEV client needed if in_kev filter is used
        kev_client_needed = in_kev is not None
        if kev_client_needed and not self.bot.cisa_kev_client:
            await interaction.response.send_message(
                "❌ KEV client is not available for the 'in_kev' filter.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            # Fetch recent CVEs from NVD (or potentially another source in future)
            # NVDClient needs a method like get_recent_cves - assuming it exists for now
            # Placeholder: Assume nvd_client.get_recent_cves returns a list of CVE dicts like get_cve_details
            logger.info(f"Fetching recent CVEs for /cve latest (days={days})...")
            recent_cves = await self.nvd_client.get_recent_cves(days=days)
            if recent_cves is None:  # Check for None explicitly
                await interaction.followup.send(
                    "❌ Failed to fetch recent CVE data from NVD.", ephemeral=True
                )
                return
            if not recent_cves:
                await interaction.followup.send(
                    f"⚪ No CVEs found published in the last {days} days.",
                    ephemeral=True,
                )
                return

            logger.info(f"Fetched {len(recent_cves)} CVEs. Applying filters...")

            # --- Apply Filters ---
            filtered_cves = recent_cves

            # 1. Severity Filter (requires CVSS score in data)
            if severity:
                min_score = {
                    "critical": 9.0,
                    "high": 7.0,
                    "medium": 4.0,
                    "low": 0.1,
                }.get(severity, 0)
                filtered_cves = [
                    cve for cve in filtered_cves if cve.get("cvss", 0) >= min_score
                ]

            # 2. Vendor Filter (requires vendor/product info - NVD API provides CPEs)
            # This is a simplification - real CPE matching is complex
            if vendor:
                # Placeholder logic: Check if vendor name appears in description or CPEs if available
                # filtered_cves = [cve for cve in filtered_cves if vendor.lower() in cve.get('description', '').lower()]
                logger.warning(
                    "Vendor filtering for /cve latest is basic, checks description only currently."
                )
                filtered_cves = [
                    cve
                    for cve in filtered_cves
                    if vendor.lower() in cve.get("description", "").lower()
                ]

            # 3. Product Filter (similar complexity to vendor)
            if product:
                # Placeholder logic:
                logger.warning(
                    "Product filtering for /cve latest is basic, checks description only currently."
                )
                filtered_cves = [
                    cve
                    for cve in filtered_cves
                    if product.lower() in cve.get("description", "").lower()
                ]

            # 4. In KEV Filter
            if in_kev is not None and self.bot.cisa_kev_client:
                kev_catalog_cves = {
                    kev.get("cveID")
                    for kev in (
                        await self.bot.cisa_kev_client.get_full_kev_catalog() or []
                    )
                }
                if in_kev:
                    filtered_cves = [
                        cve
                        for cve in filtered_cves
                        if cve.get("id") in kev_catalog_cves
                    ]
                else:
                    filtered_cves = [
                        cve
                        for cve in filtered_cves
                        if cve.get("id") not in kev_catalog_cves
                    ]

            # --- Sorting & Limiting ---
            # Sort by published date (most recent first) - assuming 'published' field exists and is sortable
            try:
                # Ensure the key function handles potential None or invalid dates gracefully
                def get_sort_key(cve_entry):
                    pub_date_str = cve_entry.get("published", "1970-01-01T00:00:00.000")
                    try:
                        # Attempt to parse, default to epoch on failure
                        return datetime.datetime.fromisoformat(
                            pub_date_str.replace("Z", "+00:00")
                        )
                    except (ValueError, TypeError):
                        return datetime.datetime(
                            1970, 1, 1, tzinfo=datetime.timezone.utc
                        )

                filtered_cves.sort(key=get_sort_key, reverse=True)
                logger.debug("Sorted CVEs by published date.")
            except Exception as sort_e:
                # Log the error but continue with unsorted list
                logger.warning(f"Could not sort CVEs by published date: {sort_e}")
                # No need for pass, just logging the warning is enough

            results_to_show = filtered_cves[:count]

            # --- Create Embed ---
            if not results_to_show:
                await interaction.followup.send(
                    f"⚪ No CVEs found matching your criteria in the last {days} days.",
                    ephemeral=True,
                )
                return

            embed_title = f"Recent CVEs (Last {days} days)"
            # Add filter indicators to title?
            filter_details = []
            if severity:
                filter_details.append(f"severity>={severity}")
            if vendor:
                filter_details.append(f"vendor={vendor}")
            if product:
                filter_details.append(f"product={product}")
            if in_kev is not None:
                filter_details.append(f"in_kev={in_kev}")
            if filter_details:
                embed_title += f" | Filters: {', '.join(filter_details)}"

            embed = discord.Embed(
                title=embed_title,
                color=discord.Color.blue(),  # Different color for CVE latest
            )

            description_lines = []
            for i, cve in enumerate(results_to_show, 1):
                cve_id = cve.get("id", "N/A")
                title = cve.get("description", "No description")[
                    :80
                ]  # Shorter description for list
                cvss_score = cve.get("cvss", "N/A")
                published = cve.get("published", "N/A")
                nvd_link = cve.get("link", "#")

                # Ensure all parts are strings before joining
                id_part = f"**{i}. [{cve_id}]({nvd_link})** - Score: {cvss_score}"
                title_part = f"   *{title}...*"
                published_part = f"   Published: {published}"
                line = f"{id_part}\n{title_part}\n{published_part}"
                description_lines.append(line)

                if i >= 5 and len(results_to_show) > 5:
                    description_lines.append(
                        f"\n*... and {len(results_to_show) - 5} more*"
                    )
                    break
            embed.description = "\n\n".join(
                description_lines
            )  # Add extra newline for spacing
            embed.set_footer(
                text=f"Found {len(filtered_cves)} CVEs matching criteria. Showing top {len(results_to_show)}."
            )
            embed.timestamp = discord.utils.utcnow()

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error handling /cve latest command: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ An unexpected error occurred while fetching latest CVEs.",
                ephemeral=True,
            )

    # --- Refactored /cve channels Group (Based on PRD Section 2.2.2) ---
    channels_group = app_commands.Group(
        name="channels",
        description="Configure channels for automatic CVE monitoring.",
        parent=cve_group,
        guild_only=True,
    )

    async def _ensure_guild_config(self, guild_id: int) -> bool:
        """Ensure a global guild config exists, creating default if needed. Returns True if successful."""
        if not self.db:
            return False
        try:
            guild_config = self.db.get_cve_guild_config(guild_id)
            if not guild_config:
                self.db.set_cve_guild_config(
                    guild_id, enabled=True, verbose_mode=False, severity_threshold="all"
                )
                logger.info(f"Initialized default CVE guild config for {guild_id}.")
            return True
        except Exception as e:
            logger.error(
                f"Error ensuring guild config for {guild_id}: {e}", exc_info=True
            )
            return False

    @channels_group.command(
        name="add",
        description="Add/Enable automatic CVE monitoring for a specific channel.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(channel="The channel to add for CVE monitoring.")
    async def channels_add_command(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        """Adds a channel to the CVE monitoring list."""
        if not self.db or not interaction.guild_id:
            await interaction.response.send_message(
                "❌ Bot error: Cannot access database or Guild ID.", ephemeral=True
            )
            return

        guild_id = interaction.guild_id
        # Ensure global config exists first
        if not await self._ensure_guild_config(guild_id):
            await interaction.response.send_message(
                "❌ Bot error: Could not ensure guild configuration.", ephemeral=True
            )
            return

        try:
            # Ensure global monitoring is enabled for the guild when adding a channel
            guild_config = self.db.get_cve_guild_config(guild_id)
            if guild_config and not guild_config.get("cve_monitoring_enabled", False):
                self.db.update_cve_guild_enabled(
                    guild_id, True
                )  # Uses cve_monitoring_enabled field now
                logger.info(
                    f"Globally enabled CVE monitoring for guild {guild_id} as channel was added."
                )

            # Add or update the specific channel configuration, ensuring it's enabled
            self.db.add_or_update_cve_channel(
                guild_id=guild_id,
                channel_id=channel.id,
                enabled=True,
                verbose_mode=None,  # Don't change verbosity here
                severity_threshold=None,  # Don't change threshold here
                alert_format=None,  # Don't change format here
            )
            logger.info(
                f"User {interaction.user} added/enabled channel {channel.id} for CVE monitoring in guild {guild_id}."
            )
            await interaction.response.send_message(
                f"✅ Automatic CVE monitoring enabled for channel {channel.mention}.",
                ephemeral=True,
            )
        except Exception as e:
            logger.error(
                f"Error adding CVE channel {channel.id} for guild {guild_id}: {e}",
                exc_info=True,
            )
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while adding the CVE monitoring channel.",
                    ephemeral=True,
                )

    @channels_group.command(
        name="remove",
        description="Remove/Disable automatic CVE monitoring for a specific channel.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(channel="The channel to remove from CVE monitoring.")
    async def channels_remove_command(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        """Removes a channel from the CVE monitoring list."""
        if not self.db or not interaction.guild_id:
            await interaction.response.send_message(
                "❌ Bot error: Cannot access database or Guild ID.", ephemeral=True
            )
            return

        guild_id = interaction.guild_id
        try:
            # Call the correct DB function to *delete* the channel config entry
            self.db.remove_cve_channel(guild_id, channel.id)
            # Since remove_cve_channel doesn't return status,
            # we assume success if no exception was raised.
            # The function in db_utils might need adjustment if it should indicate if a row was actually deleted.

            logger.info(
                f"User {interaction.user} removed channel {channel.id} from CVE monitoring in guild {guild_id}."
            )
            # Update response message to reflect removal
            await interaction.response.send_message(
                f"✅ Automatic CVE monitoring configuration **removed** for channel {channel.mention}.",
                ephemeral=True,
            )

        except sqlite3.Error as db_err:  # Catch specific DB errors if possible
            logger.error(
                f"Database error removing CVE channel {channel.id} for guild {guild_id}: {db_err}",
                exc_info=True,
            )
            await interaction.response.send_message(
                "❌ A database error occurred while removing the CVE monitoring channel.",
                ephemeral=True,
            )
        except Exception as e:
            logger.error(
                f"Unexpected error removing CVE channel {channel.id} for guild {guild_id}: {e}",
                exc_info=True,
            )
            await interaction.response.send_message(
                "❌ An unexpected error occurred while removing the CVE monitoring channel.",
                ephemeral=True,
            )

    @channels_group.command(
        name="list",
        description="Show global CVE monitoring status and list configured channels.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def channels_status_command(self, interaction: discord.Interaction):
        """Shows global status and lists channels configured for CVE monitoring."""
        if not self.db or not interaction.guild_id:
            await interaction.response.send_message(
                "❌ Bot error: Cannot access database or Guild ID.", ephemeral=True
            )
            return

        guild_id = interaction.guild_id
        try:
            guild_config = self.db.get_cve_guild_config(guild_id)
            # Use the correct field 'cve_monitoring_enabled' from updated schema
            globally_enabled = (
                guild_config.get("cve_monitoring_enabled", False)
                if guild_config
                else False
            )
            status_text = "**enabled**" if globally_enabled else "**disabled**"
            global_message = f"ℹ️ Global automatic CVE monitoring is currently {status_text} for this server."

            channel_configs = self.db.get_all_cve_channel_configs_for_guild(guild_id)
            enabled_channels = []
            if channel_configs:
                for config in channel_configs:
                    if config.get("enabled", False):
                        channel_id = config.get("channel_id")  # Get the ID
                        mention_or_id = f"ID: {channel_id} (Unknown)"  # Default text

                        if isinstance(channel_id, int):
                            channel = self.bot.get_channel(channel_id)
                            if channel is not None:
                                # If it's a TextChannel, use mention
                                if isinstance(channel, discord.TextChannel):
                                    mention_or_id = channel.mention
                                # Otherwise, try to get name, fallback to ID
                                else:
                                    channel_name = getattr(
                                        channel, "name", f"ID: {channel_id}"
                                    )
                                    mention_or_id = f"{channel_name} (Non-Text Channel)"
                            else:
                                # Channel not found by ID
                                mention_or_id = f"ID: {channel_id} (Not Found)"
                        else:
                            # ID wasn't an int in the first place
                            logger.warning(
                                f"Invalid channel ID type ({type(channel_id)}) found in config for guild {guild_id}: {channel_id}"
                            )
                            mention_or_id = f"ID: {channel_id} (Invalid Type)"

                        enabled_channels.append(mention_or_id)

            if not enabled_channels:
                channel_message = (
                    "No specific channels are currently configured and enabled."
                )
                if globally_enabled:
                    channel_message += " Use `/cve channels add` to add one."
            else:
                channel_message = (
                    f"Configured channels:\n- {'\\n- '.join(enabled_channels)}"
                )

            full_message = f"{global_message}\n\n{channel_message}"
            await interaction.response.send_message(full_message, ephemeral=True)

        except Exception as e:
            logger.error(
                f"Error fetching CVE channel status for guild {guild_id}: {e}",
                exc_info=True,
            )
            await interaction.response.send_message(
                "❌ An error occurred while fetching the channel status.",
                ephemeral=True,
            )

    @channels_group.command(
        name="disable_global",
        description="Disable automatic CVE message scanning globally for this server.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def channels_disable_global_command(self, interaction: discord.Interaction):
        if not self.db or not interaction.guild_id:
            await interaction.response.send_message(
                "❌ Bot error: Cannot access database or Guild ID.", ephemeral=True
            )
            return

        guild_id = interaction.guild_id
        # Ensure global config exists first
        if not await self._ensure_guild_config(guild_id):
            await interaction.response.send_message(
                "❌ Bot error: Could not ensure guild configuration.", ephemeral=True
            )
            return

        try:
            # Update the global enabled status to False using correct field name
            self.db.update_cve_guild_enabled(guild_id, False)
            logger.info(
                f"User {interaction.user} disabled global CVE monitoring for guild {guild_id}."
            )
            await interaction.response.send_message(
                "❌ Global automatic CVE monitoring **disabled** for this server. The bot will no longer scan messages for CVEs.",
                ephemeral=True,
            )
        except Exception as e:
            logger.error(
                f"Error disabling global CVE monitoring for guild {guild_id}: {e}",
                exc_info=True,
            )
            await interaction.response.send_message(
                "❌ An error occurred while disabling global CVE monitoring.",
                ephemeral=True,
            )

    @channels_group.command(
        name="enable_global",
        description="Enable automatic CVE message scanning globally (channels still need adding).",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def channels_enable_global_command(self, interaction: discord.Interaction):
        if not self.db or not interaction.guild_id:
            await interaction.response.send_message(
                "❌ Bot error: Cannot access database or Guild ID.", ephemeral=True
            )
            return

        guild_id = interaction.guild_id
        # Ensure global config exists first
        if not await self._ensure_guild_config(guild_id):
            await interaction.response.send_message(
                "❌ Bot error: Could not ensure guild configuration.", ephemeral=True
            )
            return

        try:
            # Update the global enabled status to True using correct field name
            self.db.update_cve_guild_enabled(guild_id, True)
            logger.info(
                f"User {interaction.user} enabled global CVE monitoring for guild {guild_id}."
            )
            await interaction.response.send_message(
                "✅ Global automatic CVE monitoring **enabled** for this server. Use `/cve channels add` to specify which channels to monitor.",
                ephemeral=True,
            )
        except Exception as e:
            logger.error(
                f"Error enabling global CVE monitoring for guild {guild_id}: {e}",
                exc_info=True,
            )
            await interaction.response.send_message(
                "❌ An error occurred while enabling global CVE monitoring.",
                ephemeral=True,
            )

    # --- Commands under the top-level /verbose Group ---

    @verbose_group.command(
        name="enable_global",
        description="Enable detailed CVE alerts globally for the server.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def verbose_enable_global_command(self, interaction: discord.Interaction):
        if not self.db or not interaction.guild_id:
            await interaction.response.send_message(
                "❌ Bot error: Cannot access database or guild ID.", ephemeral=True
            )
            return
        try:
            self.db.update_cve_guild_verbose_mode(interaction.guild_id, True)
            await interaction.response.send_message(
                "✅ Global verbose CVE alerts **enabled**. Specific channel settings may override this.",
                ephemeral=True,
            )
        except Exception as e:
            logger.error(
                f"Error enabling global verbose CVE alerts for guild {interaction.guild_id}: {e}",
                exc_info=True,
            )
            await interaction.response.send_message(
                "❌ An error occurred while enabling global verbose alerts.",
                ephemeral=True,
            )

    @verbose_group.command(
        name="disable_global",
        description="Disable detailed CVE alerts globally (uses standard format).",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def verbose_disable_global_command(self, interaction: discord.Interaction):
        if not self.db or not interaction.guild_id:
            await interaction.response.send_message(
                "❌ Bot error: Cannot access database or guild ID.", ephemeral=True
            )
            return
        try:
            self.db.update_cve_guild_verbose_mode(interaction.guild_id, False)
            await interaction.response.send_message(
                "✅ Global verbose CVE alerts **disabled**. Standard format will be used (unless channels override).",
                ephemeral=True,
            )
        except Exception as e:
            logger.error(
                f"Error disabling global verbose CVE alerts for guild {interaction.guild_id}: {e}",
                exc_info=True,
            )
            await interaction.response.send_message(
                "❌ An error occurred while disabling global verbose alerts.",
                ephemeral=True,
            )

    @verbose_group.command(
        name="set", description="Set verbosity override for a specific channel."
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        channel="The channel to configure.",
        verbosity="Whether to use verbose (True) or standard (False) alerts.",
    )
    async def verbose_channel_set_command(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        verbosity: bool,
    ):
        if not self.db or not interaction.guild_id:
            await interaction.response.send_message(
                "❌ Bot error: Cannot access database or guild ID.", ephemeral=True
            )
            return
        try:
            if not self.db.get_cve_guild_config(interaction.guild_id):
                self.db.set_cve_guild_config(
                    interaction.guild_id,
                    enabled=True,
                    verbose_mode=False,
                    severity_threshold="all",
                )
                logger.info(
                    f"Initialized default CVE guild config for {interaction.guild_id} while setting channel verbosity."
                )

            self.db.set_channel_verbosity(interaction.guild_id, channel.id, verbosity)
            status_text = "verbose" if verbosity else "standard (non-verbose)"
            await interaction.response.send_message(
                f"✅ Verbosity for {channel.mention} set to **{status_text}**. This overrides the global setting.",
                ephemeral=True,
            )
        except Exception as e:
            logger.error(
                f"Error setting channel verbosity for {channel.id} in guild {interaction.guild_id}: {e}",
                exc_info=True,
            )
            await interaction.response.send_message(
                "❌ An error occurred while setting channel verbosity.", ephemeral=True
            )

    @verbose_group.command(
        name="unset",
        description="Remove verbosity override for a channel (uses global setting).",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(channel="The channel to reset to global verbosity setting.")
    async def verbose_channel_unset_command(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        if not self.db or not interaction.guild_id:
            await interaction.response.send_message(
                "❌ Bot error: Cannot access database or guild ID.", ephemeral=True
            )
            return
        try:
            self.db.set_channel_verbosity(interaction.guild_id, channel.id, None)
            await interaction.response.send_message(
                f"✅ Verbosity override for {channel.mention} **removed**. It will now use the global server setting.",
                ephemeral=True,
            )
        except Exception as e:
            logger.error(
                f"Error unsetting channel verbosity for {channel.id} in guild {interaction.guild_id}: {e}",
                exc_info=True,
            )
            await interaction.response.send_message(
                "❌ An error occurred while unsetting channel verbosity.",
                ephemeral=True,
            )

    @verbose_group.command(
        name="setall",
        description="Set verbosity override for ALL configured channels in this server.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        verbosity="Set all channels to verbose (True) or standard (False)."
    )
    async def verbose_channel_setall_command(
        self, interaction: discord.Interaction, verbosity: bool
    ):
        if not self.db or not interaction.guild_id:
            await interaction.response.send_message(
                "❌ Bot error: Cannot access database or guild ID.", ephemeral=True
            )
            return
        try:
            self.db.set_all_channel_verbosity(interaction.guild_id, verbosity)
            status_text = "verbose" if verbosity else "standard (non-verbose)"
            await interaction.response.send_message(
                f"✅ Verbosity override for **all configured channels** set to **{status_text}**. This may differ from the global setting.",
                ephemeral=True,
            )
        except Exception as e:
            logger.error(
                f"Error setting all channel verbosity for guild {interaction.guild_id}: {e}",
                exc_info=True,
            )
            await interaction.response.send_message(
                "❌ An error occurred while setting verbosity for all channels.",
                ephemeral=True,
            )

    @verbose_group.command(
        name="status", description="Show global and per-channel verbosity status."
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(channel="(Optional) Specific channel to check status for.")
    async def verbose_channel_status_command(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not self.db or not interaction.guild_id:
            await interaction.response.send_message(
                "❌ Bot error: Cannot access database or guild ID.", ephemeral=True
            )
            return

        guild_id = interaction.guild_id
        guild_config = self.db.get_cve_guild_config(guild_id)
        global_verbose = (
            guild_config.get("verbose_mode", False) if guild_config else False
        )
        global_status_text = "Verbose" if global_verbose else "Standard (Non-Verbose)"

        embed = discord.Embed(
            title="CVE Alert Verbosity Status", color=discord.Color.blurple()
        )
        embed.description = (
            f"Global Setting: **{global_status_text}**\n\n"  # Double newline
        )

        if channel:
            channel_config = self.db.get_cve_channel_config(guild_id, channel.id)
            override = channel_config.get("verbose_mode") if channel_config else None
            if override is None:
                status = f"Inheriting Global ({global_status_text})"
            else:
                status = "Verbose (Override)" if override else "Standard (Override)"
            embed.add_field(name=f"#{channel.name}", value=status, inline=False)
        else:
            all_channel_configs = self.db.get_all_cve_channel_configs_for_guild(
                guild_id
            )
            override_count = 0
            status_lines = []
            if all_channel_configs:
                for config in all_channel_configs:
                    chan_id = config.get("channel_id")
                    override = config.get("verbose_mode")
                    chan_obj = self.bot.get_channel(chan_id)
                    chan_name = f"#{chan_obj.name}" if chan_obj else f"ID: {chan_id}"
                    if override is not None:
                        override_count += 1
                        status = "**Verbose**" if override else "**Standard**"
                        status_lines.append(f"{chan_name}: {status} (Override)")

            if override_count > 0:
                embed.add_field(
                    name="Channel Overrides",
                    value="\n".join(status_lines),
                    inline=False,
                )
            else:
                embed.add_field(
                    name="Channel Overrides",
                    value="No channels have specific verbosity overrides. All are using the global setting.",
                    inline=False,
                )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- End /verbose Group ---

    # --- /cve threshold Group ---
    threshold_group = app_commands.Group(
        name="threshold",
        description="Configure minimum severity for automatic CVE alerts.",
        parent=cve_group,
        guild_only=True,
    )

    @threshold_group.command(
        name="set",
        description="Set the GLOBAL minimum severity level for automatic CVE alerts.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        level="The minimum severity level (critical, high, medium, low, all)"
    )
    @app_commands.choices(
        level=[
            app_commands.Choice(name="Critical", value="critical"),
            app_commands.Choice(name="High", value="high"),
            app_commands.Choice(name="Medium", value="medium"),
            app_commands.Choice(name="Low", value="low"),
            app_commands.Choice(name="All (Default)", value="all"),
        ]
    )
    async def threshold_set_command(
        self, interaction: discord.Interaction, level: SeverityLevelChoices
    ):
        if not self.db or not interaction.guild_id:
            await interaction.response.send_message(
                "❌ Bot error: Cannot access database or Guild ID.", ephemeral=True
            )
            return

        guild_id = interaction.guild_id
        # Ensure global config exists first
        if not await self._ensure_guild_config(guild_id):
            await interaction.response.send_message(
                "❌ Bot error: Could not ensure guild configuration.", ephemeral=True
            )
            return

        try:
            # Use the correct DB method for global threshold
            self.db.update_cve_guild_severity_threshold(guild_id, level)
            logger.info(
                f"User {interaction.user} set global CVE severity threshold to {level} for guild {guild_id}."
            )
            await interaction.response.send_message(
                f"✅ Global CVE alert severity threshold set to **{level}**.",
                ephemeral=True,
            )
        except Exception as e:
            logger.error(
                f"Error setting global CVE threshold for guild {guild_id}: {e}",
                exc_info=True,
            )
            await interaction.response.send_message(
                "❌ An error occurred while setting the severity threshold.",
                ephemeral=True,
            )

    @threshold_group.command(
        name="view",
        description="View the current GLOBAL minimum severity level for CVE alerts.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def threshold_view_command(self, interaction: discord.Interaction):
        if not self.db or not interaction.guild_id:
            await interaction.response.send_message(
                "❌ Bot error: Cannot access database or Guild ID.", ephemeral=True
            )
            return

        guild_id = interaction.guild_id
        guild_config = self.db.get_cve_guild_config(guild_id)
        # Use the correct field 'cve_severity_threshold'
        current_threshold = (
            guild_config.get("cve_severity_threshold", "all") if guild_config else "all"
        )
        await interaction.response.send_message(
            f"ℹ️ Current global CVE alert severity threshold is **{current_threshold}**.",
            ephemeral=True,
        )

    @threshold_group.command(
        name="reset",
        description="Reset the GLOBAL CVE alert severity threshold to default ('all').",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def threshold_reset_command(self, interaction: discord.Interaction):
        if not self.db or not interaction.guild_id:
            await interaction.response.send_message(
                "❌ Bot error: Cannot access database or Guild ID.", ephemeral=True
            )
            return

        guild_id = interaction.guild_id
        # Ensure global config exists first
        if not await self._ensure_guild_config(guild_id):
            await interaction.response.send_message(
                "❌ Bot error: Could not ensure guild configuration.", ephemeral=True
            )
            return

        try:
            # Use the correct DB method for global threshold, setting to 'all'
            self.db.update_cve_guild_severity_threshold(guild_id, "all")
            logger.info(
                f"User {interaction.user} reset global CVE severity threshold for guild {guild_id}."
            )
            await interaction.response.send_message(
                "✅ Global CVE alert severity threshold reset to **all**.",
                ephemeral=True,
            )
        except Exception as e:
            logger.error(
                f"Error resetting global CVE threshold for guild {guild_id}: {e}",
                exc_info=True,
            )
            await interaction.response.send_message(
                "❌ An error occurred while resetting the severity threshold.",
                ephemeral=True,
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Processes messages to automatically detect and look up CVE IDs."""
        # 1. Initial Checks
        if message.author.bot:  # Ignore bots
            return
        if not message.guild:  # Ignore DMs
            return
        if not self.db or not self.nvd_client:  # Ensure DB and NVD client are available
            if not hasattr(self, "_logged_missing_dependency_warn"):
                logger.warning(
                    "CVE automatic detection skipped: DB or NVDClient not available in CVELookupCog."
                )
                self._logged_missing_dependency_warn = True
            return
        if not message.content:  # Ignore messages without text content
            return

        # 2. Check Guild Enablement & Get Global Config
        guild_id = message.guild.id
        guild_config = self.db.get_cve_guild_config(guild_id)
        if not guild_config or not guild_config.get("cve_monitoring_enabled", False):
            return  # Global setting is disabled

        # 3. Check Channel Configuration / Filtering
        channel_configs = self.db.get_all_cve_channel_configs_for_guild(guild_id)
        if channel_configs:  # If specific channels ARE configured...
            is_channel_configured_and_enabled = False
            for conf in channel_configs:
                # Check if it's the current channel AND explicitly enabled
                if conf.get("channel_id") == message.channel.id and conf.get(
                    "enabled", False
                ):
                    is_channel_configured_and_enabled = True
                    break
            if not is_channel_configured_and_enabled:
                return  # This channel is not in the allowed list or is disabled
        # If channel_configs is empty, it means monitoring is global (all channels allowed)

        # 4. Find CVEs in Message
        found_cves_set = {
            match.upper() for match in CVE_SCAN_REGEX.findall(message.content)
        }
        cves_to_process = list(found_cves_set)[:MAX_CVES_PER_MESSAGE]

        if not cves_to_process:
            return

        logger.info(
            f"Detected CVEs {cves_to_process} in message {message.id} (channel {message.channel.id}, guild {guild_id})."
        )

        # Get global severity threshold ONCE before the loop
        global_threshold_str = guild_config.get("cve_severity_threshold", "all")
        min_score_threshold = {
            "critical": 9.0,
            "high": 7.0,
            "medium": 4.0,
            "low": 0.1,
            "all": 0.0,
        }.get(global_threshold_str, 0.0)
        logger.debug(
            f"Guild {guild_id} severity threshold: {global_threshold_str} (Score >= {min_score_threshold})"
        )

        # 5. Process Found CVEs
        processed_count = 0
        for cve_id in cves_to_process:
            if not CVE_VALIDATE_REGEX.match(cve_id):
                logger.warning(
                    f"Skipping invalid CVE format found by SCAN_REGEX: {cve_id}"
                )
                continue

            try:
                async with self.bot.stats_lock:
                    self.bot.stats_cve_lookups += 1

                cve_details = await self.nvd_client.get_cve_details(cve_id)

                if cve_details:
                    async with self.bot.stats_lock:
                        self.bot.stats_nvd_fallback_success += 1

                    # --- Severity Threshold Check ---
                    cve_score = cve_details.get("cvss")
                    if isinstance(cve_score, (int, float)):
                        if cve_score < min_score_threshold:
                            logger.debug(
                                f"Skipping CVE {cve_id} (Score: {cve_score}) due to guild threshold ({global_threshold_str} >= {min_score_threshold})"
                            )
                            continue  # Skip this CVE
                    elif global_threshold_str != "all":
                        logger.warning(
                            f"Skipping CVE {cve_id}: Severity score missing, cannot compare against threshold '{global_threshold_str}'."
                        )
                        continue
                    # --- End Severity Threshold Check ---

                    is_kev = False
                    kev_entry_data = None
                    if self.bot.cisa_kev_client:
                        try:
                            kev_entry_data = (
                                await self.bot.cisa_kev_client.get_kev_entry(cve_id)
                            )
                            if kev_entry_data:
                                is_kev = True
                                logger.info(
                                    f"CVE {cve_id} found in KEV catalog during automatic scan."
                                )
                        except Exception as kev_err:
                            logger.error(
                                f"Error checking KEV status for {cve_id}: {kev_err}",
                                exc_info=True,
                            )

                    verbose = self.db.get_effective_verbosity(
                        guild_id, message.channel.id
                    )
                    embed = self.create_cve_embed(cve_details, verbose=verbose)
                    await message.channel.send(embed=embed)
                    await asyncio.sleep(1.5)  # Add delay after sending CVE embed

                    if is_kev and kev_entry_data:
                        kev_embed = self.create_kev_embed(
                            cve_id, kev_entry_data, verbose=verbose
                        )
                        await message.channel.send(embed=kev_embed)
                        await asyncio.sleep(1.5)  # Add delay after sending KEV embed

                    processed_count += 1
                else:
                    logger.debug(
                        f"No details found for CVE {cve_id} during automatic scan."
                    )

            except Exception as e:
                logger.error(
                    f"Error processing CVE {cve_id} from message {message.id}: {e}",
                    exc_info=True,
                )
                async with self.bot.stats_lock:
                    self.bot.stats_api_errors_nvd += 1

        if len(found_cves_set) > MAX_CVES_PER_MESSAGE:
            try:
                await message.channel.send(
                    f"ℹ️ Found {len(found_cves_set)} unique CVEs, showing details for the first {MAX_CVES_PER_MESSAGE}.",
                    delete_after=30,
                )
            except discord.HTTPException:
                pass
        elif processed_count > 0:
            logger.info(
                f"Successfully processed {processed_count} CVE(s) automatically from message {message.id}."
            )

    # --- Error Handler for Cog ---
    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "🚫 You need the 'Manage Server' permission to use this command.",
                ephemeral=True,
            )
        # Add other specific error handling if needed
        else:
            # Log the error if it wasn't handled
            logger.error(
                f"Unhandled error in CVELookupCog command '{interaction.command.qualified_name if interaction.command else 'unknown'}': {error}",
                exc_info=error,
            )
            # Inform the user generically
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An unexpected error occurred processing this command.",
                    ephemeral=True,
                )
            else:
                # May need followup if response already sent (e.g., deferred)
                try:
                    await interaction.followup.send(
                        "❌ An unexpected error occurred processing this command.",
                        ephemeral=True,
                    )
                except discord.HTTPException:
                    logger.error(
                        f"Failed to send followup error message for command {interaction.command.qualified_name if interaction.command else 'unknown'}"
                    )


async def setup(bot: "SecurityBot"):
    """Sets up the CVE Lookup Cog."""
    # Keep NVD client check, but also check for DB
    if not bot.nvd_client:
        logger.warning(
            "NVDClient not initialized. CVE Lookup Cog features requiring NVD will be limited."
        )
    if not bot.db:
        logger.error(
            "KEVConfigDB (Database) not initialized. CVE Lookup Cog cannot be loaded."
        )
        return  # Don't load cog if DB isn't ready

    await bot.add_cog(CVELookupCog(bot))
    logger.info("CVE Lookup Cog loaded.")
