"""
Network filtering for Agentum sandbox.

Provides DNS-based domain filtering by generating /etc/hosts entries
that redirect blocked domains to localhost. This prevents the sandboxed
agent from accessing unauthorized external resources.

Usage:
    from src.core.network_filter import (
        load_network_config,
        generate_hosts_entries,
        apply_hosts_filter,
    )
    
    config = load_network_config()
    entries = generate_hosts_entries(config)
    apply_hosts_filter(entries)
"""
from __future__ import annotations

import logging
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class NetworkConfig:
    """Network filtering configuration."""
    
    mode: str = "whitelist"  # "whitelist" or "blacklist"
    allowed_domains: list[str] | None = None
    blocked_domains: list[str] | None = None
    allow_localhost: bool = True
    
    def __post_init__(self) -> None:
        if self.allowed_domains is None:
            self.allowed_domains = []
        if self.blocked_domains is None:
            self.blocked_domains = []


def load_network_config(config_path: Optional[Path] = None) -> NetworkConfig:
    """
    Load network configuration from security.yaml.
    
    Args:
        config_path: Path to security.yaml. If None, uses default location.
        
    Returns:
        NetworkConfig with filtering settings.
    """
    if config_path is None:
        from ..config import CONFIG_DIR
        config_path = CONFIG_DIR / "security.yaml"
    
    if not config_path.exists():
        logger.warning(f"Security config not found at {config_path}, using defaults")
        return NetworkConfig()
    
    try:
        with open(config_path, "r") as f:
            data = yaml.safe_load(f)
        
        network_data = data.get("network", {})
        return NetworkConfig(
            mode=network_data.get("mode", "whitelist"),
            allowed_domains=network_data.get("allowed_domains", []),
            blocked_domains=network_data.get("blocked_domains", []),
            allow_localhost=network_data.get("allow_localhost", True),
        )
    except Exception as e:
        logger.error(f"Failed to load network config: {e}")
        return NetworkConfig()


def resolve_domain(domain: str) -> list[str]:
    """
    Resolve a domain to its IP addresses.
    
    Args:
        domain: Domain name to resolve.
        
    Returns:
        List of IP addresses.
    """
    try:
        # Get all IP addresses for the domain
        addr_info = socket.getaddrinfo(domain, None, socket.AF_UNSPEC)
        ips = list(set(info[4][0] for info in addr_info))
        return ips
    except socket.gaierror as e:
        logger.warning(f"Failed to resolve domain {domain}: {e}")
        return []


def generate_blocked_hosts_entries(blocked_domains: list[str]) -> str:
    """
    Generate /etc/hosts entries to block domains.
    
    Blocked domains are redirected to 127.0.0.1, preventing connections.
    
    Args:
        blocked_domains: List of domains to block.
        
    Returns:
        String with hosts file entries.
    """
    if not blocked_domains:
        return ""
    
    lines = [
        "# Agentum network filter - blocked domains",
        "# These domains are redirected to localhost to prevent access",
    ]
    
    for domain in blocked_domains:
        # Block both with and without www prefix
        lines.append(f"127.0.0.1 {domain}")
        if not domain.startswith("www."):
            lines.append(f"127.0.0.1 www.{domain}")
    
    return "\n".join(lines)


def generate_hosts_entries(config: NetworkConfig) -> str:
    """
    Generate /etc/hosts entries based on network configuration.
    
    In whitelist mode: All domains except allowed ones are blocked.
    In blacklist mode: Only explicitly blocked domains are blocked.
    
    Args:
        config: Network configuration.
        
    Returns:
        String with hosts file entries to append.
    """
    if config.mode == "blacklist":
        # Block only explicitly listed domains
        return generate_blocked_hosts_entries(config.blocked_domains or [])
    
    # Whitelist mode is more complex:
    # We can't easily block "all other domains" via /etc/hosts
    # Instead, we rely on the sandbox + iptables for comprehensive blocking
    # Here we just document what's allowed
    
    lines = [
        "# Agentum network filter - whitelist mode",
        "# Allowed domains:",
    ]
    for domain in config.allowed_domains or []:
        lines.append(f"# ALLOW: {domain}")
    
    lines.append("#")
    lines.append("# Note: In whitelist mode, network restrictions are enforced")
    lines.append("# at the container level via iptables or network policies.")
    
    return "\n".join(lines)


def apply_hosts_filter(
    entries: str,
    hosts_path: Path = Path("/etc/hosts"),
    backup: bool = True,
) -> bool:
    """
    Apply hosts filter entries to /etc/hosts.
    
    This appends the filter entries to the hosts file. Requires root
    or appropriate permissions to modify /etc/hosts.
    
    Args:
        entries: Hosts entries to append.
        hosts_path: Path to hosts file (default: /etc/hosts).
        backup: Whether to create a backup before modifying.
        
    Returns:
        True if successful, False otherwise.
    """
    if not entries.strip():
        logger.info("No hosts entries to apply")
        return True
    
    try:
        # Read current hosts file
        current_content = ""
        if hosts_path.exists():
            current_content = hosts_path.read_text()
        
        # Check if our entries are already present
        if "# Agentum network filter" in current_content:
            logger.info("Agentum network filter already applied")
            return True
        
        # Create backup if requested
        if backup and hosts_path.exists():
            backup_path = hosts_path.with_suffix(".backup")
            backup_path.write_text(current_content)
            logger.info(f"Created hosts backup at {backup_path}")
        
        # Append our entries
        new_content = current_content.rstrip() + "\n\n" + entries + "\n"
        hosts_path.write_text(new_content)
        
        logger.info(f"Applied network filter to {hosts_path}")
        return True
        
    except PermissionError:
        logger.error(f"Permission denied writing to {hosts_path}")
        return False
    except Exception as e:
        logger.error(f"Failed to apply hosts filter: {e}")
        return False


def get_allowed_ips(config: NetworkConfig) -> list[str]:
    """
    Get list of allowed IP addresses by resolving allowed domains.
    
    This can be used to generate iptables rules for whitelist mode.
    
    Args:
        config: Network configuration.
        
    Returns:
        List of allowed IP addresses.
    """
    allowed_ips: set[str] = set()
    
    if config.allow_localhost:
        allowed_ips.add("127.0.0.1")
        allowed_ips.add("::1")
    
    for domain in config.allowed_domains or []:
        ips = resolve_domain(domain)
        allowed_ips.update(ips)
    
    return sorted(allowed_ips)


def generate_iptables_rules(config: NetworkConfig) -> list[str]:
    """
    Generate iptables rules for network filtering.
    
    In whitelist mode, generates rules to:
    1. Allow established connections
    2. Allow localhost
    3. Allow connections to whitelisted IPs
    4. Drop everything else
    
    Args:
        config: Network configuration.
        
    Returns:
        List of iptables command strings.
    """
    rules: list[str] = []
    
    if config.mode != "whitelist":
        # In blacklist mode, we don't need iptables
        # DNS blocking via /etc/hosts is sufficient
        return rules
    
    # Allow established connections
    rules.append("iptables -A OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT")
    
    # Allow localhost
    if config.allow_localhost:
        rules.append("iptables -A OUTPUT -o lo -j ACCEPT")
        rules.append("iptables -A OUTPUT -d 127.0.0.0/8 -j ACCEPT")
    
    # Allow DNS (needed for domain resolution)
    rules.append("iptables -A OUTPUT -p udp --dport 53 -j ACCEPT")
    rules.append("iptables -A OUTPUT -p tcp --dport 53 -j ACCEPT")
    
    # Allow whitelisted IPs
    allowed_ips = get_allowed_ips(config)
    for ip in allowed_ips:
        if ":" in ip:  # IPv6
            continue  # Handle with ip6tables separately
        rules.append(f"iptables -A OUTPUT -d {ip} -j ACCEPT")
    
    # Drop everything else
    rules.append("iptables -A OUTPUT -j DROP")
    
    return rules


def setup_network_filter(config_path: Optional[Path] = None) -> bool:
    """
    Set up network filtering based on configuration.
    
    This is the main entry point for network filter setup.
    Should be called at container startup.
    
    Args:
        config_path: Optional path to security.yaml.
        
    Returns:
        True if setup succeeded, False otherwise.
    """
    config = load_network_config(config_path)
    
    logger.info(f"Setting up network filter (mode={config.mode})")
    
    # Generate and apply hosts entries
    entries = generate_hosts_entries(config)
    if not apply_hosts_filter(entries):
        logger.warning("Failed to apply hosts filter, continuing anyway")
    
    # Log iptables rules (actual application requires privileges)
    if config.mode == "whitelist":
        rules = generate_iptables_rules(config)
        logger.info(f"Generated {len(rules)} iptables rules for whitelist mode")
        for rule in rules:
            logger.debug(f"  {rule}")
    
    return True
